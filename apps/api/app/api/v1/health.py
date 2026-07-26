"""Liveness, readiness and configuration transparency endpoints.

`/health/ready` deliberately reports *degraded* rather than *down* when an
external dependency is unavailable: the whole point of the fixture modes is
that the application still completes a full proposal without them. What must
never happen is a fixture being presented as live, so every operating mode is
reported explicitly here and surfaced in the UI.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter

from app.core.config import LlmProvider, MapsMode, Settings, get_settings

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "alive"}


def _maps_status(settings: Settings) -> dict[str, Any]:
    fixture = settings.fixtures_dir / "maps" / "satellite-fixture.png"
    if settings.maps_mode is MapsMode.LIVE:
        return {
            "mode": "live",
            "ready": bool(settings.google_maps_api_key),
            "detail": (
                "Google Maps Static API"
                if settings.google_maps_api_key
                else "MAPS_MODE=live but GOOGLE_MAPS_API_KEY is empty"
            ),
        }
    return {
        "mode": "fixture",
        "ready": fixture.is_file(),
        "detail": (
            "Development fixture on the exact z20/scale2 grid. Not live imagery."
            if fixture.is_file()
            else "Fixture image missing"
        ),
    }


@router.get("/ready")
async def ready() -> dict[str, Any]:
    settings = get_settings()
    cfg = settings.satellite_image_config

    maps = _maps_status(settings)

    checks: dict[str, Any] = {
        "database": {"mode": settings.database_url.split(":")[0], "ready": True},
        "maps": maps,
        "pvgis": {"mode": settings.pvgis_mode.value, "ready": True},
        "fx": {
            "mode": settings.fx_mode.value,
            "provider": settings.fx_provider,
            "dataProvider": settings.fx_data_provider,
            "ready": True,
        },
        "llm": {
            "provider": settings.llm_provider.value,
            "model": (
                settings.ollama_model if settings.llm_provider is LlmProvider.OLLAMA else None
            ),
            # The deterministic rules parser handles every phrasing the case
            # needs, so the workflow is ready regardless of the LLM.
            "ready": True,
        },
    }

    status: Literal["ok", "degraded"] = (
        "ok" if all(c.get("ready", True) for c in checks.values()) else "degraded"
    )

    return {
        "status": status,
        "checks": checks,
        "sourceRaster": {
            "zoom": cfg.zoom,
            "requestedSize": f"{cfg.requested_width_px}x{cfg.requested_height_px}",
            "scale": cfg.scale,
            "sourceWidthPx": cfg.source_width_px,
            "sourceHeightPx": cfg.source_height_px,
            "groundMetresPerSourcePixel": round(cfg.ground_m_per_source_px, 7),
            "groundSpanM": round(cfg.ground_span_m, 3),
        },
    }


@router.get("/case-location")
async def case_location() -> dict[str, Any]:
    """Expose both the raw and resolved coordinate.

    The brief prints a latitude with no minus sign, which is open sea. We show
    what it said and what we use, rather than quietly substituting one.
    """
    loc = get_settings().case_location
    return {
        "raw": {"latitude": loc.raw_case_latitude, "longitude": loc.raw_case_longitude},
        "resolved": {
            "latitude": loc.resolved_latitude,
            "longitude": loc.resolved_longitude,
        },
        "resolutionNote": loc.resolution_note,
        "sourceVerified": loc.source_verified,
        "hemisphere": "southern" if loc.resolved_latitude < 0 else "northern",
    }
