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
from pathlib import Path

import httpx
import pytest
import respx

from app.core.config import PvgisMode, get_settings
from app.core.errors import PvgisUnavailableError
from app.domain.models import DataSource
from app.integrations.pvgis import (
    InMemoryPvgisCache,
    PvgisClient,
    PvgisFacetYieldRankingProvider,
    build_cache_key,
    parse_pvcalc,
)
from app.services.roof import build_roof_model

NO_DELAY = (0.0, 0.0, 0.0)
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
    return get_settings().model_copy(update={"pvgis_mode": PvgisMode.LIVE})


@pytest.fixture
def cache():
    return InMemoryPvgisCache(ttl_seconds=3600)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_parses_a_real_captured_response(captured_payload) -> None:
    result = parse_pvcalc(captured_payload, source=DataSource.FIXTURE)
    assert len(result.monthly_kwh) == 12
    assert result.annual_kwh > 0
    assert result.radiation_database == "PVGIS-SARAH3"
    assert result.specific_yield_kwh_per_kwp == pytest.approx(
        result.annual_kwh / result.peak_power_kwp
    )


def test_monthly_values_sum_to_the_annual_total(captured_payload) -> None:
    result = parse_pvcalc(captured_payload, source=DataSource.FIXTURE)
    assert sum(result.monthly_kwh) == pytest.approx(result.annual_kwh, rel=0.02)


def test_captured_north_facet_yield_is_in_a_plausible_band(captured_payload) -> None:
    result = parse_pvcalc(captured_payload, source=DataSource.FIXTURE)
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
# Cache key
# ---------------------------------------------------------------------------


def test_cache_key_includes_every_parameter_that_changes_the_answer() -> None:
    base = {
        "lat": CASE_LAT,
        "lon": CASE_LON,
        "peak_power_kwp": 6.0,
        "angle_deg": 25.0,
        "aspect_deg": -169.4,
        "loss_percent": 14.0,
        "technology": "crystSi",
        "mounting": "building",
    }
    key = build_cache_key(**base)
    for field, changed in [
        ("lat", 10.0),
        ("lon", 10.0),
        ("peak_power_kwp", 3.6),
        ("angle_deg", 30.0),
        ("aspect_deg", 0.0),
        ("loss_percent", 10.0),
        ("technology", "CIS"),
        ("mounting", "free"),
    ]:
        assert build_cache_key(**{**base, field: changed}) != key, field


# ---------------------------------------------------------------------------
# Live request shape
# ---------------------------------------------------------------------------


@respx.mock
async def test_live_call_sends_the_documented_parameters(settings, cache, captured_payload) -> None:
    route = respx.get(PVCALC_URL).mock(return_value=httpx.Response(200, json=captured_payload))
    client = PvgisClient(settings, cache=cache, retry_delays=NO_DELAY)
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
async def test_result_is_marked_live(settings, cache, captured_payload) -> None:
    respx.get(PVCALC_URL).mock(return_value=httpx.Response(200, json=captured_payload))
    result = await PvgisClient(settings, cache=cache, retry_delays=NO_DELAY).pvcalc(
        lat=CASE_LAT, lon=CASE_LON, peak_power_kwp=1.0, angle_deg=25.0, aspect_deg=-169.38
    )
    assert result.data_source is DataSource.LIVE


@respx.mock
async def test_second_identical_call_is_served_from_cache(
    settings, cache, captured_payload
) -> None:
    route = respx.get(PVCALC_URL).mock(return_value=httpx.Response(200, json=captured_payload))
    client = PvgisClient(settings, cache=cache, retry_delays=NO_DELAY)
    args = {
        "lat": CASE_LAT,
        "lon": CASE_LON,
        "peak_power_kwp": 1.0,
        "angle_deg": 25.0,
        "aspect_deg": -169.38,
    }
    await client.pvcalc(**args)
    second = await client.pvcalc(**args)

    assert route.call_count == 1
    assert second.data_source is DataSource.CACHE


# ---------------------------------------------------------------------------
# Reliability
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.parametrize("status", [429, 500, 502, 503, 529])
async def test_retryable_statuses_fall_back_to_a_labelled_fixture(settings, cache, status) -> None:
    respx.get(PVCALC_URL).mock(return_value=httpx.Response(status))
    result = await PvgisClient(settings, cache=cache, retry_delays=NO_DELAY).pvcalc(
        lat=CASE_LAT, lon=CASE_LON, peak_power_kwp=1.0, angle_deg=25.0, aspect_deg=-169.38
    )
    assert result.data_source is DataSource.LIVE_FALLBACK_FIXTURE


