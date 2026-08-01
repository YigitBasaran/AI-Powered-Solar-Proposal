"""What the snapshot records about where its production figures came from.

A proposal is a document someone acts on. "9,502 kWh a year" is only as good as
the answer to "from where, when, and at what version" - and that answer has to
survive being frozen into an immutable snapshot, because by the time anyone
asks, the observation is long gone.
"""

from __future__ import annotations

import pytest

CASE_COORD = "-34.04658242871865, 18.46491476666948"
CASE_FACETS = {"facet_n", "facet_s", "facet_e", "facet_w"}


def _analysed(client, size: str = "6 kWp") -> dict:
    project_id = client.post("/api/v1/projects").json()["projectId"]
    for message in (CASE_COORD, "1,150 kWh", size):
        client.post(f"/api/v1/projects/{project_id}/chat", json={"message": message})
    response = client.post(f"/api/v1/projects/{project_id}/run-analysis")
    assert response.status_code == 200, response.text
    return response.json()["analysis"]


@pytest.fixture(scope="module")
def provenance(client) -> dict:
    return _analysed(client)["energy"]["pvgis"]


# ---------------------------------------------------------------------------
# The block itself
# ---------------------------------------------------------------------------


def test_provenance_names_the_endpoint_and_its_identity(provenance) -> None:
    assert provenance["endpoint"].endswith("/PVcalc")
    assert provenance["origin"].startswith("http")
    assert provenance["apiVersion"] == "v5_3"
    # The suite answers from a local stub, which is not the canonical origin.
    assert provenance["source"] == "replay"


def test_every_roof_facet_is_recorded_not_just_the_occupied_ones(provenance) -> None:
    """Four probes at 6 kWp, though only three facets receive panels.

    This is what lets a later size change be answered without asking PVGIS the
    same four questions again.
    """
    assert {p["facetId"] for p in provenance["probes"]} == CASE_FACETS


def test_provenance_names_both_angle_conventions(provenance) -> None:
    """`aspect` alone is ambiguous, and the ambiguity is a different roof plane.

    PVGIS counts azimuth from south; the compass counts from north. Recording
    one under a name that could mean either is how a figure ends up attributed
    to the wrong facet.
    """
    for probe in provenance["probes"]:
        assert "compassAzimuthDeg" in probe
        assert "pvgisAspectDeg" in probe
        delta = abs((probe["compassAzimuthDeg"] - probe["pvgisAspectDeg"]) % 360.0)
        assert delta == pytest.approx(180.0, abs=1e-4), probe["facetId"]


def test_the_reuse_key_is_kept_at_full_precision(provenance) -> None:
    """`energy.facets[]` rounds for reading; provenance is a comparison key."""
    from app.services.roof import build_roof_model

    aspects = {p["facetId"]: p["pvgisAspectDeg"] for p in provenance["probes"]}
    expected = build_roof_model().facet("facet_n").pvgis_aspect_deg
    # The payload rounds to six decimals; the model carries full float precision.
    assert aspects["facet_n"] == pytest.approx(expected, abs=1e-6)
    # Two decimals would collapse -169.467751 and -169.47 into one value; full
    # precision keeps the equality check meaningful.
    assert round(aspects["facet_n"], 2) != aspects["facet_n"]


def test_batch_and_probe_timestamps_are_distinct(provenance) -> None:
    """One `retrievedAt` at the top would have meant either, and read as both."""
    assert "batchCompletedAt" in provenance
    assert "retrievedAt" not in provenance

    for probe in provenance["probes"]:
        assert probe["retrievedAt"] <= provenance["batchCompletedAt"]


def test_the_request_parameters_are_recorded(provenance) -> None:
    request = provenance["request"]
    assert request["peakPowerKwp"] == 1.0
    assert request["pvTechChoice"] == "crystSi"
    assert request["mountingPlace"] == "building"
    assert request["lossPercent"] == 14.0


def test_the_normalised_monthly_yields_are_kept(provenance) -> None:
    """What makes production at any size a multiplication rather than a call."""
    for probe in provenance["probes"]:
        monthly = probe["monthlySpecificYieldKwhPerKwp"]
        assert len(monthly) == 12
        assert sum(monthly) == pytest.approx(probe["specificYieldKwhPerKwp"], rel=0.02)


def test_the_loss_breakdown_is_recorded_where_pvgis_returned_one(provenance) -> None:
    """PVGIS computes the angle-of-incidence loss; this repo never does."""
    for probe in provenance["probes"]:
        losses = probe["losses"]
        assert losses is not None
        assert losses["angleOfIncidencePercent"] < 0
        assert "totalPercent" in losses


