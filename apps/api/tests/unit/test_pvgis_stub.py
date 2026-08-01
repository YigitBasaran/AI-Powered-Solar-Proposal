"""The replay stub is test infrastructure, and is tested like anything else.

It is about to become the only thing standing between ~70 analysis tests and
the real PVGIS service, so a bug in it would look like a bug in the
application. These tests pin what it replays, what it refuses, and that it can
never be reached from `app/`.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.services.roof import build_roof_model
from tests.support.pvgis_stub import (
    CASE_LAT,
    CASE_LON,
    PvgisStub,
    load_captures,
    start_stub,
    validate_request,
)

API_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = API_ROOT.parents[1]

GOOD = {
    "lat": CASE_LAT,
    "lon": CASE_LON,
    "angle": 25.0,
    "loss": 14.0,
    "pvtechchoice": "crystSi",
    "mountingplace": "building",
    "outputformat": "json",
    "peakpower": 1.0,
}

#: The four case facets, and the integer PVGIS rounds each one to. Derived from
#: the calibration rather than transcribed, so re-tracing the roof cannot leave
#: this table quietly describing a geometry that no longer exists.
CASE_ASPECTS = {
    f.id: (round(f.pvgis_aspect_deg, 2), round(f.pvgis_aspect_deg))
    for f in build_roof_model().facets
}


@pytest.fixture(scope="module")
def stub_url():
    base_url, stub, stop = start_stub()
    try:
        yield base_url, stub
    finally:
        stop()


def _get(base_url: str, aspect: float, *, path: str = "/api/v5_3/PVcalc", **overrides):
    params = {**GOOD, "aspect": aspect, **overrides}
    return httpx.get(f"{base_url}{path}", params=params, timeout=10.0, follow_redirects=False)


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


def test_every_case_facet_has_a_capture() -> None:
    captures = load_captures(REPO_ROOT / "fixtures" / "pvgis")
    for facet, (_, rounded) in CASE_ASPECTS.items():
        assert rounded in captures, f"{facet} has no committed capture"


@pytest.mark.parametrize(("facet", "aspect"), [(f, a) for f, (a, _) in CASE_ASPECTS.items()])
def test_each_facet_aspect_replays_its_own_capture(stub_url, facet, aspect) -> None:
    base_url, _ = stub_url
    response = _get(base_url, aspect)

    assert response.status_code == 200, response.text
    payload = response.json()
    echoed = payload["inputs"]["mounting_system"]["fixed"]["azimuth"]["value"]
    assert round(float(echoed)) == CASE_ASPECTS[facet][1]
    assert payload["outputs"]["totals"]["fixed"]["E_y"] > 0
    assert len(payload["outputs"]["monthly"]["fixed"]) == 12


def test_an_unknown_aspect_is_refused_not_approximated(stub_url) -> None:
    """The whole reason this is not `_load_fixture`.

    Nearest-neighbour matching would answer 200 with a different facet's
    numbers. If the calibration ever moves, that must be loud.
    """
    base_url, _ = stub_url
    response = _get(base_url, 45.0)

    assert response.status_code == 400
    assert "no committed capture" in response.json()["stubError"]


def test_energy_scales_linearly_and_irradiation_does_not(stub_url) -> None:
    base_url, _ = stub_url
    north = CASE_ASPECTS["facet_n"][0]
    one = _get(base_url, north, peakpower=1.0).json()["outputs"]["totals"]["fixed"]
    six = _get(base_url, north, peakpower=6.0).json()["outputs"]["totals"]["fixed"]

    assert six["E_y"] == pytest.approx(one["E_y"] * 6.0, rel=1e-6)
    assert six["E_m"] == pytest.approx(one["E_m"] * 6.0, rel=1e-6)
    # Per-m2 irradiation and the loss percentages are not per-kWp quantities.
    assert six["H(i)_y"] == one["H(i)_y"]
    assert six["l_total"] == one["l_total"]


def test_the_echoed_peak_power_is_the_requested_one(stub_url) -> None:
    base_url, _ = stub_url
    payload = _get(base_url, -169.38, peakpower=3.6).json()
    assert payload["inputs"]["pv_module"]["peak_power"] == 3.6


# ---------------------------------------------------------------------------
# The contract check
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({"angle": 30.0}, "case pitch"),
        ({"loss": 10.0}, "configured"),
        ({"pvtechchoice": "CIS"}, "pvtechchoice"),
        ({"mountingplace": "free"}, "mountingplace"),
        ({"outputformat": "csv"}, "outputformat"),
        ({"lat": 41.0}, "case latitude"),
        ({"lon": 28.9}, "case longitude"),
        ({"optimalangles": "1"}, "roof geometry decides"),
    ],
)
def test_a_request_that_breaks_the_contract_is_refused(stub_url, override, expected) -> None:
    """Upgrades the stub from a replayer to a request-shape guard.

    A regression that sent the compass azimuth where the PVGIS aspect belongs
    would otherwise pass every test that only checks the numbers.
    """
    base_url, _ = stub_url
    response = _get(base_url, -169.38, **override)

    assert response.status_code == 400
    assert expected in response.json()["stubError"]


def test_the_validator_returns_the_aspect_and_power() -> None:
    aspect, peak = validate_request({**GOOD, "aspect": "-169.38", "peakpower": "6"})
    assert aspect == pytest.approx(-169.38)
    assert peak == pytest.approx(6.0)


def test_a_missing_parameter_names_itself() -> None:
    from tests.support.pvgis_stub import StubContractError

    params = {k: v for k, v in GOOD.items() if k != "loss"}
    with pytest.raises(StubContractError, match="loss"):
        validate_request({**params, "aspect": -169.38})


# ---------------------------------------------------------------------------
# Faults
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fault", "status"),
    [("unavailable", 503), ("permanent", 400), ("server-error", 500)],
)
def test_status_faults(stub_url, fault, status) -> None:
    base_url, _ = stub_url
    response = _get(base_url, -169.38, path=f"/__fault/{fault}/api/v5_3/PVcalc")
    assert response.status_code == status


def test_a_redirect_fault_does_not_follow(stub_url) -> None:
    base_url, _ = stub_url
    response = _get(base_url, -169.38, path="/__fault/redirect/api/v5_3/PVcalc")
    assert response.status_code == 302
    assert "evil.example" in response.headers["location"]


def test_garbage_is_a_two_hundred_that_is_not_json(stub_url) -> None:
    base_url, _ = stub_url
    response = _get(base_url, -169.38, path="/__fault/garbage/api/v5_3/PVcalc")
    assert response.status_code == 200
    with pytest.raises(json.JSONDecodeError):
        response.json()


def test_malformed_is_valid_json_that_fails_the_schema(stub_url) -> None:
    base_url, _ = stub_url
    response = _get(base_url, -169.38, path="/__fault/malformed/api/v5_3/PVcalc")
    assert response.status_code == 200
    assert "E_y" not in response.json()["outputs"]["totals"]["fixed"]


def test_flaky_fails_twice_then_succeeds(stub_url) -> None:
    base_url, _ = stub_url
    httpx.post(f"{base_url}/__stub/reset", timeout=10.0)
    path = "/__fault/flaky3/api/v5_3/PVcalc"

    codes = [
        _get(base_url, CASE_ASPECTS["facet_w"][0], path=path).status_code for _ in range(3)
    ]
    assert codes == [503, 503, 200]


def test_retry_after_carries_the_header_then_succeeds(stub_url) -> None:
    base_url, _ = stub_url
    httpx.post(f"{base_url}/__stub/reset", timeout=10.0)
    path = "/__fault/retry-after/api/v5_3/PVcalc"

    first = _get(base_url, -79.38, path=path)
    assert first.status_code == 429
    assert first.headers["Retry-After"] == "2"
    assert _get(base_url, -79.38, path=path).status_code == 200


def test_one_facet_fails_while_the_others_answer(stub_url) -> None:
    """What makes partial-probe rejection testable at all."""
    base_url, _ = stub_url
    path = "/__fault/one-facet/-79.38/api/v5_3/PVcalc"

    assert _get(base_url, -79.38, path=path).status_code == 503
    assert _get(base_url, -169.38, path=path).status_code == 200
    assert _get(base_url, CASE_ASPECTS["facet_w"][0], path=path).status_code == 200


def test_mixed_radiation_database_affects_only_the_named_aspect(stub_url) -> None:
    base_url, _ = stub_url
    south = CASE_ASPECTS["facet_s"][0]
    path = f"/__fault/mixed-raddb/{south}/api/v5_3/PVcalc"

    odd = _get(base_url, south, path=path).json()
    normal = _get(base_url, -169.38, path=path).json()
    assert odd["inputs"]["meteo_data"]["radiation_db"] == "PVGIS-ERA5"
    assert normal["inputs"]["meteo_data"]["radiation_db"] == "PVGIS-SARAH3"


def test_the_attempt_counter_is_per_aspect(stub_url) -> None:
    """Otherwise four concurrent facets would consume each other's attempts."""
    base_url, _ = stub_url
    httpx.post(f"{base_url}/__stub/reset", timeout=10.0)
    path = "/__fault/flaky3/api/v5_3/PVcalc"

    assert _get(base_url, -169.38, path=path).status_code == 503
    assert _get(base_url, -79.38, path=path).status_code == 503
    # The north facet is on its second attempt, not its third.
    assert _get(base_url, -169.38, path=path).status_code == 503
    assert _get(base_url, -169.38, path=path).status_code == 200


