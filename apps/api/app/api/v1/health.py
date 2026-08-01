"""Liveness, readiness and configuration transparency endpoints.

Maps and FX still have fixture modes, and for those `/health/ready` reports
*degraded* rather than *down*: a full proposal still completes without them.

**PVGIS is different, and the asymmetry is worth stating.** It is a hard
dependency - there is no fixture, no fallback and no synthetic estimate - so
what is reported for it is not a mode but *which endpoint will be called* and
whether that configuration could produce a valid proposal at all.

The check makes no outbound request; a readiness probe that called PVGIS would
be a fine way to get rate-limited. It validates configuration instead, which
costs nothing and catches faults that would otherwise surface only when a
customer runs an analysis.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter

from app.core.config import (
    TEST_ENVIRONMENTS,
    LlmProvider,
    Settings,
    get_settings,
)
from app.domain.imagery import is_google_endpoint
from app.integrations.pvgis import classify_endpoint

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "alive"}


def _maps_status(settings: Settings) -> dict[str, Any]:
    """Where imagery comes from, and whether it could back a measurement.

    Not a mode: there is no fixture branch any more. What varies is the endpoint,
    and whether it is Google's own. No outbound call is made - a readiness probe
    must not - so this reports configuration, not reachability.
    """
    url = settings.google_static_maps_base_url
    live = is_google_endpoint(url)

    if live:
        return {
            "mode": "live",
            "endpoint": url,
            "ready": bool(settings.google_maps_api_key),
            "detail": (
                "Google Maps Static API"
                if settings.google_maps_api_key
                else "GOOGLE_MAPS_API_KEY is empty, so imagery cannot be fetched"
            ),
        }
    return {
        "mode": "stub",
        "endpoint": url,
        "ready": True,
        "detail": (
            "Imagery is answered by a local stub, not by Google. Measurements "
            "taken against it are not proposal-grade."
        ),
    }


def _pvgis_status(settings: Settings) -> dict[str, Any]:
    """Which endpoint will be called, and whether it could back a proposal.

    No outbound call. Every fault below is a configuration mistake that would
    otherwise be discovered by a customer.
    """
    trust = classify_endpoint(settings.pvgis_base_url)
    is_test_env = settings.app_env.lower() in TEST_ENVIRONMENTS

    reasons: list[str] = []
    if not trust.origin or trust.reason == "the URL has no host":
        reasons.append("PVGIS_BASE_URL does not name a host")
    elif not trust.is_trusted and not is_test_env:
        reasons.append(f"PVGIS endpoint is not proposal-grade: {trust.reason}")

    if settings.allow_replay_proposals and not is_test_env:
        # Start-up already refuses this, so reaching it means something bypassed
        # the settings. Reported anyway - it is the one signal an operator sees
        # without reading logs.
        reasons.append("ALLOW_REPLAY_PROPOSALS is set outside a test environment")

    if settings.pvgis_max_attempts < 1:
        reasons.append("PVGIS_MAX_ATTEMPTS must be at least 1")
    if settings.pvgis_retry_budget_seconds <= 0:
        reasons.append("PVGIS_RETRY_BUDGET_SECONDS must be positive")
    if settings.pvgis_timeout_seconds <= 0:
        reasons.append("PVGIS_TIMEOUT_SECONDS must be positive")

    return {
        "endpoint": f"{settings.pvgis_base_url}/PVcalc",
        "origin": trust.origin,
        "apiVersion": trust.api_version,
        "trusted": trust.is_trusted,
        "timeoutSeconds": settings.pvgis_timeout_seconds,
        "maxAttempts": settings.pvgis_max_attempts,
        "retryBudgetSeconds": settings.pvgis_retry_budget_seconds,
        # Reported because it is the single thing standing between a replayed
        # capture and an issued proposal. An operator should be able to see it
        # without reading the environment.
        "allowReplayProposals": settings.allow_replay_proposals,
        "ready": not reasons,
        "detail": "; ".join(reasons) or None,
    }


def _email_status(settings: Settings) -> dict[str, Any]:
    """Which provider will send, and whether it could.

    No outbound call, like every other check here - a readiness probe that
    opened an SMTP connection would be a fine way to get an IP blocked.

    `ready` is what the send route will do: false here means a send returns 503
    rather than silently falling back to console. `sends` is the field that
    keeps console mode honest at a glance - it is `false`, because console
    records a message and transmits nothing.
    """
    from app.services.email import build_sender

    if not settings.proposal_email_enabled:
        return {
            "mode": settings.email_mode.value,
            "provider": None,
            "sends": False,
            "ready": True,
            "detail": "PROPOSAL_EMAIL_ENABLED is off; proposals can still be shared by link.",
        }

    sender = build_sender(settings)
    ready_to_send, reason = sender.available()
    sends = sender.name != "console"

    return {
        "mode": settings.email_mode.value,
        "provider": sender.name,
        "sends": sends,
        "ready": ready_to_send,
        "notificationRecipient": bool(settings.salesperson_email),
        "detail": reason
        or (
            None
            if sends
            else "Console mode records the message locally and sends nothing."
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
        "pvgis": _pvgis_status(settings),
        "fx": {
            "mode": settings.fx_mode.value,
            "provider": settings.fx_provider,
            "dataProvider": settings.fx_data_provider,
            "ready": True,
        },
        "email": _email_status(settings),
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
