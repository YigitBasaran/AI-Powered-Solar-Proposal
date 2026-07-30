"""End-to-end API tests for the chat-driven workflow.

Everything runs in fixture mode with `LLM_PROVIDER=rules`, which is exactly the
configuration a reviewer gets from a clean clone with no credentials and no
model pulled. If this suite passes, the submission runs out of the box.
"""

from __future__ import annotations

import pytest

CASE_COORD = "-34.04658242871865, 18.46491476666948"


def start_project(client) -> str:
    response = client.post("/api/v1/projects")
    assert response.status_code == 201
    return response.json()["projectId"]


def say(client, project_id: str, message: str) -> dict:
    response = client.post(f"/api/v1/projects/{project_id}/chat", json={"message": message})
    assert response.status_code == 200
    return response.json()


def complete_intake(client, size_message: str = "the middle option") -> str:
    project_id = start_project(client)
    say(client, project_id, CASE_COORD)
    say(client, project_id, "1,150 kWh")
    say(client, project_id, size_message)
    return project_id


# ---------------------------------------------------------------------------
# Intake
# ---------------------------------------------------------------------------


def test_new_project_opens_with_the_solarvis_welcome(client) -> None:
    body = client.post("/api/v1/projects").json()
    assert body["currentStep"] == "location"
    assert "solarVis AI" in body["assistantMessage"]
    assert "latitude and longitude" in body["assistantMessage"]


def test_progress_rail_has_the_nine_case_steps(client) -> None:
    body = client.post("/api/v1/projects").json()
    labels = [p["label"] for p in body["progress"]]
    assert labels == [
        "Location",
        "Usage",
        "System",
        "Roof",
        "Layout",
        "Yield",
        "FX",
        "Finance",
        "Proposal",
    ]
    assert body["progress"][0]["state"] == "active"


def test_location_resolves_to_the_fixed_case_property(client) -> None:
    project_id = start_project(client)
    body = say(client, project_id, CASE_COORD)

    assert body["currentStep"] == "consumption"
    assert body["parserSource"] == "rules"
    assert "Cape Town" in body["assistantMessage"]

    project = client.get(f"/api/v1/projects/{project_id}").json()
    assert project["resolvedLatitude"] == pytest.approx(-34.04658242871865)


def test_raw_location_input_is_preserved_verbatim(client) -> None:
    project_id = start_project(client)
    say(client, project_id, "somewhere near the station, -34.0466 18.4649")
    project = client.get(f"/api/v1/projects/{project_id}").json()
    assert project["rawLocationInput"] == "somewhere near the station, -34.0466 18.4649"


def test_a_location_elsewhere_is_blocked_and_the_case_property_offered(client) -> None:
    """Behaviour change, deliberate: London used to be silently accepted.

    The old build stored any input as `raw_location_input` and then analysed
    Cape Town's roof under it. Every figure downstream was then labelled with a
    property it did not describe. The honest reply says so and offers the one
    property this build can actually analyse - and stores nothing.
    """
    project_id = start_project(client)
    body = say(client, project_id, "51.5074, -0.1278")

    assert body["accepted"] is False
    assert body["currentStep"] == "location"
    assert "-34.046582" in body["assistantMessage"]

    project = client.get(f"/api/v1/projects/{project_id}").json()
    assert project["rawLocationInput"] is None, "nothing is stored for a refused location"
    assert project["resolvedLatitude"] is None


def test_confirming_the_offer_records_the_case_property(client) -> None:
    """The second half of block-and-offer: "yes" is a real answer to it."""
    project_id = start_project(client)
    say(client, project_id, "51.5074, -0.1278")
    body = say(client, project_id, "yes")

    assert body["accepted"] is True
    assert body["currentStep"] == "consumption"

    project = client.get(f"/api/v1/projects/{project_id}").json()
    assert project["resolvedLatitude"] == pytest.approx(-34.04658242871865)
    # Not the word "yes": the field means which property was chosen.
    assert "Galway Road" in project["rawLocationInput"]


