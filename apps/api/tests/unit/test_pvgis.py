"""PVGIS client tests.

Exact production numbers are asserted only against committed fixtures. The
live-marked tests assert invariants and plausible ranges instead, because PVGIS
revises its underlying radiation datasets and pinning live kWh would make this
suite fail for reasons that have nothing to do with this code.

Run the live set explicitly:

    pytest -m live
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from dataclasses import field as dataclasses_field
from pathlib import Path

import httpx
import pytest
import respx

from app.core.config import get_settings
from app.core.errors import PvgisUnavailableError
from app.domain.models import DataSource
from app.integrations.pvgis import (
    CAUTIOUS_STATUS_ATTEMPTS,
    PvgisClient,
    RetryClock,
    parse_pvcalc,
    parse_retry_after,
)


@dataclass
class FakeClock:
    """A virtual clock that records what it was asked to sleep.

    The retry policy is bounded by wall-clock as well as attempts, so testing it
    against the real clock would mean waiting seconds per case - and a suite
    that waits is a suite that gets marked slow and then skipped.
    """

    elapsed: float = 0.0
    sleeps: list[float] = dataclasses_field(default_factory=list)

    def now(self) -> float:
        return self.elapsed

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.elapsed += seconds

    def jitter(self, upper: float) -> float:
        # Deterministic: full jitter would make the recorded schedule random.
        return upper


def fake_clock() -> tuple[FakeClock, RetryClock]:
    fake = FakeClock()
    return fake, RetryClock(now=fake.now, sleep=fake.sleep, jitter=fake.jitter)
PVCALC_URL = "https://re.jrc.ec.europa.eu/api/v5_3/PVcalc"
CASE_LAT = -34.04658242871865
CASE_LON = 18.46491476666948


@pytest.fixture(scope="module")
def captured_payload() -> dict:
    """A real PVGIS response captured at the case site (north-facing)."""
    path = (
        Path(__file__).resolve().parents[3].parent
        / "fixtures"
        / "pvgis"
        / "pvcalcmaspectm169.38.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def settings():
    """Pinned at the canonical PVGIS URL, because respx mocks that transport.

    The rest of the suite points `PVGIS_BASE_URL` at the local replay stub.
    This file is the one place where the *client itself* is the unit under
    test, so it needs per-attempt control that only a transport mock gives -
    `side_effect=[503, 503, 200]` is the point of several tests below.
    """
    return get_settings().model_copy(
        update={"pvgis_base_url": "https://re.jrc.ec.europa.eu/api/v5_3"}
    )


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_parses_a_real_captured_response(captured_payload) -> None:
    result = parse_pvcalc(captured_payload, source=DataSource.REPLAY)
    assert len(result.monthly_kwh) == 12
    assert result.annual_kwh > 0
    assert result.radiation_database == "PVGIS-SARAH3"
    assert result.specific_yield_kwh_per_kwp == pytest.approx(
        result.annual_kwh / result.peak_power_kwp
    )


def test_monthly_values_sum_to_the_annual_total(captured_payload) -> None:
    result = parse_pvcalc(captured_payload, source=DataSource.REPLAY)
    assert sum(result.monthly_kwh) == pytest.approx(result.annual_kwh, rel=0.02)


def test_captured_north_facet_yield_is_in_a_plausible_band(captured_payload) -> None:
    result = parse_pvcalc(captured_payload, source=DataSource.REPLAY)
    assert 1600 < result.specific_yield_kwh_per_kwp < 1750


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"outputs": {}},
        {"outputs": {"totals": {"fixed": {"E_y": 100}}}},
        {"outputs": {"totals": {"fixed": {"E_y": "abc"}}, "monthly": {"fixed": []}}},
    ],
)
def test_malformed_payloads_are_rejected(payload) -> None:
    with pytest.raises(PvgisUnavailableError):
        parse_pvcalc(payload, source=DataSource.LIVE)


def test_wrong_number_of_months_is_rejected(captured_payload) -> None:
    broken = json.loads(json.dumps(captured_payload))
    broken["outputs"]["monthly"]["fixed"] = broken["outputs"]["monthly"]["fixed"][:6]
    with pytest.raises(PvgisUnavailableError, match="monthly"):
        parse_pvcalc(broken, source=DataSource.LIVE)


def test_monthly_annual_mismatch_is_rejected(captured_payload) -> None:
    """Catches a truncated or mis-scaled payload that would still parse."""
    broken = json.loads(json.dumps(captured_payload))
    broken["outputs"]["totals"]["fixed"]["E_y"] = 99_999.0
    with pytest.raises(PvgisUnavailableError, match="sum"):
        parse_pvcalc(broken, source=DataSource.LIVE)


def test_non_positive_production_is_rejected(captured_payload) -> None:
    broken = json.loads(json.dumps(captured_payload))
    broken["outputs"]["totals"]["fixed"]["E_y"] = 0.0
    with pytest.raises(PvgisUnavailableError):
        parse_pvcalc(broken, source=DataSource.LIVE)


# ---------------------------------------------------------------------------
# Live request shape
# ---------------------------------------------------------------------------


@respx.mock
async def test_live_call_sends_the_documented_parameters(settings, captured_payload) -> None:
    route = respx.get(PVCALC_URL).mock(return_value=httpx.Response(200, json=captured_payload))
    client = PvgisClient(settings, retry_clock=fake_clock()[1])
    await client.pvcalc(
        lat=CASE_LAT, lon=CASE_LON, peak_power_kwp=6.0, angle_deg=25.0, aspect_deg=-169.38
    )

    params = route.calls.last.request.url.params
    assert float(params["lat"]) == pytest.approx(CASE_LAT)
    assert float(params["peakpower"]) == 6.0
    assert float(params["angle"]) == 25.0
    assert float(params["aspect"]) == pytest.approx(-169.38)
    assert float(params["loss"]) == 14.0
    assert params["pvtechchoice"] == "crystSi"
    assert params["mountingplace"] == "building"
    assert params["outputformat"] == "json"
    # PVGIS optimal-angle behaviour must not be requested: tilt and azimuth
    # come from the roof, not from PVGIS.
    assert "optimalangles" not in params
    assert "optimalinclination" not in params


@respx.mock
async def test_result_is_marked_live(settings, captured_payload) -> None:
    respx.get(PVCALC_URL).mock(return_value=httpx.Response(200, json=captured_payload))
    result = await PvgisClient(settings, retry_clock=fake_clock()[1]).pvcalc(
        lat=CASE_LAT, lon=CASE_LON, peak_power_kwp=1.0, angle_deg=25.0, aspect_deg=-169.38
    )
    assert result.data_source is DataSource.LIVE


@respx.mock
async def test_every_call_reaches_the_network(settings, captured_payload) -> None:
    """There is no cache, deliberately.

    A per-request cache could never hit: the four probes are at four distinct
    aspects and all at 1 kWp, and nothing is reused across requests. What it did
    do was relabel a result as `cache`, which meant a snapshot could report a
    source that was not where the number came from.
    """
    route = respx.get(PVCALC_URL).mock(return_value=httpx.Response(200, json=captured_payload))
    client = PvgisClient(settings, retry_clock=fake_clock()[1])
    args = {
        "lat": CASE_LAT,
        "lon": CASE_LON,
        "peak_power_kwp": 1.0,
        "angle_deg": 25.0,
        "aspect_deg": -169.38,
    }
    first = await client.pvcalc(**args)
    second = await client.pvcalc(**args)

    assert route.call_count == 2
    assert first.data_source is DataSource.LIVE
    assert second.data_source is DataSource.LIVE


# ---------------------------------------------------------------------------
# Reliability
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.parametrize("status", [429, 502, 503, 504, 529])
async def test_a_retryable_status_that_never_clears_raises(settings, status) -> None:
    """Retried to the budget, then it fails. There is nothing to fall back to.

    Until this change a captured payload was substituted here and labelled - so
    an outage produced a proposal quoting a production figure that had never
    been observed for this roof. The label was the only thing distinguishing it,
    and nothing downstream read the label.
    """
    respx.get(PVCALC_URL).mock(return_value=httpx.Response(status))
    with pytest.raises(PvgisUnavailableError):
        await PvgisClient(settings, retry_clock=fake_clock()[1]).pvcalc(
            lat=CASE_LAT, lon=CASE_LON, peak_power_kwp=1.0, angle_deg=25.0, aspect_deg=-169.38
        )


@respx.mock
async def test_retries_before_giving_up(settings, captured_payload) -> None:
    route = respx.get(PVCALC_URL).mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(503),
            httpx.Response(200, json=captured_payload),
        ]
    )
    result = await PvgisClient(settings, retry_clock=fake_clock()[1]).pvcalc(
        lat=CASE_LAT, lon=CASE_LON, peak_power_kwp=1.0, angle_deg=25.0, aspect_deg=-169.38
    )
    assert route.call_count == 3
    assert result.data_source is DataSource.LIVE


@respx.mock
async def test_a_timeout_raises(settings) -> None:
    respx.get(PVCALC_URL).mock(side_effect=httpx.ConnectTimeout("slow"))
    with pytest.raises(PvgisUnavailableError):
        await PvgisClient(settings, retry_clock=fake_clock()[1]).pvcalc(
            lat=CASE_LAT, lon=CASE_LON, peak_power_kwp=1.0, angle_deg=25.0, aspect_deg=-169.38
        )


# ---------------------------------------------------------------------------
# Live integration - invariants and ranges only
# ---------------------------------------------------------------------------


@pytest.mark.live
async def test_live_pvgis_responds_at_the_resolved_coordinate(settings) -> None:
    result = await PvgisClient(settings).pvcalc(
        lat=CASE_LAT, lon=CASE_LON, peak_power_kwp=1.0, angle_deg=25.0, aspect_deg=-180.0
    )
    assert result.radiation_database
    assert len(result.monthly_kwh) == 12
    assert sum(result.monthly_kwh) == pytest.approx(result.annual_kwh, rel=0.02)
    assert 900 < result.specific_yield_kwh_per_kwp < 2000


@pytest.mark.live
async def test_live_north_outperforms_south_in_the_southern_hemisphere(settings) -> None:
    """The ordering invariant. Never assert exact kWh against live PVGIS."""
    client = PvgisClient(settings)
    north = await client.pvcalc(
        lat=CASE_LAT, lon=CASE_LON, peak_power_kwp=1.0, angle_deg=25.0, aspect_deg=180.0
    )
    south = await client.pvcalc(
        lat=CASE_LAT, lon=CASE_LON, peak_power_kwp=1.0, angle_deg=25.0, aspect_deg=0.0
    )
    assert north.specific_yield_kwh_per_kwp > south.specific_yield_kwh_per_kwp
    ratio = north.specific_yield_kwh_per_kwp / south.specific_yield_kwh_per_kwp
    assert 1.2 < ratio < 2.0


# ---------------------------------------------------------------------------
# The bounded retry policy
# ---------------------------------------------------------------------------


@respx.mock
async def test_a_server_error_gets_exactly_one_extra_attempt(settings) -> None:
    """500 is ambiguous, so it is cautiously retried - once, not four times.

    Plausibly a blip; plausibly a request PVGIS mishandled. One retry buys the
    blip case without spending the whole ladder confirming a deterministic
    failure.
    """
    route = respx.get(PVCALC_URL).mock(return_value=httpx.Response(500))

    with pytest.raises(PvgisUnavailableError):
        await PvgisClient(settings, retry_clock=fake_clock()[1]).pvcalc(
            lat=CASE_LAT, lon=CASE_LON, peak_power_kwp=1.0, angle_deg=25.0, aspect_deg=-169.38
        )

    assert route.call_count == CAUTIOUS_STATUS_ATTEMPTS[500] == 2


@respx.mock
@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
async def test_a_permanent_status_is_not_retried(settings, status) -> None:
    route = respx.get(PVCALC_URL).mock(return_value=httpx.Response(status))

    with pytest.raises(PvgisUnavailableError):
        await PvgisClient(settings, retry_clock=fake_clock()[1]).pvcalc(
            lat=CASE_LAT, lon=CASE_LON, peak_power_kwp=1.0, angle_deg=25.0, aspect_deg=-169.38
        )

    assert route.call_count == 1, "a permanent rejection was retried"


@respx.mock
async def test_the_attempt_count_is_bounded(settings) -> None:
    route = respx.get(PVCALC_URL).mock(return_value=httpx.Response(503))
    fake, clock = fake_clock()

    with pytest.raises(PvgisUnavailableError, match="attempts"):
        await PvgisClient(settings, retry_clock=clock).pvcalc(
            lat=CASE_LAT, lon=CASE_LON, peak_power_kwp=1.0, angle_deg=25.0, aspect_deg=-169.38
        )

    assert route.call_count == settings.pvgis_max_attempts
    assert sum(fake.sleeps) <= settings.pvgis_retry_budget_seconds



@respx.mock
async def test_the_wall_clock_budget_binds_before_the_attempt_count(settings) -> None:
    """Two bounds, whichever comes first. Here the budget is the tight one."""
    respx.get(PVCALC_URL).mock(return_value=httpx.Response(503))
    tight = settings.model_copy(
        update={
            "pvgis_max_attempts": 50,
            "pvgis_retry_budget_seconds": 3.0,
            "pvgis_retry_base_delay_seconds": 1.0,
        }
    )
    fake, clock = fake_clock()

    with pytest.raises(PvgisUnavailableError, match="budget"):
        await PvgisClient(tight, retry_clock=clock).pvcalc(
            lat=CASE_LAT, lon=CASE_LON, peak_power_kwp=1.0, angle_deg=25.0, aspect_deg=-169.38
        )

    assert fake.elapsed <= 3.0


@respx.mock
async def test_backoff_is_exponential(settings) -> None:
    respx.get(PVCALC_URL).mock(return_value=httpx.Response(503))
    laddered = settings.model_copy(update={"pvgis_retry_base_delay_seconds": 0.5})
    fake, clock = fake_clock()

    with pytest.raises(PvgisUnavailableError):
        await PvgisClient(laddered, retry_clock=clock).pvcalc(
            lat=CASE_LAT, lon=CASE_LON, peak_power_kwp=1.0, angle_deg=25.0, aspect_deg=-169.38
        )

    # The fake jitter returns its ceiling, so the schedule is the ladder itself.
    assert fake.sleeps == [0.5, 1.0, 2.0]


@respx.mock
async def test_retry_after_is_honoured(settings, captured_payload) -> None:
    respx.get(PVCALC_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(200, json=captured_payload),
        ]
    )
    fake, clock = fake_clock()

    await PvgisClient(settings, retry_clock=clock).pvcalc(
        lat=CASE_LAT, lon=CASE_LON, peak_power_kwp=1.0, angle_deg=25.0, aspect_deg=-169.38
    )

    assert fake.sleeps == [7.0], "the server's own suggestion should win over the backoff"


@respx.mock
async def test_retry_after_is_clamped_to_the_remaining_budget(settings) -> None:
    """A header cannot hold a customer's request open for five minutes."""
    respx.get(PVCALC_URL).mock(return_value=httpx.Response(503, headers={"Retry-After": "300"}))
    bounded = settings.model_copy(update={"pvgis_retry_budget_seconds": 10.0})
    fake, clock = fake_clock()

    with pytest.raises(PvgisUnavailableError):
        await PvgisClient(bounded, retry_clock=clock).pvcalc(
            lat=CASE_LAT, lon=CASE_LON, peak_power_kwp=1.0, angle_deg=25.0, aspect_deg=-169.38
        )

    assert fake.sleeps[0] == 10.0, "300s was not clamped to the budget"
    assert fake.elapsed <= 10.0


