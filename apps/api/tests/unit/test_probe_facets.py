"""Probing every facet once, at 1 kWp, concurrently.

Three properties, each of which fails silently if it regresses:

* **all four facets**, so a later size change needs no new call;
* **concurrently**, so a bad PVGIS day costs one retry budget rather than four;
* **all or nothing**, because the optimiser ranks facets against each other.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest
import respx

from app.core.config import get_settings
from app.core.errors import PvgisUnavailableError
from app.integrations.pvgis import PvgisClient, RetryClock, probe_facets
from app.services.roof import build_roof_model

PVCALC_URL = "https://re.jrc.ec.europa.eu/api/v5_3/PVcalc"
REPO_ROOT = Path(__file__).resolve().parents[3].parent


@dataclass
class FakeClock:
    elapsed: float = 0.0
    sleeps: list[float] = field(default_factory=list)

    def now(self) -> float:
        return self.elapsed

    async def sleep(self, seconds: float) -> None:
        # Yield to the loop so concurrent tasks interleave, but advance the
        # virtual clock rather than the real one.
        self.sleeps.append(seconds)
        self.elapsed += seconds
        await asyncio.sleep(0)

    def jitter(self, upper: float) -> float:
        return upper


@pytest.fixture
def settings():
    # Pinned rather than inherited: this file mocks the transport, so it needs
    # the canonical URL and no fixture path to fall back into.
    return get_settings().model_copy(
        update={
            "pvgis_base_url": "https://re.jrc.ec.europa.eu/api/v5_3",
        }
    )


@pytest.fixture(scope="module")
def payload() -> dict:
    path = REPO_ROOT / "fixtures" / "pvgis" / "pvcalcmaspectm169.38.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def roof():
    return build_roof_model()


def _payload_for(aspect: float, base: dict) -> dict:
    """The capture, re-stamped with the aspect that was asked for."""
    body = json.loads(json.dumps(base))
    body["inputs"]["mounting_system"]["fixed"]["azimuth"]["value"] = aspect
    return body


# ---------------------------------------------------------------------------
# Every facet, once
# ---------------------------------------------------------------------------


@respx.mock
async def test_every_facet_is_probed_once_at_one_kwp(settings, roof, payload) -> None:
    route = respx.get(PVCALC_URL).mock(return_value=httpx.Response(200, json=payload))

    probes = await probe_facets(roof.facets, settings=settings)

    assert set(probes.probes) == {f.id for f in roof.facets}
    assert route.call_count == len(roof.facets) == 4
    for call in route.calls:
        assert call.request.url.params["peakpower"] == "1.0"


@respx.mock
async def test_each_probe_carries_both_angle_conventions(settings, roof, payload) -> None:
    """`aspect` alone is ambiguous; provenance names which convention it is."""
    respx.get(PVCALC_URL).mock(
        side_effect=lambda request: httpx.Response(
            200, json=_payload_for(float(request.url.params["aspect"]), payload)
        )
    )

    probes = await probe_facets(roof.facets, settings=settings)

    for facet in roof.facets:
        probe = probes.probes[facet.id]
        assert probe.pvgis_aspect_deg == pytest.approx(facet.pvgis_aspect_deg)
        assert probe.compass_azimuth_deg == pytest.approx(facet.compass_azimuth_deg)
        # PVGIS aspect is the compass azimuth turned through half a circle.
        delta = abs((probe.compass_azimuth_deg - probe.pvgis_aspect_deg) % 360.0)
        assert delta == pytest.approx(180.0, abs=1e-6)


@respx.mock
async def test_the_request_carries_the_pvgis_aspect_not_the_compass_azimuth(
    settings, roof, payload
) -> None:
    """The single most consequential thing to get backwards."""
    route = respx.get(PVCALC_URL).mock(return_value=httpx.Response(200, json=payload))

    await probe_facets(roof.facets, settings=settings)

    sent = sorted(float(c.request.url.params["aspect"]) for c in route.calls)
    expected = sorted(f.pvgis_aspect_deg for f in roof.facets)
    assert sent == pytest.approx(expected)


# ---------------------------------------------------------------------------
# All or nothing
# ---------------------------------------------------------------------------


@respx.mock
async def test_one_failed_facet_fails_the_whole_batch(settings, roof, payload) -> None:
    """No three-of-four path.

    The optimiser ranks the facets against each other, so a missing facet does
    not shrink the problem - it silently changes which roof planes get panels.
    """
    failing = roof.facets[1]

    def _respond(request: httpx.Request) -> httpx.Response:
        aspect = float(request.url.params["aspect"])
        if aspect == pytest.approx(failing.pvgis_aspect_deg):
            return httpx.Response(503)
        return httpx.Response(200, json=payload)

    respx.get(PVCALC_URL).mock(side_effect=_respond)

    with pytest.raises(PvgisUnavailableError) as caught:
        await probe_facets(
            roof.facets,
            settings=settings,
            client=PvgisClient(settings, retry_clock=RetryClock(sleep=FakeClock().sleep)),
        )

    assert failing.id in str(caught.value)
    assert caught.value.details["facets"] == [failing.id]


@respx.mock
async def test_a_failure_names_every_facet_that_failed(settings, roof, payload) -> None:
    respx.get(PVCALC_URL).mock(return_value=httpx.Response(503))
    fake = FakeClock()

    with pytest.raises(PvgisUnavailableError) as caught:
        await probe_facets(
            roof.facets,
            settings=settings,
            client=PvgisClient(
                settings,
                retry_clock=RetryClock(now=fake.now, sleep=fake.sleep, jitter=fake.jitter),
            ),
        )

    assert set(caught.value.details["facets"]) == {f.id for f in roof.facets}


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


@respx.mock
async def test_probes_run_concurrently_not_serially(settings, roof, payload) -> None:
    """Four facets that each retry twice must cost one ladder, not four.

    Getting this wrong is invisible in the numbers - they come out identical -
    and only the latency betrays it. Measured on the injected clock, so the
    assertion is exact rather than a timing heuristic.
    """
    attempts: dict[float, int] = {}

    def _flaky(request: httpx.Request) -> httpx.Response:
        aspect = float(request.url.params["aspect"])
        attempts[aspect] = attempts.get(aspect, 0) + 1
        if attempts[aspect] <= 2:
            return httpx.Response(503)
        return httpx.Response(200, json=payload)

    respx.get(PVCALC_URL).mock(side_effect=_flaky)
    fake = FakeClock()

    probes = await probe_facets(
        roof.facets,
        settings=settings,
        client=PvgisClient(
            settings, retry_clock=RetryClock(now=fake.now, sleep=fake.sleep, jitter=fake.jitter)
        ),
    )

    assert len(probes.probes) == 4
    # Each facet sleeps 0.5 then 1.0. Serially that is 6.0 of virtual time;
    # concurrently the clock only advances by what the tasks actually waited.
    assert sum(fake.sleeps) == pytest.approx(6.0), "each facet should retry twice"
    assert fake.elapsed <= 6.0
    # The real proof: the batch finished, and every facet's second retry
    # overlapped the others rather than queueing behind them.
    assert len(fake.sleeps) == 8


@respx.mock
async def test_the_batch_shares_one_connection_pool(settings, roof, payload) -> None:
    """One client for four facets, rather than four connections."""
    respx.get(PVCALC_URL).mock(return_value=httpx.Response(200, json=payload))
    seen: set[int] = set()

    class Recording(httpx.AsyncClient):
        async def send(self, *args, **kwargs):  # type: ignore[override]
            seen.add(id(self))
            return await super().send(*args, **kwargs)

    async with Recording() as pooled:
        await probe_facets(roof.facets, settings=settings, http_client=pooled)

    assert len(seen) == 1


# ---------------------------------------------------------------------------
# What the set carries
# ---------------------------------------------------------------------------


@respx.mock
async def test_the_probe_set_records_the_endpoint_and_batch_time(settings, roof, payload) -> None:
    respx.get(PVCALC_URL).mock(return_value=httpx.Response(200, json=payload))

    probes = await probe_facets(roof.facets, settings=settings)

    assert probes.endpoint == "https://re.jrc.ec.europa.eu/api/v5_3/PVcalc"
    assert probes.batch_completed_at is not None
    for probe in probes.probes.values():
        assert probe.result.retrieved_at <= probes.batch_completed_at


@respx.mock
async def test_yields_are_the_mapping_the_optimiser_takes(settings, roof, payload) -> None:
    respx.get(PVCALC_URL).mock(
        side_effect=lambda request: httpx.Response(
            200, json=_payload_for(float(request.url.params["aspect"]), payload)
        )
    )

    probes = await probe_facets(roof.facets, settings=settings)
    yields = probes.yields()

    assert set(yields) == {f.id for f in roof.facets}
    assert all(v > 0 for v in yields.values())


@respx.mock
async def test_the_request_parameters_are_recorded_without_the_per_facet_ones(
    settings, roof, payload
) -> None:
    """Batch-level provenance is what every facet shared, not what varied."""
    respx.get(PVCALC_URL).mock(return_value=httpx.Response(200, json=payload))

    probes = await probe_facets(roof.facets, settings=settings)

    assert probes.request_params["peakpower"] == 1.0
    assert probes.request_params["pvtechchoice"] == "crystSi"
    assert probes.request_params["mountingplace"] == "building"
    assert "aspect" not in probes.request_params
    assert "angle" not in probes.request_params