def test_the_brief_positive_latitude_is_accepted_as_the_case_property(client) -> None:
    """The documented sign error identifies this property, not a point at sea."""
    project_id = start_project(client)
    body = say(client, project_id, "34.04658242871865, 18.46491476666948")

    assert body["accepted"] is True
    assert body["currentStep"] == "consumption"
    project = client.get(f"/api/v1/projects/{project_id}").json()
    assert project["resolvedLatitude"] == pytest.approx(-34.04658242871865)


def test_consumption_is_multiplied_out_deterministically(client) -> None:
    project_id = start_project(client)
    say(client, project_id, CASE_COORD)
    body = say(client, project_id, "1,150 kWh")

    assert "13,800 kWh/year" in body["assistantMessage"]
    assert "0.25/kWh" in body["assistantMessage"]

    project = client.get(f"/api/v1/projects/{project_id}").json()
    assert project["monthlyConsumptionKwh"] == 1150.0
    assert project["annualConsumptionKwh"] == 13800.0


def test_exactly_three_system_sizes_are_offered(client) -> None:
    project_id = start_project(client)
    say(client, project_id, CASE_COORD)
    message = say(client, project_id, "1,150 kWh")["assistantMessage"]

    assert "3.6 kWp" in message
    assert "6 kWp" in message
    assert "9.6 kWp" in message
    assert "custom" not in message.lower()


@pytest.mark.parametrize(
    ("reply", "size", "panels"),
    [
        ("3.6", 3.6, 9),
        ("the middle option", 6.0, 15),
        ("largest", 9.6, 24),
        ("fifteen panels", 6.0, 15),
        ("twenty-four panels", 9.6, 24),
    ],
)
def test_system_size_selection_derives_the_panel_count(client, reply, size, panels) -> None:
    project_id = start_project(client)
    say(client, project_id, CASE_COORD)
    say(client, project_id, "1,150 kWh")
    body = say(client, project_id, reply)

    assert body["currentStep"] == "roof_reconstruction"
    assert body["readyForAnalysis"] is True
    assert f"{panels} panels" in body["assistantMessage"]

    project = client.get(f"/api/v1/projects/{project_id}").json()
    assert project["selectedSystemSizeKwp"] == size
    assert project["requestedPanelCount"] == panels


# ---------------------------------------------------------------------------
# Validation and step discipline
# ---------------------------------------------------------------------------


def test_unreadable_location_is_rejected_without_advancing(client) -> None:
    """Neither coordinates nor words is not an answer to "where?".

    A written place *is* accepted - geocoding is out of scope and the analysis
    always runs at the verified case coordinate, so an address is recorded
    verbatim rather than demanded away.
    """
    project_id = start_project(client)
    body = say(client, project_id, "???")
    assert body["accepted"] is False
    assert body["currentStep"] == "location"


def test_a_written_address_is_blocked_because_nothing_here_geocodes(client) -> None:
    """Behaviour change, deliberate: the address used to be accepted verbatim.

    There is no geocoder in this build, so an address cannot be checked against
    the calibrated property. Accepting it meant claiming a match that had never
    been tested.
    """
    project_id = start_project(client)
    body = say(client, project_id, "10 Downing Street, London")
    assert body["accepted"] is False
    assert body["currentStep"] == "location"
    # The refusal names what was refused, and what is on offer instead.
    assert "10 Downing Street, London" in body["assistantMessage"]
    assert "-34.046582" in body["assistantMessage"]


def test_unsupported_system_size_is_refused(client) -> None:
    project_id = start_project(client)
    say(client, project_id, CASE_COORD)
    say(client, project_id, "1,150 kWh")
    body = say(client, project_id, "5 kWp")

    assert body["accepted"] is False
    assert body["currentStep"] == "system_size"


def test_a_consumption_figure_cannot_be_supplied_at_the_location_step(client) -> None:
    project_id = start_project(client)
    body = say(client, project_id, "1150")
    assert body["accepted"] is False
    assert body["currentStep"] == "location"


def test_over_long_messages_are_rejected(client) -> None:
    project_id = start_project(client)
    response = client.post(f"/api/v1/projects/{project_id}/chat", json={"message": "x" * 5000})
    assert response.status_code == 422


def test_unknown_project_returns_a_structured_error(client) -> None:
    response = client.post("/api/v1/projects/does-not-exist/chat", json={"message": "hello"})
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "NOT_FOUND"
    assert "requestId" in error


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def analysis(client) -> dict:
    project_id = complete_intake(client)
    response = client.post(f"/api/v1/projects/{project_id}/run-analysis")
    assert response.status_code == 200
    return response.json()["analysis"]


