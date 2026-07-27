"""PVGIS 5.3 client.

The LLM never estimates production; this is where the numbers come from.

Fixture responses are parsed by exactly the same code as live ones, so fixture
mode cannot drift into being a second, subtly different implementation. What
mode a number came from is always carried on the result and surfaced to the
user - a fixture is never presented as live.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from app.core.config import PvgisMode, Settings, get_settings
from app.core.errors import PvgisUnavailableError
from app.domain.models import DataSource, RoofFacet

logger = logging.getLogger("solarvis.pvgis")

RETRY_DELAYS = (0.5, 1.0, 2.0)
RETRYABLE_STATUS = {429, 500, 502, 503, 504, 529}
MAX_CONCURRENCY = 4


@dataclass(frozen=True)
class PvgisResult:
    annual_kwh: float
    monthly_kwh: list[float]
    specific_yield_kwh_per_kwp: float
    peak_power_kwp: float
    aspect_deg: float
    angle_deg: float
    radiation_database: str
    data_source: DataSource


@dataclass
class _CacheEntry:
    result: PvgisResult
    stored_at: float


@dataclass
class InMemoryPvgisCache:
    """Process-lifetime cache.

    PVGIS yields for a fixed site are stable over the analysis horizon and the
    service asks to be used politely, so a long TTL is appropriate. The cache
    is keyed on every parameter that changes the answer, per the brief.
    """

    ttl_seconds: float
    _entries: dict[str, _CacheEntry] = field(default_factory=dict)

    def get(self, key: str) -> PvgisResult | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if time.monotonic() - entry.stored_at > self.ttl_seconds:
            del self._entries[key]
            return None
        return entry.result

    def put(self, key: str, result: PvgisResult) -> None:
        self._entries[key] = _CacheEntry(result, time.monotonic())

    def clear(self) -> None:
        self._entries.clear()


def build_cache_key(
    *,
    lat: float,
    lon: float,
    peak_power_kwp: float,
    angle_deg: float,
    aspect_deg: float,
    loss_percent: float,
    technology: str,
    mounting: str,
    version: str = "v5_3",
) -> str:
    return "|".join(
        [
            version,
            f"{lat:.6f}",
            f"{lon:.6f}",
            f"{peak_power_kwp:.4f}",
            f"{angle_deg:.2f}",
            f"{aspect_deg:.2f}",
            f"{loss_percent:.2f}",
            technology,
            mounting,
        ]
    )


def parse_pvcalc(payload: dict[str, Any], *, source: DataSource) -> PvgisResult:
    """Parse a PVcalc payload. Shared by live and fixture paths."""
    try:
        outputs = payload["outputs"]
        totals = outputs["totals"]["fixed"]
        monthly_rows = outputs["monthly"]["fixed"]
        inputs = payload["inputs"]

        annual = float(totals["E_y"])
        monthly = [float(row["E_m"]) for row in monthly_rows]
        peak = float(inputs["pv_module"]["peak_power"])
        angle = float(inputs["mounting_system"]["fixed"]["slope"]["value"])
        aspect = float(inputs["mounting_system"]["fixed"]["azimuth"]["value"])
        radiation_db = str(inputs["meteo_data"]["radiation_db"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PvgisUnavailableError(f"PVGIS response could not be parsed: {exc}") from exc

    if len(monthly) != 12:
        raise PvgisUnavailableError(f"PVGIS returned {len(monthly)} monthly values, expected 12")
    if annual <= 0 or peak <= 0:
        raise PvgisUnavailableError("PVGIS returned a non-positive production or power")

    monthly_sum = sum(monthly)
    if abs(monthly_sum - annual) / annual > 0.02:
        raise PvgisUnavailableError(
            f"PVGIS monthly values sum to {monthly_sum:.1f} but annual is {annual:.1f}"
        )

    return PvgisResult(
        annual_kwh=annual,
        monthly_kwh=monthly,
        specific_yield_kwh_per_kwp=annual / peak,
        peak_power_kwp=peak,
        aspect_deg=aspect,
        angle_deg=angle,
        radiation_database=radiation_db,
        data_source=source,
    )


class PvgisClient:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        cache: InMemoryPvgisCache | None = None,
        client: httpx.AsyncClient | None = None,
        retry_delays: tuple[float, ...] = RETRY_DELAYS,
    ) -> None:
        self._settings = settings or get_settings()
        self._retry_delays = retry_delays
        self._cache = cache or InMemoryPvgisCache(
            ttl_seconds=self._settings.pvgis_cache_ttl_hours * 3600
        )
        self._client = client
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    # -- fixtures ---------------------------------------------------------

    def _fixture_dir(self) -> Path:
        return self._settings.fixtures_dir / "pvgis"

    def _load_fixture(self, aspect_deg: float) -> dict[str, Any] | None:
        """Nearest captured PVcalc payload by aspect."""
        directory = self._fixture_dir()
        if not directory.is_dir():
            return None
        best: tuple[float, Path] | None = None
        for path in directory.glob("pvcalc*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                captured = float(payload["inputs"]["mounting_system"]["fixed"]["azimuth"]["value"])
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
            delta = abs(((captured - aspect_deg + 180.0) % 360.0) - 180.0)
            if best is None or delta < best[0]:
                best = (delta, path)
        if best is None:
            return None
        loaded: dict[str, Any] = json.loads(best[1].read_text(encoding="utf-8"))
        return loaded

    def _from_fixture(
        self, *, aspect_deg: float, peak_power_kwp: float, source: DataSource
    ) -> PvgisResult:
        payload = self._load_fixture(aspect_deg)
        if payload is None:
            raise PvgisUnavailableError(
                "No PVGIS fixture available. Run scripts/fetch_pvgis_fixtures.py, "
                "or set PVGIS_MODE=live."
            )
        base = parse_pvcalc(payload, source=source)
        # Production scales linearly with installed power, so a 1 kWp capture
        # serves any requested size without pretending to be a fresh call.
        factor = peak_power_kwp / base.peak_power_kwp
        return PvgisResult(
            annual_kwh=base.annual_kwh * factor,
            monthly_kwh=[m * factor for m in base.monthly_kwh],
            specific_yield_kwh_per_kwp=base.specific_yield_kwh_per_kwp,
            peak_power_kwp=peak_power_kwp,
            aspect_deg=base.aspect_deg,
            angle_deg=base.angle_deg,
            radiation_database=base.radiation_database,
            data_source=source,
        )

    # -- live -------------------------------------------------------------

    async def _fetch_live(self, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._settings.pvgis_base_url}/PVcalc"
        timeout = self._settings.pvgis_timeout_seconds

        async def _do(client: httpx.AsyncClient) -> dict[str, Any]:
            last_error: str = "unknown"
            for attempt, delay in enumerate((*self._retry_delays, None)):
                try:
                    response = await client.get(url, params=params, timeout=timeout)
                    if response.status_code == 200:
                        return dict(response.json())
                    last_error = f"HTTP {response.status_code}"
                    if response.status_code not in RETRYABLE_STATUS:
                        raise PvgisUnavailableError(
                            f"PVGIS rejected the request: {last_error}",
                            details={"status": response.status_code},
                        )
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    last_error = type(exc).__name__
                except json.JSONDecodeError as exc:
                    last_error = f"invalid JSON: {exc}"

                if delay is None:
                    break
                # Jitter so retries from concurrent facets do not resonate.
                await asyncio.sleep(delay + random.uniform(0, 0.1))
                logger.warning("PVGIS retry %d after %s", attempt + 1, last_error)

            raise PvgisUnavailableError(f"PVGIS unavailable after retries: {last_error}")

        if self._client is not None:
            return await _do(self._client)
        async with httpx.AsyncClient() as client:
            return await _do(client)

    # -- public -----------------------------------------------------------

    async def pvcalc(
        self,
        *,
        lat: float,
        lon: float,
        peak_power_kwp: float,
        angle_deg: float,
        aspect_deg: float,
    ) -> PvgisResult:
        s = self._settings
        key = build_cache_key(
            lat=lat,
            lon=lon,
            peak_power_kwp=peak_power_kwp,
            angle_deg=angle_deg,
            aspect_deg=aspect_deg,
            loss_percent=s.pvgis_system_loss_percent,
            technology=s.pvgis_technology,
            mounting=s.pvgis_mounting_place,
        )
        cached = self._cache.get(key)
        if cached is not None:
            logger.debug("PVGIS cache hit for %s", key)
            return PvgisResult(**{**cached.__dict__, "data_source": DataSource.CACHE})

        if s.pvgis_mode is PvgisMode.FIXTURE:
            result = self._from_fixture(
                aspect_deg=aspect_deg,
                peak_power_kwp=peak_power_kwp,
                source=DataSource.FIXTURE,
            )
            self._cache.put(key, result)
            return result

        params = {
            "lat": lat,
            "lon": lon,
            "peakpower": peak_power_kwp,
            "loss": s.pvgis_system_loss_percent,
            "angle": angle_deg,
            "aspect": aspect_deg,
            "pvtechchoice": s.pvgis_technology,
            "mountingplace": s.pvgis_mounting_place,
            "outputformat": "json",
        }

        started = time.monotonic()
        try:
            async with self._semaphore:
                payload = await self._fetch_live(params)
            result = parse_pvcalc(payload, source=DataSource.LIVE)
        except PvgisUnavailableError:
            if not s.pvgis_fallback_enabled:
                raise
            logger.warning("PVGIS live failed; falling back to fixture (labelled)")
            result = self._from_fixture(
                aspect_deg=aspect_deg,
                peak_power_kwp=peak_power_kwp,
                source=DataSource.LIVE_FALLBACK_FIXTURE,
            )
            self._cache.put(key, result)
            return result

        logger.info(
            "PVGIS live | aspect=%.1f peak=%.2f kWp -> %.0f kWh (%.0f ms, %s)",
            aspect_deg,
            peak_power_kwp,
            result.annual_kwh,
            (time.monotonic() - started) * 1000,
            result.radiation_database,
        )
        self._cache.put(key, result)
        return result


class PvgisFacetYieldRankingProvider:
    """Live ranking implementation of :class:`FacetYieldRankingProvider`.

    A 1 kWp probe is enough: specific yield is independent of installed power,
    so one cached probe per aspect serves every system size.
    """

    def __init__(self, client: PvgisClient, *, settings: Settings | None = None) -> None:
        self._client = client
        self._settings = settings or get_settings()

    async def specific_yield_kwh_per_kwp(self, facet: RoofFacet) -> float:
        result = await self._client.pvcalc(
            lat=self._settings.case_resolved_latitude,
            lon=self._settings.case_resolved_longitude,
            peak_power_kwp=1.0,
            angle_deg=facet.pitch_deg,
            aspect_deg=facet.pvgis_aspect_deg,
        )
        return result.specific_yield_kwh_per_kwp
