"""What the API says about who handled a message.

The old contract reported `parserSource: "rules"` both when the rules parser
succeeded and when Ollama was called and failed. A degraded stack was therefore
indistinguishable from a healthy one at the boundary - which is how a defect
that silenced the entire language layer survived a whole build.

The distinction is between *the model was never needed* and *the model was
asked and could not answer*. Only the second is a fallback, and only the second
is something to tell a customer about.
"""

from __future__ import annotations

import httpx
import respx

from app.core.config import LlmProvider, get_settings

CASE_COORD = "-34.04658242871865, 18.46491476666948"


def _say(client, project_id: str, message: str) -> dict:
    response = client.post(f"/api/v1/projects/{project_id}/chat", json={"message": message})
    assert response.status_code == 200, response.text
    return response.json()


def _ollama(client, base_url: str = "http://ollama.test"):
    """Point *this* app at a mockable Ollama, leaving the real settings alone.

    The override goes on `client.app`, not on the module-level singleton: the
    suite builds its app with `create_app()`, so those are different objects
    and overriding the wrong one silently does nothing.
    """
    settings = get_settings().model_copy(
        update={"llm_provider": LlmProvider.OLLAMA, "ollama_base_url": base_url}
    )
    client.app.dependency_overrides[get_settings] = lambda: settings
    return settings


def _clear_override(client):
    client.app.dependency_overrides.pop(get_settings, None)


# ---------------------------------------------------------------------------
# The happy path is not a fallback
# ---------------------------------------------------------------------------


def test_a_rules_answer_reports_rules_sufficient_and_no_attempt(client) -> None:
    project_id = client.post("/api/v1/projects").json()["projectId"]
    body = _say(client, project_id, CASE_COORD)

    interpretation = body["interpretation"]
    assert interpretation["attemptedProvider"] is None
    assert interpretation["effectiveProvider"] == "rules"
    assert interpretation["fallbackReason"] == "rules_sufficient"
    assert body["parserSource"] == "rules"


def test_every_field_of_the_interpretation_object_is_present(client) -> None:
    """The client renders from these keys; a missing one is a silent blank."""
    project_id = client.post("/api/v1/projects").json()["projectId"]
    interpretation = _say(client, project_id, CASE_COORD)["interpretation"]

    assert set(interpretation) == {
        "configuredProvider",
        "attemptedProvider",
        "effectiveProvider",
        "fallbackReason",
        "modelName",
        "latencyMs",
    }


def test_a_clean_rules_answer_on_an_ollama_stack_is_still_not_a_fallback(client) -> None:
    """Correction 5, end to end.

    The configured provider being Ollama does not make a deterministic answer a
    degraded one. Keying the customer-facing chip off `configured != effective`
    would have chipped nearly every message, because rules answer most of them.
    """
    _ollama(client)
    try:
        project_id = client.post("/api/v1/projects").json()["projectId"]
        interpretation = _say(client, project_id, CASE_COORD)["interpretation"]
    finally:
        _clear_override(client)

    assert interpretation["configuredProvider"] == "ollama"
    assert interpretation["attemptedProvider"] is None, "no HTTP call was made"
    assert interpretation["fallbackReason"] == "rules_sufficient"


# ---------------------------------------------------------------------------
# A real failure is reported as one, and named
# ---------------------------------------------------------------------------


def test_an_unreachable_model_is_reported_as_attempted_and_failed(client) -> None:
    _ollama(client, "http://ollama.invalid")
    try:
        project_id = client.post("/api/v1/projects").json()["projectId"]
        for message in (CASE_COORD, "1,150 kWh"):
            _say(client, project_id, message)
        with respx.mock:
            respx.post("http://ollama.invalid/api/generate").mock(
                side_effect=httpx.ConnectError("refused")
            )
            body = _say(client, project_id, "whichever one my neighbour got")
    finally:
        _clear_override(client)

    interpretation = body["interpretation"]
    assert interpretation["attemptedProvider"] == "ollama"
    assert interpretation["effectiveProvider"] == "rules"
    assert interpretation["fallbackReason"] == "unreachable"


def test_invalid_model_output_is_schema_rejected_not_unreachable(client) -> None:
    """A dead port and a lying model are different failures.

    Reporting both as "unreachable" would send an operator to the network when
    the problem is the prompt or the model.
    """
    _ollama(client)
    try:
        project_id = client.post("/api/v1/projects").json()["projectId"]
        for message in (CASE_COORD, "1,150 kWh"):
            _say(client, project_id, message)
        with respx.mock:
            respx.post("http://ollama.test/api/generate").mock(
                return_value=httpx.Response(200, json={"response": '{"kind": "teleport"}'})
            )
            body = _say(client, project_id, "whichever one my neighbour got")
    finally:
        _clear_override(client)

    assert body["interpretation"]["fallbackReason"] == "schema_rejected"
    assert body["interpretation"]["attemptedProvider"] == "ollama"


def test_a_model_answer_is_labelled_as_one(client) -> None:
    _ollama(client)
    try:
        project_id = client.post("/api/v1/projects").json()["projectId"]
        for message in (CASE_COORD, "1,150 kWh"):
            _say(client, project_id, message)
        with respx.mock:
            respx.post("http://ollama.test/api/generate").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "response": (
                            '{"kind": "provide_value", "topic": "system_size", '
                            '"values": {"system_size_kwp": 6.0}}'
                        )
                    },
                )
            )
            body = _say(client, project_id, "whichever one my neighbour got")
    finally:
        _clear_override(client)

    assert body["interpretation"]["effectiveProvider"] == "ollama"
    assert body["interpretation"]["fallbackReason"] is None
    assert body["parserSource"] == "llm", "the flat field is derived from the object"
    assert body["accepted"] is True


def test_a_model_supplied_value_still_faces_the_state_machine(client) -> None:
    """The model may interpret. It may not set a value the rules would refuse."""
    _ollama(client)
    try:
        project_id = client.post("/api/v1/projects").json()["projectId"]
        for message in (CASE_COORD, "1,150 kWh"):
            _say(client, project_id, message)
        with respx.mock:
            respx.post("http://ollama.test/api/generate").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "response": (
                            '{"kind": "provide_value", "topic": "system_size", '
                            '"values": {"system_size_kwp": 7.5}}'
                        )
                    },
                )
            )
            body = _say(client, project_id, "whichever one my neighbour got")
    finally:
        _clear_override(client)

    # 7.5 kWp is not one of the three, so the action never becomes a value.
    project = client.get(f"/api/v1/projects/{project_id}").json()
    assert project["selectedSystemSizeKwp"] is None
    assert body["currentStep"] == "system_size"