def test_one_radiation_database_for_the_whole_batch(provenance) -> None:
    """Disagreement is a failure, so any snapshot that exists has exactly one."""
    assert provenance["radiationDatabase"] == "PVGIS-SARAH3"
    assert {p["radiationDatabase"] for p in provenance["probes"]} == {"PVGIS-SARAH3"}


# ---------------------------------------------------------------------------
# What it does *not* depend on
# ---------------------------------------------------------------------------


def test_provenance_does_not_depend_on_the_system_size(client) -> None:
    """The fact that licenses reusing probes across a size change.

    The probes are taken at 1 kWp before any size is chosen, so two analyses of
    the same roof differ in their layout and their production and in nothing
    about where the numbers came from.
    """
    from app.services.conversation.invalidation import DEPENDS_ON_SYSTEM_SIZE

    small = _analysed(client, "3.6 kWp")["energy"]["pvgis"]
    large = _analysed(client, "9.6 kWp")["energy"]["pvgis"]

    for a, b in zip(
        sorted(small["probes"], key=lambda p: p["facetId"]),
        sorted(large["probes"], key=lambda p: p["facetId"]),
        strict=True,
    ):
        assert a["specificYieldKwhPerKwp"] == b["specificYieldKwhPerKwp"]
        assert a["pvgisAspectDeg"] == b["pvgisAspectDeg"]
        assert a["monthlySpecificYieldKwhPerKwp"] == b["monthlySpecificYieldKwhPerKwp"]

    # And the declared dependency map says so too: no provenance path is listed.
    assert not [p for p in DEPENDS_ON_SYSTEM_SIZE if p.startswith("energy.pvgis")]


def test_a_consumption_change_carries_provenance_across_untouched(client) -> None:
    project_id = client.post("/api/v1/projects").json()["projectId"]
    for message in (CASE_COORD, "1,150 kWh", "6 kWp"):
        client.post(f"/api/v1/projects/{project_id}/chat", json={"message": message})
    before = client.post(f"/api/v1/projects/{project_id}/run-analysis").json()["analysis"]

    client.post(
        f"/api/v1/projects/{project_id}/chat",
        json={"message": "actually make it 400 kWh a month"},
    )
    after = client.get(f"/api/v1/projects/{project_id}").json()["analysis"]

    assert after["energy"]["pvgis"] == before["energy"]["pvgis"], (
        "a consumption change must not touch where the production figures came from"
    )


# ---------------------------------------------------------------------------
# Batch consistency
# ---------------------------------------------------------------------------


async def test_disagreeing_radiation_databases_fail_the_whole_analysis(
    pvgis_stub, offline_env
) -> None:
    """Two datasets means the four yields are not comparable with each other.

    The optimiser ranks facets against one another, so a yield drawn from a
    different dataset does not merely add noise - it silently changes which
    roof planes get panels. The service answered fine, which is why this has
    its own code rather than being reported as an outage.
    """
    from app.core.config import get_settings
    from app.core.errors import PvgisInconsistentProvenanceError
    from app.services.analysis import run_analysis
    from app.services.roof import build_roof_model

    stub_url, _ = pvgis_stub
    # The fault is scoped to one facet's aspect, so it has to track the
    # calibration: a literal here silently stops injecting anything the moment
    # the roof is re-traced, and the test then passes for the wrong reason.
    south = round(build_roof_model().facet("facet_s").pvgis_aspect_deg, 2)
    settings = get_settings().model_copy(
        update={"pvgis_base_url": f"{stub_url}/__fault/mixed-raddb/{south}/api/v5_3"}
    )

    with pytest.raises(PvgisInconsistentProvenanceError) as caught:
        await run_analysis(
            monthly_consumption_kwh=1150.0, system_size_kwp=6.0, settings=settings
        )

    assert caught.value.code == "PVGIS_INCONSISTENT_PROVENANCE"
    detail = caught.value.details["radiationDatabases"]
    assert set(detail.values()) == {"PVGIS-SARAH3", "PVGIS-ERA5"}
    # Named per facet, so an operator can see which one disagreed.
    assert detail["facet_s"] == "PVGIS-ERA5"


async def test_a_consistent_batch_reports_the_single_database(pvgis_stub, offline_env) -> None:
    from app.core.config import get_settings
    from app.services.analysis import run_analysis, single_radiation_database

    stub_url, _ = pvgis_stub
    settings = get_settings().model_copy(update={"pvgis_base_url": f"{stub_url}/api/v5_3"})

    result = await run_analysis(
        monthly_consumption_kwh=1150.0, system_size_kwp=6.0, settings=settings
    )

    assert result.probes is not None
    assert single_radiation_database(result.probes) == "PVGIS-SARAH3"
    assert result.yield_result.radiation_database == "PVGIS-SARAH3"