# ---------------------------------------------------------------------------
# Control plane
# ---------------------------------------------------------------------------


def test_the_request_log_records_what_was_asked(stub_url) -> None:
    base_url, _ = stub_url
    httpx.post(f"{base_url}/__stub/reset", timeout=10.0)
    _get(base_url, -169.38, peakpower=1.0)

    log = httpx.get(f"{base_url}/__stub/requests", timeout=10.0).json()
    assert len(log) == 1
    assert float(log[0]["aspect"]) == pytest.approx(-169.38)
    assert float(log[0]["peakpower"]) == pytest.approx(1.0)


def test_health_lists_the_captures(stub_url) -> None:
    base_url, _ = stub_url
    body = httpx.get(f"{base_url}/__stub/health", timeout=10.0).json()
    assert body["ok"] is True
    # One capture per case facet, indexed by the integer PVGIS rounds it to.
    assert body["captures"] == sorted(rounded for _, rounded in CASE_ASPECTS.values())


def test_reset_clears_the_log_and_the_attempt_counters(stub_url) -> None:
    base_url, _ = stub_url
    _get(base_url, -169.38)
    httpx.post(f"{base_url}/__stub/reset", timeout=10.0)
    assert httpx.get(f"{base_url}/__stub/requests", timeout=10.0).json() == []


# ---------------------------------------------------------------------------
# It is test infrastructure, and stays that way
# ---------------------------------------------------------------------------


def test_the_application_never_imports_the_stub() -> None:
    """The one assertion that keeps this from becoming a second fixture mode."""
    offenders = [
        path.relative_to(API_ROOT).as_posix()
        for path in (API_ROOT / "app").rglob("*.py")
        if "tests.support" in path.read_text(encoding="utf-8")
        or "pvgis_stub" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"application code reaches into test support: {offenders}"


def test_the_stub_is_not_packaged() -> None:
    """`hatch` ships `app` only, so `tests/` cannot travel with a wheel."""
    pyproject = (API_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'packages = ["app"]' in pyproject


def test_a_stub_without_captures_refuses_to_start(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="no PVGIS captures"):
        PvgisStub(tmp_path)
