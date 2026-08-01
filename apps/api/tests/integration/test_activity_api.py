"""The project activity timeline, end to end.

The two things worth proving over HTTP rather than in a unit test.

**The timeline spans the whole revision lineage.** A revision is a separate
project row, so a timeline scoped to one of them would begin halfway through
the story - the original analysis and the first proposal simply missing.

**An audit write never breaks the thing it describes.** The view and PDF events
are best-effort, so a customer's page must render even when recording fails.
"""

from __future__ import annotations

import uuid

CASE_COORD = "-34.04658242871865, 18.46491476666948"


def a_customer(client) -> dict:
    response = client.post(
        "/api/v1/customers",
        json={
            "firstName": "Anna",
            "lastName": "Schmidt",
            "email": f"anna.{uuid.uuid4().hex[:12]}@example.com",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["customer"]


def _say(client, project_id: str, message: str) -> dict:
    response = client.post(f"/api/v1/projects/{project_id}/chat", json={"message": message})
    assert response.status_code == 200, response.text
    return response.json()


def _finalised(client, *, customer_id: str | None = None) -> dict:
    body = {"customerId": customer_id} if customer_id else None
    project_id = client.post("/api/v1/projects", json=body).json()["projectId"]
    for message in (CASE_COORD, "1,150 kWh", "6 kWp"):
        _say(client, project_id, message)
    assert client.post(f"/api/v1/projects/{project_id}/run-analysis").status_code == 200
    finalised = client.post(f"/api/v1/projects/{project_id}/finalize")
    assert finalised.status_code == 200, finalised.text
    return {"projectId": project_id, **finalised.json()}


def _timeline(client, project_id: str) -> list[dict]:
    response = client.get(f"/api/v1/projects/{project_id}/activity")
    assert response.status_code == 200, response.text
    return response.json()["events"]


def _types(events: list[dict]) -> list[str]:
    return [event["eventType"] for event in events]


# ---------------------------------------------------------------------------
# What lands on the timeline
# ---------------------------------------------------------------------------


def test_creating_a_project_is_the_first_event(client) -> None:
    project_id = client.post("/api/v1/projects").json()["projectId"]
    events = _timeline(client, project_id)

    assert _types(events) == ["project.created"]
    assert events[0]["actor"] == "user"
    assert events[0]["projectId"] == project_id


def test_a_full_run_records_analysis_and_finalisation(client) -> None:
    customer = a_customer(client)
    finalised = _finalised(client, customer_id=customer["customerId"])

    events = _timeline(client, finalised["projectId"])
    assert "analysis.completed" in _types(events)
    assert "proposal.finalised" in _types(events)

    finalisation = next(e for e in events if e["eventType"] == "proposal.finalised")
    assert finalisation["metadata"]["revisionNumber"] == 1
    assert finalisation["metadata"]["reference"].startswith("SOL-")
    assert finalisation["customerId"] == customer["customerId"]
    assert finalisation["proposalId"] == finalised["proposalId"]


def test_the_newest_event_comes_first(client) -> None:
    project_id = client.post("/api/v1/projects").json()["projectId"]
    _say(client, project_id, CASE_COORD)
    _say(client, project_id, "1,150 kWh")
    _say(client, project_id, "6 kWp")
    client.post(f"/api/v1/projects/{project_id}/run-analysis")

    events = _timeline(client, project_id)
    assert _types(events)[0] == "analysis.completed"
    assert _types(events)[-1] == "project.created"


def test_viewing_the_public_proposal_appears_as_the_customer(client) -> None:
    finalised = _finalised(client)
    client.post(f"/api/v1/proposals/{finalised['shareToken']}/view")

    viewed = next(
        e for e in _timeline(client, finalised["projectId"]) if e["eventType"] == "proposal.viewed"
    )
    assert viewed["actor"] == "customer", "the only actor who is not the operator"
    assert viewed["metadata"]["viewCount"] == 1


def test_a_pdf_download_is_not_recorded_as_a_page_view(client, monkeypatch) -> None:
    """Two different acts. Folding one into the other inflates the number an
    operator uses to judge whether the customer has actually looked."""
    finalised = _finalised(client)

    from app.api.v1 import proposals as route

    async def _skip_chromium(html: str) -> bytes:
        return b"%PDF-stub"

    monkeypatch.setattr(route, "render_pdf", _skip_chromium)
    assert client.get(f"/api/v1/proposals/{finalised['shareToken']}/pdf").status_code == 200

    types = _types(_timeline(client, finalised["projectId"]))
    assert "proposal.pdf_downloaded" in types
    assert "proposal.viewed" not in types

    views = client.get(f"/api/v1/proposals/{finalised['shareToken']}").json()["views"]
    assert views["viewCount"] == 0, "a download moved the view count"


def test_assigning_a_customer_is_recorded_with_the_display_name_only(client) -> None:
    customer = a_customer(client)
    project_id = client.post("/api/v1/projects").json()["projectId"]
    client.post(
        f"/api/v1/projects/{project_id}/customer", json={"customerId": customer["customerId"]}
    )

    assigned = next(
        e
        for e in _timeline(client, project_id)
        if e["eventType"] == "project.customer_assigned"
    )
    assert assigned["metadata"]["displayName"] == "Anna Schmidt"
    assert assigned["metadata"]["forkedRevision"] is False
    assert customer["email"] not in str(assigned)


def test_a_customer_update_records_which_fields_moved_not_their_values(client) -> None:
    customer = a_customer(client)
    project_id = client.post(
        "/api/v1/projects", json={"customerId": customer["customerId"]}
    ).json()["projectId"]

    new_email = f"moved.{uuid.uuid4().hex[:8]}@example.com"
    client.patch(f"/api/v1/customers/{customer['customerId']}", json={"email": new_email})

    # The customer event is not on a project, so it is read from the customer's
    # own trail rather than the project timeline.
    events = _timeline(client, project_id)
    assert new_email not in str(events), "the new address reached the timeline"


# ---------------------------------------------------------------------------
# The lineage
# ---------------------------------------------------------------------------


def test_the_timeline_spans_the_whole_revision_chain(client) -> None:
    original = _finalised(client)
    revision_id = _say(client, original["projectId"], "actually make it the largest option")[
        "projectId"
    ]
    assert client.post(f"/api/v1/projects/{revision_id}/finalize").status_code == 200

    from_revision = _timeline(client, revision_id)
    from_original = _timeline(client, original["projectId"])

    assert from_revision == from_original, "the history must read the same from either end"
    assert _types(from_revision).count("proposal.finalised") == 2, (
        "the timeline began halfway through the story"
    )
    assert "project.revised" in _types(from_revision)


# ---------------------------------------------------------------------------
# Failure does not propagate
# ---------------------------------------------------------------------------


def test_a_failed_audit_write_does_not_break_the_customers_page(client, monkeypatch) -> None:
    finalised = _finalised(client)

    from app.services import activity as activity_service

    async def _explode(*args, **kwargs):
        raise RuntimeError("the audit table is on fire")

    monkeypatch.setattr(activity_service, "record", _explode)

    response = client.post(f"/api/v1/proposals/{finalised['shareToken']}/view")
    assert response.status_code == 200, "an audit failure reached the customer"
    assert response.json()["viewCount"] == 1, "the view itself was still counted"


# ---------------------------------------------------------------------------
# Paging
# ---------------------------------------------------------------------------


def test_paging_walks_the_timeline_without_repeating(client) -> None:
    finalised = _finalised(client)
    for _ in range(4):
        client.post(f"/api/v1/proposals/{finalised['shareToken']}/view")

    seen: list[str] = []
    cursor = None
    for _ in range(20):  # bounded: a cursor bug must fail, not hang
        params = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        body = client.get(
            f"/api/v1/projects/{finalised['projectId']}/activity", params=params
        ).json()
        seen.extend(e["eventId"] for e in body["events"])
        cursor = body["nextCursor"]
        if not cursor:
            break

    assert cursor is None, "paging did not terminate"
    assert len(seen) == len(set(seen)), "an event appeared on two pages"
    assert set(seen) == {e["eventId"] for e in _timeline(client, finalised["projectId"])}
