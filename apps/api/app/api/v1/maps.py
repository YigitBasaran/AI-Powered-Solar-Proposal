"""Satellite imagery.

The image is always served from this origin, never fetched by the browser from
Google directly. That keeps the API key server-side and, just as importantly,
keeps the Konva canvas same-origin so the completed layout can be exported to
PNG without tainting.
"""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Depends, Response

from app.core.config import MapsMode, Settings, get_settings
from app.core.errors import MapsUnavailableError

logger = logging.getLogger("solarvis.maps")

router = APIRouter(prefix="/maps", tags=["maps"])

GOOGLE_STATIC_URL = "https://maps.googleapis.com/maps/api/staticmap"
MIN_IMAGE_BYTES = 2048


@router.get("/config")
async def satellite_config(settings: Settings = Depends(get_settings)) -> dict:
    """Everything the client needs to place overlays, and nothing it must guess.

    Ground resolution is published here so the frontend never derives scale
    from the rendered image's dimensions.
    """
    cfg = settings.satellite_image_config
    fixture_meta = settings.fixtures_dir / "maps" / "satellite-fixture.json"

    attribution = "Map data (c) Google"
    provenance = None
    if settings.maps_mode is MapsMode.FIXTURE and fixture_meta.is_file():
        import json

        meta = json.loads(fixture_meta.read_text(encoding="utf-8"))
        attribution = meta["provenance"]["attribution"]
        provenance = meta["provenance"]

    return {
        "mode": settings.maps_mode.value,
        "isLive": settings.maps_mode is MapsMode.LIVE,
        "center": {"latitude": cfg.center_latitude, "longitude": cfg.center_longitude},
        "zoom": cfg.zoom,
        "scale": cfg.scale,
        "requestedSize": f"{cfg.requested_width_px}x{cfg.requested_height_px}",
        "sourceWidthPx": cfg.source_width_px,
        "sourceHeightPx": cfg.source_height_px,
        "groundMetresPerSourcePixel": cfg.ground_m_per_source_px,
        "groundSpanM": round(cfg.ground_span_m, 3),
        "attribution": attribution,
        "provenance": provenance,
        "imageUrl": "/api/v1/maps/satellite",
    }


@router.get("/satellite")
async def satellite(settings: Settings = Depends(get_settings)) -> Response:
    cfg = settings.satellite_image_config

    if settings.maps_mode is MapsMode.FIXTURE:
        path = settings.fixtures_dir / "maps" / "satellite-fixture.png"
        if not path.is_file():
            raise MapsUnavailableError("Satellite fixture is missing.", details={"path": str(path)})
        return Response(
            content=path.read_bytes(),
            media_type="image/png",
            headers={
                "Cache-Control": "public, max-age=3600",
                # Never let a fixture be mistaken for live imagery downstream.
                "X-Image-Source": "fixture",
            },
        )

    if not settings.google_maps_api_key:
        raise MapsUnavailableError(
            "MAPS_MODE=live but GOOGLE_MAPS_API_KEY is not set.",
            details={"hint": "Set the key, or use MAPS_MODE=fixture."},
        )

    params = {
        "center": f"{cfg.center_latitude},{cfg.center_longitude}",
        "zoom": cfg.zoom,
        "size": f"{cfg.requested_width_px}x{cfg.requested_height_px}",
        "scale": cfg.scale,
        "maptype": cfg.map_type,
        "key": settings.google_maps_api_key,
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(GOOGLE_STATIC_URL, params=params, timeout=15.0)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise MapsUnavailableError(
                f"Google Static Maps unreachable: {type(exc).__name__}"
            ) from exc

    if response.status_code != 200:
        raise MapsUnavailableError(
            f"Google Static Maps returned HTTP {response.status_code}",
            details={"status": response.status_code},
        )
    content_type = response.headers.get("content-type", "")
    if not content_type.startswith("image/"):
        raise MapsUnavailableError(f"Google returned content-type {content_type!r}")
    if len(response.content) < MIN_IMAGE_BYTES:
        raise MapsUnavailableError("Google returned a suspiciously small image")

    logger.info("served live satellite image (%d bytes)", len(response.content))
    return Response(
        content=response.content,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=3600", "X-Image-Source": "live"},
    )