def test_analysis_places_the_requested_panels(analysis) -> None:
    assert analysis["layout"]["requestedPanelCount"] == 15
    assert analysis["layout"]["placedPanelCount"] == 15
    assert analysis["layout"]["feasibleSystemSizeKwp"] == 6.0
    assert analysis["layout"]["capacityWarning"] is None


def test_analysis_reports_every_roof_edge_with_a_measurement(analysis) -> None:
    edges = analysis["roof"]["edges"]
    assert len(edges) == 9
    assert {e["type"] for e in edges} == {"eave", "hip", "ridge"}
    for edge in edges:
        assert edge["projectedLengthM"] > 0


def test_analysis_reports_four_facets_with_pitch_and_aspect(analysis) -> None:
    facets = analysis["roof"]["facets"]
    assert len(facets) == 4
    for facet in facets:
        assert facet["slopedAreaM2"] > facet["projectedAreaM2"]
        assert -180 < facet["pvgisAspectDeg"] <= 180


def test_panels_are_reported_in_source_pixels_for_rendering(analysis) -> None:
    panels = [p for f in analysis["layout"]["facets"] for p in f["panels"]]
    assert len(panels) == 15
    for panel in panels:
        assert len(panel["sourcePixelPolygon"]) == 4


def test_energy_is_calculated_per_occupied_facet(analysis) -> None:
    """Facet production must sum to the total, within display rounding.

    The tolerance is deliberate. Facet figures and the total are each rounded
    to 0.01 kWh for presentation, so summing the parts can differ from the
    rounded whole by a few hundredths. Unlike the cash-flow table - where cents
    must reconcile exactly and the model canonicalises money once - a 0.01 kWh
    residue on ~9,500 kWh is far below PVGIS's own model uncertainty, and
    forcing agreement there would manufacture precision that does not exist.
    """
    facets = analysis["energy"]["facets"]
    assert len(facets) == len(analysis["layout"]["facets"])
    total = sum(f["annualProductionKwh"] for f in facets)
    assert total == pytest.approx(analysis["energy"]["totalAnnualProductionKwh"], abs=0.05)
    assert len(analysis["energy"]["totalMonthlyProductionKwh"]) == 12


def test_the_north_facet_is_used_and_the_south_facet_is_not(analysis) -> None:
    """Production-first allocation at a southern-hemisphere site."""
    used = {f["facetId"] for f in analysis["layout"]["facets"]}
    assert "facet_n" in used
    assert "facet_s" not in used
    assert {"facet_e", "facet_w"} <= used


def test_production_provenance_is_recorded(analysis) -> None:
    """Production is now always a real HTTP call, so there is no fixture label.

    Deliberate behaviour change: the suite answers that call from a local replay
    stub rather than reading captures off disk, so `dataSource` reflects the
    transport rather than a mode. Step 6 splits this into `live` vs `replay` by
    the trustworthiness of the endpoint; until then the honest assertion is that
    provenance is recorded at all.

    The suite answers that call from a local replay stub, which is not the
    canonical PVGIS origin - so the figure is labelled `replay`, never `live`.
    That distinction is what stops a replayed capture being finalised as though
    it were an observation, and it is also the assertion that would catch the
    stack quietly falling back to reading captures off disk.

    FX still has a fixture mode and is unaffected.
    """
    assert analysis["energy"]["dataSource"] == "replay"
    assert analysis["energy"]["radiationDatabase"] == "PVGIS-SARAH3"
    assert analysis["exchangeRate"]["isFixture"] is True
    assert analysis["exchangeRate"]["isLive"] is False


def test_capex_is_converted_and_both_currencies_reported(analysis) -> None:
    financial = analysis["financial"]
    assert financial["originalCapex"] == {"amount": "10000.00", "currency": "USD"}
    assert financial["convertedCapex"]["currency"] == "EUR"
    assert float(financial["convertedCapex"]["amount"]) < 10000.0
    assert analysis["exchangeRate"]["rate"] != "1"
    assert analysis["exchangeRate"]["dataProvider"] == "ECB"