def test_retry_after_parses_both_documented_forms() -> None:
    from datetime import UTC, datetime, timedelta

    assert parse_retry_after("12") == pytest.approx(12.0)
    assert parse_retry_after(None) is None
    assert parse_retry_after("not a duration") is None

    now = datetime(2026, 7, 29, 12, 0, 0, tzinfo=UTC)
    later = (now + timedelta(seconds=30)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    assert parse_retry_after(later, now=now) == pytest.approx(30.0, abs=1.0)
    # A date in the past is zero, not negative.
    earlier = (now - timedelta(seconds=30)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    assert parse_retry_after(earlier, now=now) == 0.0


@respx.mock
async def test_a_schema_invalid_success_is_not_retried(settings) -> None:
    """A 200 that parses as JSON but not as PVcalc is a permanent failure.

    Asking again produces the same body. Only a body that will not parse as
    JSON at all - a truncated transfer - is worth another attempt.
    """
    route = respx.get(PVCALC_URL).mock(
        return_value=httpx.Response(200, json={"outputs": {"totals": {"fixed": {}}}})
    )

    with pytest.raises(PvgisUnavailableError, match="could not be parsed"):
        await PvgisClient(settings, retry_clock=fake_clock()[1]).pvcalc(
            lat=CASE_LAT, lon=CASE_LON, peak_power_kwp=1.0, angle_deg=25.0, aspect_deg=-169.38
        )

    assert route.call_count == 1


@respx.mock
async def test_a_redirect_is_refused_not_followed(settings) -> None:
    """A 3xx moves the request off the origin whose trust was just established."""
    route = respx.get(PVCALC_URL).mock(
        return_value=httpx.Response(302, headers={"Location": "https://evil.example/PVcalc"})
    )

    with pytest.raises(PvgisUnavailableError, match="refusing to"):
        await PvgisClient(settings, retry_clock=fake_clock()[1]).pvcalc(
            lat=CASE_LAT, lon=CASE_LON, peak_power_kwp=1.0, angle_deg=25.0, aspect_deg=-169.38
        )

    assert route.call_count == 1


# ---------------------------------------------------------------------------
# The loss breakdown and normalised monthlies
# ---------------------------------------------------------------------------


def test_the_loss_breakdown_is_parsed_including_the_string_field(captured_payload) -> None:
    """`l_spec` arrives as a JSON string while the rest are numbers."""
    result = parse_pvcalc(captured_payload, source=DataSource.LIVE)

    assert result.losses_percent is not None
    assert set(result.losses_percent) == {
        "angleOfIncidencePercent",
        "spectralPercent",
        "temperatureAndIrradiancePercent",
        "totalPercent",
    }
    assert result.losses_percent["angleOfIncidencePercent"] < 0
    assert isinstance(result.losses_percent["spectralPercent"], float)


def test_a_missing_loss_block_is_not_an_error(captured_payload) -> None:
    payload = json.loads(json.dumps(captured_payload))
    for key in ("l_aoi", "l_spec", "l_tg", "l_total"):
        payload["outputs"]["totals"]["fixed"].pop(key, None)

    result = parse_pvcalc(payload, source=DataSource.LIVE)
    assert result.losses_percent is None
    assert result.annual_kwh > 0


def test_an_uncoercible_loss_field_is_dropped_not_fatal(captured_payload) -> None:
    payload = json.loads(json.dumps(captured_payload))
    payload["outputs"]["totals"]["fixed"]["l_aoi"] = "not a number"

    result = parse_pvcalc(payload, source=DataSource.LIVE)
    assert result.losses_percent is not None
    assert "angleOfIncidencePercent" not in result.losses_percent
    assert "totalPercent" in result.losses_percent


def test_monthly_specific_yield_is_normalised_to_one_kwp(captured_payload) -> None:
    result = parse_pvcalc(captured_payload, source=DataSource.LIVE)

    assert len(result.monthly_specific_yield_kwh_per_kwp) == 12
    assert sum(result.monthly_specific_yield_kwh_per_kwp) == pytest.approx(
        result.specific_yield_kwh_per_kwp, rel=0.02
    )


@pytest.mark.live
async def test_live_production_scales_linearly_with_installed_power(settings) -> None:
    """The assumption the whole normalised-probe design rests on.

    Every facet is probed once at 1 kWp and production is then computed as
    `installed kWp x specific yield` - arithmetic, not a second call. That is
    only sound if PVGIS itself is linear in `peakpower`, and only the real
    service can confirm it. If it ever stops being linear, every production
    figure in every proposal is wrong by whatever the non-linearity is, and no
    offline test could tell.
    """
    client = PvgisClient(settings)
    one = await client.pvcalc(
        lat=CASE_LAT, lon=CASE_LON, peak_power_kwp=1.0, angle_deg=25.0, aspect_deg=-169.378845
    )
    six = await client.pvcalc(
        lat=CASE_LAT, lon=CASE_LON, peak_power_kwp=6.0, angle_deg=25.0, aspect_deg=-169.378845
    )

    assert six.annual_kwh == pytest.approx(one.annual_kwh * 6.0, rel=1e-3)
    assert six.specific_yield_kwh_per_kwp == pytest.approx(
        one.specific_yield_kwh_per_kwp, rel=1e-3
    )
    for a, b in zip(six.monthly_kwh, one.monthly_kwh, strict=True):
        assert a == pytest.approx(b * 6.0, rel=2e-3)
