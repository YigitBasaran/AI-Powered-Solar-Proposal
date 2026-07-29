"""Correction 4, over HTTP: editing a finalised project forks a revision.

A finalised proposal is immutable, and `finalise_proposal` returns the existing
one for a project that already has it. The failure this guards against is the
project's values drifting while every later finalize hands back the old
document - values and proposal silently disagreeing, with no error anywhere.

The answer is a revision: a new editable project, no proposal of its own, the
old link untouched, and a *different* token when the revision is finalised.
"""

from __future__ import annotations

CASE_COORD = "-34.04658242871865, 18.46491476666948"


def _finalised(client, size_reply: str = "6 kWp") -> dict:
    project_id = client.post("/api/v1/projects").json()["projectId"]
    for message in (CASE_COORD, "1,150 kWh", size_reply):
        client.post(f"/api/v1/projects/{project_id}/chat", json={"message": message})
    client.post(f"/api/v1/projects/{project_id}/run-analysis")
    finalised = client.post(f"/api/v1/projects/{project_id}/finalize")
    assert finalised.status_code == 200
    return {"projectId": project_id, **finalised.json()}


def _say(client, project_id: str, message: str) -> dict:
    response = client.post(f"/api/v1/projects/{project_id}/chat", json={"message": message})
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# The fork itself
# ---------------------------------------------------------------------------


def test_changing_a_finalised_project_forks_a_revision(client) -> None:
    original = _finalised(client)

    body = _say(client, original["projectId"], "actually make it the largest option")

    assert body["projectId"] != original["projectId"], "the conversation moved to a revision"
    assert body["revisionOfProjectId"] == original["projectId"]


def test_the_original_proposal_and_link_are_untouched(client) -> None:
    original = _finalised(client)
    before = client.get(f"/api/v1/proposals/{original['shareToken']}").json()

    body = _say(client, original["projectId"], "actually make it the largest option")
    # Guard: without this the assertions below pass trivially in a build that
    # simply ignores the change.
    assert body["projectId"] != original["projectId"], "the change must have forked a revision"

    after = client.get(f"/api/v1/proposals/{original['shareToken']}")
    assert after.status_code == 200
    body = after.json()
    assert body["layout"] == before["layout"]
    assert body["financial"] == before["financial"]
    assert body["exchangeRate"] == before["exchangeRate"]


def test_finalising_the_revision_issues_a_new_token(client) -> None:
    original = _finalised(client)
    revision_id = _say(client, original["projectId"], "change to the largest option")["projectId"]

    revised = client.post(f"/api/v1/projects/{revision_id}/finalize")
    assert revised.status_code == 200
    assert revised.json()["shareToken"] != original["shareToken"]

    # Both links resolve, and they describe different systems.
    old = client.get(f"/api/v1/proposals/{original['shareToken']}").json()
    new = client.get(f"/api/v1/proposals/{revised.json()['shareToken']}").json()
    assert old["layout"]["requestedSystemSizeKwp"] == 6.0
    assert new["layout"]["requestedSystemSizeKwp"] == 9.6


# ---------------------------------------------------------------------------
# Lifecycle - a revision never inherits a finalised state
# ---------------------------------------------------------------------------


def test_a_revision_starts_editable_with_no_proposal_of_its_own(client) -> None:
    original = _finalised(client)
    revision_id = _say(client, original["projectId"], "change to the largest option")["projectId"]

    project = client.get(f"/api/v1/projects/{revision_id}").json()
    assert project["currentStep"] != "completed", "a revision is editable, not finalised"
    assert project["revisionOfProjectId"] == original["projectId"]

    # No proposal until the revision is finalised in its own right.
    assert project["hasProposal"] is False


def test_a_revision_is_complete_after_selective_recomputation(client) -> None:
    original = _finalised(client)
    body = _say(client, original["projectId"], "change to the largest option")

    project = client.get(f"/api/v1/projects/{body['projectId']}").json()
    assert project["analysisStatus"] == "complete"
    assert project["selectedSystemSizeKwp"] == 9.6
    assert project["analysis"]["layout"]["requestedSystemSizeKwp"] == 9.6


def test_a_revision_cannot_be_finalised_while_it_is_stale(client, monkeypatch) -> None:
    """A failed recomputation must not present the parent's numbers as the child's."""
    original = _finalised(client)

    async def _boom(**_kwargs):
        raise RuntimeError("PVGIS unavailable")

    monkeypatch.setattr("app.services.analysis.recompute_for_system_size", _boom)
    revision_id = _say(client, original["projectId"], "change to the largest option")["projectId"]

    project = client.get(f"/api/v1/projects/{revision_id}").json()
    assert project["analysisStatus"] == "stale"

    refused = client.post(f"/api/v1/projects/{revision_id}/finalize")
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "PROPOSAL_INCOMPLETE"


# ---------------------------------------------------------------------------
# Idempotency - a retried or repeated change must not fork twice
# ---------------------------------------------------------------------------


def test_repeating_the_same_change_reuses_the_one_revision(client) -> None:
    original = _finalised(client)

    first = _say(client, original["projectId"], "change to the largest option")["projectId"]
    second = _say(client, original["projectId"], "change to the largest option")["projectId"]

    assert first != original["projectId"], "the change must have forked a revision at all"
    assert first == second, "a retried delivery must not create a second draft"


def test_a_different_change_still_reuses_the_active_revision(client) -> None:
    """One editable child per parent - revisions chain, they do not fan out."""
    original = _finalised(client)

    first = _say(client, original["projectId"], "change to the largest option")["projectId"]
    second = _say(client, original["projectId"], "change to the smallest option")["projectId"]

    assert first == second
    project = client.get(f"/api/v1/projects/{second}").json()
    assert project["selectedSystemSizeKwp"] == 3.6


# ---------------------------------------------------------------------------
# Correction 5, over HTTP - the interpretation object is truthful
# ---------------------------------------------------------------------------


def test_a_clean_rules_answer_reports_no_attempted_provider(client) -> None:
    project_id = client.post("/api/v1/projects").json()["projectId"]
    body = _say(client, project_id, CASE_COORD)

    interpretation = body["interpretation"]
    assert interpretation["effectiveProvider"] == "rules"
    assert interpretation["attemptedProvider"] is None
    assert interpretation["fallbackReason"] == "rules_sufficient"
    assert body["parserSource"] == "rules", "the flat field is derived, never contradictory"