def test_cash_flow_covers_twenty_one_years(analysis) -> None:
    flow = analysis["financial"]["cashFlow"]
    assert len(flow) == 21
    assert flow[0]["year"] == 0
    assert float(flow[0]["cumulativeCashFlowEur"]) < 0
    assert flow[-1]["cumulativeCashFlowEur"] == (analysis["financial"]["twentyYearNetBenefitEur"])


def test_savings_are_capped_at_consumption(analysis) -> None:
    financial = analysis["financial"]
    assert financial["coveredEnergyKwh"] <= financial["annualConsumptionKwh"]
    assert financial["annualConsumptionKwh"] == 13800.0


def test_analysis_is_persisted_on_the_project(client) -> None:
    project_id = complete_intake(client)
    client.post(f"/api/v1/projects/{project_id}/run-analysis")
    project = client.get(f"/api/v1/projects/{project_id}").json()
    assert project["analysisStatus"] == "complete"
    assert project["analysis"] is not None
    assert project["currentStep"] == "proposal"


@pytest.mark.parametrize(("reply", "panels"), [("3.6", 9), ("largest", 24)])
def test_the_other_two_system_sizes_also_complete(client, reply, panels) -> None:
    project_id = complete_intake(client, reply)
    analysis = client.post(f"/api/v1/projects/{project_id}/run-analysis").json()["analysis"]
    assert analysis["layout"]["placedPanelCount"] == panels
    assert analysis["financial"]["simplePaybackYears"] > 0


# ---------------------------------------------------------------------------
# Supporting endpoints
# ---------------------------------------------------------------------------


def test_satellite_image_is_served_same_origin_and_labelled(client) -> None:
    """Same origin, so the Konva canvas stays exportable and the key stays server-side.

    The suite answers from a local stub, so the source reads `stub` rather than
    `live` - and the imagery is deliberately not Google's, so it is reported as
    unverified. Both labels are the point: nothing downstream may mistake this
    for the raster the roof was calibrated against.
    """
    response = client.get("/api/v1/maps/satellite")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["X-Image-Source"] == "stub"
    assert response.headers["X-Imagery-Verified"] == "false"
    assert len(response.content) > 100_000


def test_map_config_publishes_scale_so_the_client_never_derives_it(client) -> None:
    config = client.get("/api/v1/maps/config").json()
    assert config["sourceWidthPx"] == 1280
    assert config["groundMetresPerSourcePixel"] == pytest.approx(0.06185, abs=1e-5)
    assert config["zoom"] == 20
    assert config["scale"] == 2
    assert config["attribution"]


def test_roof_endpoint_publishes_source_pixel_geometry(client) -> None:
    roof = client.get("/api/v1/roof/fixed-model").json()
    assert len(roof["vertices"]) == 6
    assert len(roof["facetGeometry"]) == 4
    assert roof["sourceWidthPx"] == 1280
    for facet in roof["facetGeometry"]:
        for point in facet["sourcePixelPolygon"]:
            assert 0 <= point["x"] <= 1280


def test_health_ready_reports_every_operating_mode(client) -> None:
    checks = client.get("/api/v1/health/ready").json()["checks"]
    assert checks["maps"]["mode"] == "fixture"
    assert checks["fx"]["mode"] == "fixture"

    # PVGIS has no mode: it is always a real call. What is reported is which
    # endpoint will be called and whether that configuration could back a
    # proposal - here the local replay stub, which could not.
    pvgis = checks["pvgis"]
    assert "127.0.0.1" in pvgis["endpoint"]
    assert pvgis["trusted"] is False
    assert pvgis["maxAttempts"] >= 1
    # Untrusted is fine in a test environment, so readiness is unaffected.
    assert pvgis["ready"] is True
    assert checks["llm"]["provider"] == "rules"
    assert checks["llm"]["ready"] is True


def test_case_location_endpoint_shows_both_coordinates(client) -> None:
    body = client.get("/api/v1/health/case-location").json()
    assert body["raw"]["latitude"] == pytest.approx(34.04658242871865)
    assert body["resolved"]["latitude"] == pytest.approx(-34.04658242871865)
    assert body["hemisphere"] == "southern"
    assert body["sourceVerified"] is True
