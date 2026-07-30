"""Is the imagery we are measuring against the imagery we calibrated against?

The map is worth showing either way - a customer looking at their own roof is
not harmed by looking at it. What is not worth showing is a *measurement* taken
from an outline that was traced on different pixels, because that measurement
looks exactly as confident as a correct one.

So this is deliberately not an all-or-nothing gate. When the answer is no, the
imagery still renders and everything downstream of it - roof measurements, panel
layout, the proposal - stops, with a message saying the roof needs re-tracing.

The verdict is cached per request signature. Google returns byte-identical
responses for a given tile, so re-fetching on every analysis would spend most of
a second to reach the same conclusion. The cache is deliberately not permanent:
imagery does get re-flown, and this check exists precisely to notice when it is.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

from app.core.config import Settings, get_settings
from app.core.errors import RoofCalibrationUnverifiedError
from app.domain.imagery import ImageryVerdict, request_signature, verify_imagery
from app.services.roof import calibration_metadata, load_calibration

logger = logging.getLogger("solarvis.imagery")

#: How long a verdict is trusted before the raster is fetched again.
VERDICT_TTL_SECONDS = 900.0


@dataclass(frozen=True)
class _CachedVerdict:
    verdict: ImageryVerdict
    signature: str
    checked_at: float


_cache: _CachedVerdict | None = None


def reset_cache() -> None:
    """Forget the cached verdict. For tests, and for a deliberate re-check."""
    global _cache
    _cache = None


async def current_imagery_verdict(
    settings: Settings | None = None, *, client: httpx.AsyncClient | None = None
) -> ImageryVerdict:
    """Fetch the configured raster and compare it with the calibration.

    A transport failure is *not* silently treated as unverified: it is a
    different problem with a different fix, and the map is unavailable anyway, so
    it surfaces as the imagery error it is.
    """
    global _cache
    settings = settings or get_settings()
    signature = request_signature(settings.satellite_image_config)

    cached = _cache
    if (
        cached is not None
        and cached.signature == signature
        and (time.monotonic() - cached.checked_at) < VERDICT_TTL_SECONDS
    ):
        return cached.verdict

    # Imported here rather than at module scope: the maps route imports this
    # module's siblings, and a top-level import would close the cycle.
    from app.api.v1.maps import fetch_raster

    raster = await fetch_raster(settings, client=client)
    expected = (calibration_metadata(load_calibration()).get("imagery") or {}).get(
        "perceptual_hash"
    )
    verdict = verify_imagery(raster, expected_hash=expected)
    _cache = _CachedVerdict(verdict=verdict, signature=signature, checked_at=time.monotonic())

    if not verdict.matches:
        logger.warning("imagery is not the calibrated capture: %s", verdict.reason)
    return verdict


async def require_calibrated_imagery(settings: Settings | None = None) -> None:
    """Refuse to measure against imagery the roof was not traced on.

    Called before an analysis and before finalisation, which between them cover
    every figure a customer ever sees.
    """
    verdict = await current_imagery_verdict(settings)
    if verdict.matches:
        return

    raise RoofCalibrationUnverifiedError(
        "The satellite imagery is not the imagery this roof was calibrated against, "
        f"so its measurements cannot be trusted ({verdict.reason}). The roof needs "
        "re-tracing against the current imagery before an analysis can be produced.",
        details={
            "expectedImageryHash": verdict.expected,
            "actualImageryHash": verdict.actual,
            "hammingDistance": verdict.distance,
            "recalibrationRequired": True,
        },
    )
