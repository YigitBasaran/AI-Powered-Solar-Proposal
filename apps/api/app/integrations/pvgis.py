"""PVGIS 5.3 client.

The LLM never estimates production; this is where the numbers come from.

Every facet is probed once at **1 kWp**. Specific yield is independent of
installed power, so one normalised observation per facet serves the ranking the
optimiser needs *and* the production figure for whatever size is chosen - from
the same observation, so the two cannot disagree. Production is then
`installed kWp x specific yield`, which is arithmetic rather than a second call.

Retry policy is explicit and bounded. Transient statuses get the full budget, a
500 gets one cautious extra attempt, anything else is permanent, and a 200 whose
body parses as JSON but not as a PVcalc response is a permanent failure too -
retrying a deterministic parse failure only delays it. Time is injected, so the
retry tests run in milliseconds rather than waiting.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import httpx

from app.core.config import PvgisMode, Settings, get_settings
from app.core.errors import PvgisUnavailableError
from app.domain.models import DataSource, RoofFacet

logger = logging.getLogger("solarvis.pvgis")

#: Statuses worth the full retry budget: the service is up but busy or briefly
#: unwell, and the next attempt plausibly succeeds.
TRANSIENT_STATUS = frozenset({429, 502, 503, 504, 529})

#: Statuses that get a *bounded* second chance. A 500 is ambiguous - plausibly a
#: blip, plausibly a request PVGIS mishandled - so one retry buys the blip case
#: without spending the whole ladder confirming a deterministic failure.
CAUTIOUS_STATUS_ATTEMPTS: dict[int, int] = {500: 2}

MAX_CONCURRENCY = 4

#: The loss fields PVGIS reports, and the names they are stored under. `l_spec`
#: arrives as a JSON *string* while the rest are numbers, and three of the four
#: are negative percentages - so each is coerced separately and a field that
#: will not coerce is simply dropped.
LOSS_FIELDS = {
    "l_aoi": "angleOfIncidencePercent",
    "l_spec": "spectralPercent",
    "l_tg": "temperatureAndIrradiancePercent",
    "l_total": "totalPercent",
}


# ---------------------------------------------------------------------------
# Injected time
# ---------------------------------------------------------------------------


def _default_jitter(upper: float) -> float:
    return random.uniform(0.0, upper)


@dataclass(frozen=True)
class RetryClock:
    """Everything time-dependent about a retry, in one injectable place.

    Real by default. Tests supply a fake that advances a virtual clock and
    records what it was asked to sleep, so the retry suite asserts on a
    schedule instead of waiting several seconds per case - which is how retry
    logic ends up untested.
    """

    now: Callable[[], float] = time.monotonic
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    jitter: Callable[[float], float] = _default_jitter


DEFAULT_RETRY_CLOCK = RetryClock()


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PvgisResult:
    annual_kwh: float
    monthly_kwh: list[float]
    specific_yield_kwh_per_kwp: float
    #: The same twelve values normalised to 1 kWp, so production at any size is
    #: a multiplication rather than another request.
    monthly_specific_yield_kwh_per_kwp: list[float]
    peak_power_kwp: float
    aspect_deg: float
    angle_deg: float
    radiation_database: str
    data_source: DataSource
    retrieved_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    #: The query actually sent, for provenance. Never used for control flow.
    request_params: dict[str, Any] = field(default_factory=dict)
    #: PVGIS's own loss breakdown where it returned one - angle of incidence,
    #: spectral, temperature. Recorded, never recomputed.
    losses_percent: dict[str, float] | None = None


@dataclass(frozen=True)
class FacetProbe:
    """One facet's normalised observation."""

    facet_id: str
    compass_azimuth_deg: float
    pvgis_aspect_deg: float
    angle_deg: float
    result: PvgisResult

    @property
    def specific_yield_kwh_per_kwp(self) -> float:
        return self.result.specific_yield_kwh_per_kwp


@dataclass(frozen=True)
class PvgisProbeSet:
    """Every usable facet, probed at 1 kWp, from one batch.

    A partial set is never produced: the optimiser ranks facets *against each
    other*, so a missing facet silently changes which roof planes get panels.
    That is not a smaller problem than a missing analysis.
    """

    probes: dict[str, FacetProbe]
    endpoint: str
    batch_completed_at: datetime
    data_source: DataSource
    request_params: dict[str, Any]

    def yields(self) -> dict[str, float]:
        return {fid: p.specific_yield_kwh_per_kwp for fid, p in self.probes.items()}

    @property
    def radiation_databases(self) -> set[str]:
        return {p.result.radiation_database for p in self.probes.values()}


@dataclass
class _CacheEntry:
    result: PvgisResult
    stored_at: float


