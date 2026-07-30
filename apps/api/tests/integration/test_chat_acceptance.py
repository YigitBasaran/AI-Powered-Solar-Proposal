"""The conversational acceptance examples, end to end through the API.

Each of these was a reported defect before it was a test. They run against the
real route, the real state machine and the real database, because the failures
they cover were all *integration* failures - the router said one thing, the step
validator did another, and the customer saw the difference.

Four properties every example is checked against:

1. an update changes state,
2. a question changes nothing,
3. an ambiguous message asks rather than guesses,
4. the pending workflow question comes back afterwards.
"""

from __future__ import annotations

import pytest

CASE_COORD = "-34.04658242871865, 18.46491476666948"


def _say(client, project_id: str, message: str) -> dict:
    response = client.post(f"/api/v1/projects/{project_id}/chat", json={"message": message})
    assert response.status_code == 200, response.text
    return response.json()


def _project(client, project_id: str) -> dict:
    return client.get(f"/api/v1/projects/{project_id}").json()


@pytest.fixture
def at_system_size(client):
    """A project sitting on the system-size question, as the examples specify."""
    project_id = client.post("/api/v1/projects").json()["projectId"]
    _say(client, project_id, CASE_COORD)
    _say(client, project_id, "1,150 kWh")
    assert _project(client, project_id)["currentStep"] == "system_size"
    return project_id


# ---------------------------------------------------------------------------
# Example 1 - an update from another step
# ---------------------------------------------------------------------------


def test_annual_consumption_can_be_changed_from_the_system_size_step(client, at_system_size):
    reply = _say(
        client, at_system_size, "I want to change my annual electricity consumption to 10000."
    )

    project = _project(client, at_system_size)
    assert project["monthlyConsumptionKwh"] == pytest.approx(833.33, abs=0.01), (
        "10,000 kWh a year is 833.33 a month; an annual figure must not be stored as monthly"
    )
    assert project["annualConsumptionKwh"] == pytest.approx(10000.0, abs=0.2)
    assert project["currentStep"] == "system_size", "the workflow moved"
    assert "kWp" in reply["assistantMessage"], "the pending question was not restated"


def test_an_action_request_written_as_a_question_still_updates(client, at_system_size):
    """The routing rule that has to beat the question detector.

    Answering this as a question explains what consumption means and changes
    nothing, which is the most annoying possible response to a clear
    instruction.
    """
    _say(client, at_system_size, "Can you change my annual consumption to 10000?")

    assert _project(client, at_system_size)["monthlyConsumptionKwh"] == pytest.approx(
        833.33, abs=0.01
    )


def test_a_tariff_request_written_as_a_question_is_applied(client, at_system_size):
    """A price, filed as a price.

    The property that matters is that 0.31 does not land in some other field -
    which is exactly what happened while `use` was a consumption word, and the
    customer's consumption was silently rewritten to 0.31 kWh a month.
    """
    before = _project(client, at_system_size)["monthlyConsumptionKwh"]
    reply = _say(client, at_system_size, "Could you use 0.31 EUR/kWh instead?")

    assert "tariff" in reply["assistantMessage"].lower()
    assert _project(client, at_system_size)["monthlyConsumptionKwh"] == before, (
        "a tariff request rewrote the consumption"
    )


# ---------------------------------------------------------------------------
# Examples 2 and 3 - questions that must not disturb the workflow
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "Why would 6 kWp be suitable for me?",
        "What is the difference between kW, kWp and kWh?",
        "How is payback worked out?",
    ],
)
def test_a_question_answers_without_moving_or_changing_anything(client, at_system_size, question):
    before = _project(client, at_system_size)
    reply = _say(client, at_system_size, question)
    after = _project(client, at_system_size)

    assert len(reply["assistantMessage"]) > 40
    assert after["currentStep"] == before["currentStep"]
    assert after["monthlyConsumptionKwh"] == before["monthlyConsumptionKwh"]
    assert after["selectedSystemSizeKwp"] == before["selectedSystemSizeKwp"]


# ---------------------------------------------------------------------------
# Examples 4 and 5 - ambiguity is asked about, never guessed
# ---------------------------------------------------------------------------


def test_a_bare_figure_asks_which_value_it_is(client, at_system_size):
    """`10000` is not a system size, and refusing it as one names the wrong subject."""
    before = _project(client, at_system_size)
    reply = _say(client, at_system_size, "10000")
    after = _project(client, at_system_size)

    text = reply["assistantMessage"]
    assert "?" in text
    assert "isn't one of the three" not in text.lower(), "still answering as though it were a size"

    assert after["monthlyConsumptionKwh"] == before["monthlyConsumptionKwh"]
    assert after["selectedSystemSizeKwp"] == before["selectedSystemSizeKwp"]