@respx.mock
async def test_retries_before_giving_up(settings, cache, captured_payload) -> None:
    route = respx.get(PVCALC_URL).mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(503),
            httpx.Response(200, json=captured_payload),
        ]
    )
    result = await PvgisClient(settings, cache=cache, retry_delays=NO_DELAY).pvcalc(
        lat=CASE_LAT, lon=CASE_LON, peak_power_kwp=1.0, angle_deg=25.0, aspect_deg=-169.38
    )
    assert route.call_count == 3
    assert result.data_source is DataSource.LIVE


@respx.mock
async def test_timeout_falls_back_to_fixture(settings, cache) -> None:
    respx.get(PVCALC_URL).mock(side_effect=httpx.ConnectTimeout("slow"))
    result = await PvgisClient(settings, cache=cache, retry_delays=NO_DELAY).pvcalc(
        lat=CASE_LAT, lon=CASE_LON, peak_power_kwp=1.0, angle_deg=25.0, aspect_deg=-169.38
    )
    assert result.data_source is DataSource.LIVE_FALLBACK_FIXTURE


@respx.mock
async def test_failure_with_fallback_disabled_raises(settings, cache) -> None:
    respx.get(PVCALC_URL).mock(side_effect=httpx.ConnectTimeout("slow"))
    strict = settings.model_copy(update={"pvgis_fallback_enabled": False})
    with pytest.raises(PvgisUnavailableError):
        await PvgisClient(strict, cache=cache, retry_delays=NO_DELAY).pvcalc(
            lat=CASE_LAT,
            lon=CASE_LON,
            peak_power_kwp=1.0,
            angle_deg=25.0,
            aspect_deg=-169.38,
        )


# ---------------------------------------------------------------------------
# Fixture mode
# ---------------------------------------------------------------------------


async def test_fixture_mode_is_labelled_and_makes_no_request(cache) -> None:
    fixture_settings = get_settings().model_copy(update={"pvgis_mode": PvgisMode.FIXTURE})
    with respx.mock:
        route = respx.get(PVCALC_URL)
        result = await PvgisClient(fixture_settings, cache=cache, retry_delays=NO_DELAY).pvcalc(
            lat=CASE_LAT,
            lon=CASE_LON,
            peak_power_kwp=1.0,
            angle_deg=25.0,
            aspect_deg=-169.38,
        )
        assert not route.called
    assert result.data_source is DataSource.FIXTURE


async def test_fixture_scales_linearly_with_installed_power(cache) -> None:
    """Production scales with kWp; specific yield does not."""
    fixture_settings = get_settings().model_copy(update={"pvgis_mode": PvgisMode.FIXTURE})
    client = PvgisClient(fixture_settings, cache=cache, retry_delays=NO_DELAY)
    one = await client.pvcalc(
        lat=CASE_LAT, lon=CASE_LON, peak_power_kwp=1.0, angle_deg=25.0, aspect_deg=-169.38
    )
    six = await client.pvcalc(
        lat=CASE_LAT, lon=CASE_LON, peak_power_kwp=6.0, angle_deg=25.0, aspect_deg=-169.38
    )
    assert six.annual_kwh == pytest.approx(one.annual_kwh * 6.0)
    assert six.specific_yield_kwh_per_kwp == pytest.approx(one.specific_yield_kwh_per_kwp)


async def test_fixture_and_live_share_the_same_parser(captured_payload) -> None:
    """Fixture mode must not become a second implementation."""
    live = parse_pvcalc(captured_payload, source=DataSource.LIVE)
    fixture = parse_pvcalc(captured_payload, source=DataSource.FIXTURE)
    assert live.annual_kwh == fixture.annual_kwh
    assert live.monthly_kwh == fixture.monthly_kwh


# ---------------------------------------------------------------------------
# The live provider satisfies the same port as the fixture one
# ---------------------------------------------------------------------------


async def test_live_provider_satisfies_the_ranking_port(cache) -> None:
    from app.services.yield_ranking import FacetYieldRankingProvider

    fixture_settings = get_settings().model_copy(update={"pvgis_mode": PvgisMode.FIXTURE})
    provider = PvgisFacetYieldRankingProvider(
        PvgisClient(fixture_settings, cache=cache, retry_delays=NO_DELAY), settings=fixture_settings
    )
    assert isinstance(provider, FacetYieldRankingProvider)

    roof = build_roof_model()
    value = await provider.specific_yield_kwh_per_kwp(roof.facet("facet_n"))
    assert 1000 < value < 2000


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