@dataclass
class InMemoryPvgisCache:
    """Per-request cache.

    Kept while fixture mode still exists. Once every probe is a 1 kWp call at a
    distinct aspect, no two keys within a request can collide and there is no
    cross-request reuse, so this becomes dead weight.
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


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _losses(totals: dict[str, Any]) -> dict[str, float] | None:
    """PVGIS's loss breakdown, coerced field by field.

    A loss figure is provenance, not a number anything depends on, so one that
    will not coerce is dropped rather than allowed to fail an otherwise-good
    response.
    """
    found: dict[str, float] = {}
    for source_key, name in LOSS_FIELDS.items():
        if source_key not in totals:
            continue
        try:
            found[name] = float(totals[source_key])
        except (TypeError, ValueError):
            logger.debug("PVGIS loss field %s was not numeric: %r", source_key, totals[source_key])
    return found or None


def parse_pvcalc(
    payload: dict[str, Any],
    *,
    source: DataSource,
    request_params: dict[str, Any] | None = None,
    retrieved_at: datetime | None = None,
) -> PvgisResult:
    """Parse a PVcalc payload.

    A failure here is **permanent**. The body arrived intact and is valid JSON;
    it simply is not a PVcalc response, and asking again would produce the same
    thing. Transport-level damage - a truncated body that will not parse as JSON
    at all - is handled in the fetch loop, where it is genuinely worth retrying.
    """
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
        monthly_specific_yield_kwh_per_kwp=[m / peak for m in monthly],
        peak_power_kwp=peak,
        aspect_deg=aspect,
        angle_deg=angle,
        radiation_database=radiation_db,
        data_source=source,
        retrieved_at=retrieved_at or datetime.now(UTC),
        request_params=dict(request_params or {}),
        losses_percent=_losses(totals),
    )


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    """`Retry-After`, in seconds. Both the delta and the HTTP-date form."""
    if not value:
        return None
    try:
        return max(0.0, float(value.strip()))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - (now or datetime.now(UTC))).total_seconds())


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class PvgisClient:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        cache: InMemoryPvgisCache | None = None,
        client: httpx.AsyncClient | None = None,
        retry_clock: RetryClock | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._clock = retry_clock or DEFAULT_RETRY_CLOCK
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
            monthly_specific_yield_kwh_per_kwp=base.monthly_specific_yield_kwh_per_kwp,
            peak_power_kwp=peak_power_kwp,
            aspect_deg=base.aspect_deg,
            angle_deg=base.angle_deg,
            radiation_database=base.radiation_database,
            data_source=source,
            losses_percent=base.losses_percent,
        )

    # -- live -------------------------------------------------------------

    def _backoff(self, attempt: int) -> float:
        """Exponential with full jitter, so concurrent facets do not resonate."""
        s = self._settings
        ceiling = min(
            s.pvgis_retry_base_delay_seconds * (2 ** (attempt - 1)),
            s.pvgis_retry_max_delay_seconds,
        )
        return self._clock.jitter(ceiling)

    async def _fetch_live(self, params: dict[str, Any]) -> dict[str, Any]:
        s = self._settings
        url = f"{s.pvgis_base_url}/PVcalc"
        started = self._clock.now()
        attempt = 0
        last_error = "unknown"

        async def _once(
            client: httpx.AsyncClient,
        ) -> tuple[dict[str, Any] | None, int, float | None]:
            """One attempt. Returns `(payload, attempt_cap, retry_after)`.

            A cap below the configured maximum is how a 500 gets exactly one
            extra try without a special case in the loop.
            """
            nonlocal last_error
            try:
                # Redirects are refused, never followed: a 3xx moves the request
                # off the origin whose trustworthiness was just established.
                response = await client.get(
                    url, params=params, timeout=s.pvgis_timeout_seconds, follow_redirects=False
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = type(exc).__name__
                return None, s.pvgis_max_attempts, None

            status = response.status_code
            if response.is_redirect:
                raise PvgisUnavailableError(
                    f"PVGIS redirected to {response.headers.get('location')!r}; refusing to "
                    f"follow it off the trusted origin",
                    details={"status": status},
                )
            if status == 200:
                try:
                    return dict(response.json()), s.pvgis_max_attempts, None
                except (json.JSONDecodeError, ValueError) as exc:
                    # A body that is not JSON at all is plausibly a truncated
                    # transfer, which is worth another try. A body that *is*
                    # JSON but not a PVcalc response is not - `parse_pvcalc`
                    # raises for that, outside this loop.
                    last_error = f"invalid JSON: {exc}"
                    return None, s.pvgis_max_attempts, None

            last_error = f"HTTP {status}"
            retry_after = parse_retry_after(response.headers.get("Retry-After"))
            if status in TRANSIENT_STATUS:
                return None, s.pvgis_max_attempts, retry_after
            if status in CAUTIOUS_STATUS_ATTEMPTS:
                return None, CAUTIOUS_STATUS_ATTEMPTS[status], retry_after
            raise PvgisUnavailableError(
                f"PVGIS rejected the request: {last_error}", details={"status": status}
            )

        async def _do(client: httpx.AsyncClient) -> dict[str, Any]:
            nonlocal attempt
            while True:
                attempt += 1
                payload, cap, retry_after = await _once(client)
                if payload is not None:
                    return payload

                if attempt >= min(cap, s.pvgis_max_attempts):
                    raise PvgisUnavailableError(
                        f"PVGIS unavailable after {attempt} attempts: {last_error}",
                        details={"attempts": attempt, "lastError": last_error},
                    )

                elapsed = self._clock.now() - started
                remaining = s.pvgis_retry_budget_seconds - elapsed
                if remaining <= 0:
                    raise PvgisUnavailableError(
                        f"PVGIS retry budget of {s.pvgis_retry_budget_seconds:.0f}s "
                        f"exhausted after {attempt} attempts: {last_error}",
                        details={"attempts": attempt, "lastError": last_error},
                    )

                # A server-suggested delay is honoured, but never beyond what is
                # left of the budget: a customer's request is not held open for
                # five minutes because a header asked nicely.
                suggested = retry_after if retry_after is not None else self._backoff(attempt)
                delay = min(suggested, remaining)
                logger.warning("PVGIS retry %d in %.2fs after %s", attempt, delay, last_error)
                await self._clock.sleep(delay)

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
            result = parse_pvcalc(payload, source=DataSource.LIVE, request_params=params)
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


# ---------------------------------------------------------------------------
# Probing every facet, once
# ---------------------------------------------------------------------------


async def probe_facets(
    facets: Sequence[RoofFacet],
    *,
    settings: Settings | None = None,
    client: PvgisClient | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> PvgisProbeSet:
    """One 1 kWp observation per facet, concurrently, on one connection pool.

    Concurrent matters twice. It is four round trips instead of four in series,
    and - because each facet carries its own retry budget - a bad PVGIS day
    costs roughly one budget in wall-clock rather than four. Getting that wrong
    is silent: the numbers come out identical and only the latency betrays it.

    Every facet is probed, not just the ones that will receive panels, because
    the optimiser ranks them against each other and a later size change must be
    answerable without another call.
    """
    settings = settings or get_settings()

    async def _run(pvgis: PvgisClient) -> PvgisProbeSet:
        started = datetime.now(UTC)
        results = await asyncio.gather(
            *(
                pvgis.pvcalc(
                    lat=settings.case_resolved_latitude,
                    lon=settings.case_resolved_longitude,
                    peak_power_kwp=1.0,
                    angle_deg=facet.pitch_deg,
                    aspect_deg=facet.pvgis_aspect_deg,
                )
                for facet in facets
            ),
            return_exceptions=True,
        )

        probes: dict[str, FacetProbe] = {}
        failures: list[str] = []
        for facet, outcome in zip(facets, results, strict=True):
            if isinstance(outcome, BaseException):
                failures.append(f"{facet.id}: {outcome}")
                continue
            probes[facet.id] = FacetProbe(
                facet_id=facet.id,
                compass_azimuth_deg=facet.compass_azimuth_deg,
                pvgis_aspect_deg=facet.pvgis_aspect_deg,
                angle_deg=facet.pitch_deg,
                result=outcome,
            )

        if failures:
            # No partial set. The optimiser ranks facets against each other, so
            # a missing facet does not shrink the problem - it silently changes
            # which roof planes get panels.
            raise PvgisUnavailableError(
                "PVGIS did not answer for every roof facet, so no layout can be "
                f"optimised: {'; '.join(failures)}",
                details={"facets": [f.split(":")[0] for f in failures]},
            )

        sources = {p.result.data_source for p in probes.values()}
        aggregate = DataSource.LIVE if sources == {DataSource.LIVE} else next(iter(sources))
        first = next(iter(probes.values())).result
        return PvgisProbeSet(
            probes=probes,
            endpoint=f"{settings.pvgis_base_url}/PVcalc",
            batch_completed_at=max(
                (p.result.retrieved_at for p in probes.values()), default=started
            ),
            data_source=aggregate,
            request_params={
                k: v for k, v in first.request_params.items() if k not in {"aspect", "angle"}
            },
        )

    if client is not None:
        return await _run(client)
    if http_client is not None:
        return await _run(PvgisClient(settings, client=http_client))
    # One pool for the batch, rather than a fresh connection per facet.
    async with httpx.AsyncClient() as pooled:
        return await _run(PvgisClient(settings, client=pooled))


class PvgisFacetYieldRankingProvider:
    """Live ranking implementation of :class:`FacetYieldRankingProvider`.

    A 1 kWp probe is enough: specific yield is independent of installed power,
    so one probe per aspect serves every system size.
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
