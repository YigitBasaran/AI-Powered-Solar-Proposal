"""Satellite imagery.

The image is always served from this origin, never fetched by the browser from
Google directly. That keeps the API key server-side and, just as importantly,
keeps the Konva canvas same-origin so the completed layout can be exported to
PNG without tainting.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, Response

from app.core.config import Settings, get_settings
from app.core.errors import AppError, MapsUnavailableError
from app.domain.imagery import request_signature, verify_imagery
from app.services.roof import calibration_metadata, load_calibration

logger = logging.getLogger("solarvis.maps")

router = APIRouter(prefix="/maps", tags=["maps"])

#: The canonical origin. Only a request to *this* host needs an API key; the
#: test stub does not have one and must not be made to invent one.
GOOGLE_STATIC_HOST = "maps.googleapis.com"
MIN_IMAGE_BYTES = 2048


def _is_canonical(url: str) -> bool:
    return urlparse(url).hostname == GOOGLE_STATIC_HOST


def _calibration_expectations(settings: Settings) -> dict[str, Any]:
    """What the roof calibration says it was traced against.

    Read defensively: a calibration that cannot be loaded must not take the
    map down with it. The map is still worth showing - it is *measurement*
    that has to stop, and that is decided from the verdict this feeds.
    """
    try:
        data = load_calibration(settings)
    except AppError:
        return {}
    return calibration_metadata(data)


@router.get("/config")
async def satellite_config(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """Everything the client needs to place overlays, and nothing it must guess.

    Ground resolution is published here so the frontend never derives scale
    from the rendered image's dimensions.
    """
    cfg = settings.satellite_image_config
    meta = _calibration_expectations(settings)

    return {
        "center": {"latitude": cfg.center_latitude, "longitude": cfg.center_longitude},
        "zoom": cfg.zoom,
        "scale": cfg.scale,
        "requestedSize": f"{cfg.requested_width_px}x{cfg.requested_height_px}",
        "sourceWidthPx": cfg.source_width_px,
        "sourceHeightPx": cfg.source_height_px,
        "groundMetresPerSourcePixel": cfg.ground_m_per_source_px,
        "groundSpanM": round(cfg.ground_span_m, 3),
        "attribution": "Map data (c) Google",
        # The signature the calibration was traced against, and the one in
        # force. Published so the client can say *why* measurement is
        # unavailable rather than simply omitting it.
        "requestSignature": request_signature(cfg),
        "calibrationSignature": meta.get("request_signature"),
        "calibrationTracedOn": meta.get("traced_on"),
        # Cache-busted on the signature. The URL used to be identical in
        # every configuration, so a browser could serve an hour-old raster
        # from a different one - which both masks and mimics a misalignment.
        "imageUrl": f"/api/v1/maps/satellite?v={request_signature(cfg)[:12]}",
    }


def build_request_params(settings: Settings) -> dict[str, str | int]:
    """Exactly what is sent to Google, derived from the verified config.

    Separated so a test can assert the parameters without performing a
    request, and so the key is added in exactly one place. The key is never
    logged and never returned to a client.
    """
    cfg = settings.satellite_image_config
    params: dict[str, str | int] = {
        "center": f"{cfg.center_latitude},{cfg.center_longitude}",
        "zoom": cfg.zoom,
        "size": f"{cfg.requested_width_px}x{cfg.requested_height_px}",
        "scale": cfg.scale,
        "maptype": cfg.map_type,
    }
    if settings.google_maps_api_key:
        params["key"] = settings.google_maps_api_key
    return params


@router.get("/satellite")
async def satellite(settings: Settings = Depends(get_settings)) -> Response:
    """The satellite raster, always fetched over HTTP.

    There is no fixture branch. An offline deployment cannot show imagery,
    and an offline *test* points `GOOGLE_STATIC_MAPS_BASE_URL` at a local
    stub - the same seam PVGIS uses. Substituting a committed raster when the
    request fails would put a correct-looking overlay on the wrong picture,
    which is the exact failure this change exists to remove.
    """
    url = settings.google_static_maps_base_url

    if _is_canonical(url) and not settings.google_maps_api_key:
        raise MapsUnavailableError(
            "GOOGLE_MAPS_API_KEY is not set, so Google Static Maps cannot be called.",
            details={"hint": "Set the key, or point GOOGLE_STATIC_MAPS_BASE_URL at a stub."},
        )

    params = build_request_params(settings)

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, params=params, timeout=15.0)
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

    # Is this the imagery the roof was traced on? Reported rather than
    # enforced here: the picture is still worth showing, and it is the
    # *measurement* that is withheld when the answer is no.
    meta = _calibration_expectations(settings)
    expected = (meta.get("imagery") or {}).get("perceptual_hash")
    try:
        verdict = verify_imagery(response.content, expected_hash=expected)
    except (OSError, ValueError) as exc:
        # A body that claims `image/*` and does not decode is not a map. The
        # content-type check above cannot catch this: a truncated transfer keeps
        # the header and loses the pixels.
        raise MapsUnavailableError(
            f"The imagery response could not be decoded as an image: {exc}"
        ) from exc
    if not verdict.matches:
        logger.warning(
            "satellite imagery does not match the calibration: %s", verdict.reason
        )

    logger.info(
        "served satellite image (%d bytes, imagery %s)",
        len(response.content),
        "verified" if verdict.matches else "UNVERIFIED",
    )
    return Response(
        content=response.content,
        media_type=content_type,
        headers={
            # Long-lived, but the URL carries the signature, so a
            # configuration change is a different URL rather than a stale hit.
            "Cache-Control": "public, max-age=3600",
            "X-Image-Source": "live" if _is_canonical(url) else "stub",
            "X-Imagery-Verified": "true" if verdict.matches else "false",
        },
    )
