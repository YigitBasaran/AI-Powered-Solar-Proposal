"""Changing a value after the analysis, and starting over.

Two behaviours that used to be impossible and one that used to be too easy.

A change recalculates **only** what depends on it. That is asserted by
comparing the untouched sections byte for byte rather than by trusting the
call graph - "we only re-ran the financials" is exactly the kind of claim that
is true when written and false a refactor later.

A reset asks first. One word wiping consumption, system size and the analysis
is a hostile default, and the confirmation is honoured only when it answers the
immediately preceding offer.
"""

from __future__ import annotations

import json

CASE_COORD = "-34.04658242871865, 18.46491476666948"


def _say(client, project_id: str, message: str) -> dict:
    response = client.post(f"/api/v1/projects/{project_id}/chat", json={"message": message})
    assert response.status_code == 200, response.text
    return response.json()


def _project(client, project_id: str) -> dict:
    return client.get(f"/api/v1/projects/{project_id}").json()


def _analysed(client, size: str = "6 kWp") -> str:
    project_id = client.post("/api/v1/projects").json()["projectId"]
    for message in (CASE_COORD, "1,150 kWh", size):
        _say(client, project_id, message)
    client.post(f"/api/v1/projects/{project_id}/run-analysis")
    return project_id


# ---------------------------------------------------------------------------
# Changing consumption
# ---------------------------------------------------------------------------


def test_changing_consumption_recalculates_the_financials_and_nothing_else(client) -> None:
    project_id = _analysed(client)
    before = _project(client, project_id)["analysis"]

    body = _say(client, project_id, "actually make it 900 kWh a month")
    assert body["accepted"] is True
    assert body["currentStep"] == "proposal", "a correction does not rewind the rail"

    after = _project(client, project_id)
    assert after["monthlyConsumptionKwh"] == 900.0
    assert after["analysisStatus"] == "complete"

    for section in ("roof", "layout", "energy", "exchangeRate"):
        assert json.dumps(after["analysis"][section], sort_keys=True) == json.dumps(
            before[section], sort_keys=True
        ), f"{section} was rebuilt by a consumption change"

    assert after["analysis"]["financial"]["annualConsumptionKwh"] == 10_800.0
    assert after["analysis"]["financial"] != before["financial"]


def test_the_reply_says_what_moved_and_what_did_not(client) -> None:
    project_id = _analysed(client)
    message = _say(client, project_id, "actually make it 900 kWh a month")["assistantMessage"]

    assert "900" in message
    assert "recalculating" in message.lower()
    for preserved in ("roof", "exchange rate"):
        assert preserved in message.lower()


def test_changing_the_system_size_recalculates_the_layout_but_not_the_rate(client) -> None:
    project_id = _analysed(client)
    before = _project(client, project_id)["analysis"]

    _say(client, project_id, "change it to the largest option")
    after = _project(client, project_id)

    assert after["selectedSystemSizeKwp"] == 9.6
    assert after["analysis"]["layout"]["placedPanelCount"] == 24
    assert (
        after["analysis"]["energy"]["totalAnnualProductionKwh"]
        != before["energy"]["totalAnnualProductionKwh"]
    )
    # The rate the customer was quoted is an observation already made.
    assert json.dumps(after["analysis"]["exchangeRate"], sort_keys=True) == json.dumps(
        before["exchangeRate"], sort_keys=True
    )
    assert json.dumps(after["analysis"]["roof"], sort_keys=True) == json.dumps(
        before["roof"], sort_keys=True
    )


def test_a_selective_recompute_matches_a_fresh_analysis_of_the_same_inputs(client) -> None:
    """The shortcut is a shortcut, not a different answer."""
    edited = _analysed(client, "6 kWp")
    _say(client, edited, "change it to the largest option")

    fresh = _analysed(client, "9.6 kWp")

    a = _project(client, edited)["analysis"]
    b = _project(client, fresh)["analysis"]
    for section in ("roof", "layout", "energy", "financial"):
        assert json.dumps(a[section], sort_keys=True) == json.dumps(b[section], sort_keys=True)


def test_a_stale_project_cannot_be_finalised(client, monkeypatch) -> None:
    project_id = _analysed(client)

    async def _boom(**_kwargs):
        raise RuntimeError("PVGIS unavailable")

    monkeypatch.setattr("app.services.analysis.recompute_for_system_size", _boom)
    _say(client, project_id, "change it to the largest option")

    assert _project(client, project_id)["analysisStatus"] == "stale"
    refused = client.post(f"/api/v1/projects/{project_id}/finalize")
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "PROPOSAL_INCOMPLETE"


def test_a_stale_project_withholds_the_affected_figures_from_answers(client, monkeypatch) -> None:
    """The number is still in the database; it must not reach the customer."""
    project_id = _analysed(client)
    stale_payback = _project(client, project_id)["analysis"]["financial"]["simplePaybackYears"]

    async def _boom(**_kwargs):
        raise RuntimeError("PVGIS unavailable")

    monkeypatch.setattr("app.services.analysis.recompute_for_system_size", _boom)
    _say(client, project_id, "change it to the largest option")

    answer = _say(client, project_id, "what is my payback?")["assistantMessage"]
    assert f"{stale_payback:.1f}" not in answer
    assert "don't have that yet" in answer or "recalculating" in answer.lower()

    # The roof does not depend on the system size, so it still answers.
    roof = _say(client, project_id, "how big is the roof?")["assistantMessage"]
    assert "m2" in roof


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------


def test_starting_over_asks_before_wiping_anything(client) -> None:
    project_id = _analysed(client)
    body = _say(client, project_id, "start over")

    assert "Shall I go ahead" in body["assistantMessage"]
    after = _project(client, project_id)
    assert after["monthlyConsumptionKwh"] == 1150.0, "nothing is cleared by the question"
    assert after["analysis"] is not None


def test_confirming_the_reset_clears_the_project(client) -> None:
    project_id = _analysed(client)
    _say(client, project_id, "start over")
    body = _say(client, project_id, "yes")

    assert body["currentStep"] == "location"
    after = _project(client, project_id)
    assert after["monthlyConsumptionKwh"] is None
    assert after["selectedSystemSizeKwp"] is None
    assert after["analysis"] is None
    assert after["analysisStatus"] == "pending"


def test_a_yes_that_answers_nothing_does_not_reset(client) -> None:
    """The offer is honoured only as the answer to the message before it."""
    project_id = _analysed(client)
    _say(client, project_id, "start over")
    _say(client, project_id, "how is payback calculated?")
    _say(client, project_id, "yes")

    after = _project(client, project_id)
    assert after["monthlyConsumptionKwh"] == 1150.0, "a stale offer was honoured"
    assert after["analysis"] is not None


def test_asking_how_to_start_over_explains_rather_than_starting_over(client) -> None:
    """A question about the mechanism is not an invocation of it."""
    project_id = _analysed(client)
    body = _say(client, project_id, "how do I start over?")

    assert "Shall I go ahead" not in body["assistantMessage"]
    assert _project(client, project_id)["monthlyConsumptionKwh"] == 1150.0