def test_an_unlabelled_correction_asks_which_value(client, at_system_size):
    """"Actually make it 10000" used to silently resolve to a system size."""
    before = _project(client, at_system_size)
    reply = _say(client, at_system_size, "Actually make it 10000")
    after = _project(client, at_system_size)

    assert "?" in reply["assistantMessage"]
    assert after["monthlyConsumptionKwh"] == before["monthlyConsumptionKwh"]
    assert after["selectedSystemSizeKwp"] == before["selectedSystemSizeKwp"]


# ---------------------------------------------------------------------------
# Example 6 - out of scope, without losing the customer's place
# ---------------------------------------------------------------------------


def test_an_unrelated_question_keeps_the_customers_place(client, at_system_size):
    reply = _say(client, at_system_size, "who won the world cup in 1998?")

    assert _project(client, at_system_size)["currentStep"] == "system_size"
    assert len(reply["assistantMessage"]) > 20


# ---------------------------------------------------------------------------
# The explicit fast paths still work, so clarification is not the new default
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "expected"),
    [("6 kWp", 6.0), ("9.6 kWp", 9.6), ("15 panels", 6.0), ("the largest option", 9.6)],
)
def test_an_explicit_answer_is_still_taken_directly(client, at_system_size, message, expected):
    _say(client, at_system_size, message)
    assert _project(client, at_system_size)["selectedSystemSizeKwp"] == expected


def test_a_consumption_figure_is_still_taken_at_its_own_step(client):
    project_id = client.post("/api/v1/projects").json()["projectId"]
    _say(client, project_id, CASE_COORD)
    _say(client, project_id, "1150")

    assert _project(client, project_id)["monthlyConsumptionKwh"] == 1150.0


# ---------------------------------------------------------------------------
# An update recalculates its dependents, and only those
# ---------------------------------------------------------------------------


def test_an_update_after_analysis_recalculates_the_dependent_figures(client):
    project_id = client.post("/api/v1/projects").json()["projectId"]
    for message in (CASE_COORD, "1,150 kWh", "6 kWp"):
        _say(client, project_id, message)
    assert client.post(f"/api/v1/projects/{project_id}/run-analysis").status_code == 200

    before = _project(client, project_id)["analysis"]
    _say(client, project_id, "Change my consumption to 400 kWh a month")
    after = _project(client, project_id)["analysis"]

    assert _project(client, project_id)["monthlyConsumptionKwh"] == 400.0
    # Production is a property of the roof and the system size, not of usage.
    assert (
        after["energy"]["totalAnnualProductionKwh"]
        == before["energy"]["totalAnnualProductionKwh"]
    )
    # Coverage and savings are properties of usage, so they must move.
    assert after["financial"]["coveragePercent"] != before["financial"]["coveragePercent"]


# ---------------------------------------------------------------------------
# The tariff is a real, stored, per-project value
# ---------------------------------------------------------------------------


def test_a_tariff_change_moves_the_savings_and_leaves_production_alone(client):
    """The dependency rule for a tariff, asserted rather than assumed.

    Electricity price has no bearing on how much sun falls on a roof, so
    production must not move. It has every bearing on what the electricity is
    worth, so savings and payback must.
    """
    project_id = client.post("/api/v1/projects").json()["projectId"]
    for message in (CASE_COORD, "1,150 kWh", "6 kWp"):
        _say(client, project_id, message)
    assert client.post(f"/api/v1/projects/{project_id}/run-analysis").status_code == 200

    before = _project(client, project_id)["analysis"]
    _say(client, project_id, "My tariff is actually 0.31 EUR/kWh")
    after = _project(client, project_id)["analysis"]

    # Serialised as a string, because money is a Decimal all the way down.
    assert float(after["financial"]["electricityPriceEurPerKwh"]) == pytest.approx(0.31)
    assert float(after["financial"]["annualSavingsEur"]) > float(
        before["financial"]["annualSavingsEur"]
    ), (
        "a higher tariff makes the same generation worth more"
    )
    assert float(after["financial"]["simplePaybackYears"]) < float(
        before["financial"]["simplePaybackYears"]
    )

    assert (
        after["energy"]["totalAnnualProductionKwh"]
        == before["energy"]["totalAnnualProductionKwh"]
    ), "the price of electricity moved the modelled production"
    assert after["layout"]["placedPanelCount"] == before["layout"]["placedPanelCount"]


def test_an_implausible_tariff_is_refused_rather_than_stored(client, at_system_size):
    """Zero makes payback infinite; both would render as a confident figure."""
    reply = _say(client, at_system_size, "Change my tariff to 0 EUR/kWh")

    assert "doesn't look right" in reply["assistantMessage"]
    project = _project(client, at_system_size)
    assert project["currentStep"] == "system_size"
