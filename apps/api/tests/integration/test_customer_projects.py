"""Linking a project to a customer.

Three things are being pinned here.

**Nothing that worked before stops working.** `POST /projects` with no body at
all is how the chat entry point, both Playwright launchers and the
sample-output script all create projects, and a project with no customer stays
fully analysable and finalisable.

**A revision keeps the customer.** A forked revision that lost it would be a
proposal addressed to nobody, and the failure would only surface at the point
of sending - after re-analysis and re-finalisation.

**Changing the customer after finalisation forks.** Who a proposal is addressed
to is part of the document. The issued one has to keep saying what it said.
"""

from __future__ import annotations

import uuid

CASE_COORD = "-34.04658242871865, 18.46491476666948"


def a_customer(client, **overrides) -> dict:
    body = {
        "firstName": "Anna",
        "lastName": "Schmidt",
        "email": f"anna.{uuid.uuid4().hex[:12]}@example.com",
        **overrides,
    }
    response = client.post("/api/v1/customers", json=body)
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


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------


def test_a_project_can_still_be_created_with_no_body(client) -> None:
    """The way every existing client does it."""
    response = client.post("/api/v1/projects")
    assert response.status_code == 201, response.text
    assert response.json()["customer"] is None


def test_a_project_with_no_customer_is_readable_and_says_so(client) -> None:
    project_id = client.post("/api/v1/projects").json()["projectId"]
    body = client.get(f"/api/v1/projects/{project_id}").json()
    assert body["customer"] is None
    assert body["name"] is None


def test_a_project_with_no_customer_can_still_be_finalised(client) -> None:
    """The requirement lands on sending, not on finalising.

    A walk-in wanting a quick estimate never needs a record, and demanding one
    up front would break the chat-first flow the whole product is built around.
    """
    finalised = _finalised(client)
    assert finalised["shareToken"]


# ---------------------------------------------------------------------------
# Creating a linked project
# ---------------------------------------------------------------------------


def test_a_project_can_be_created_for_a_customer(client) -> None:
    customer = a_customer(client)
    response = client.post(
        "/api/v1/projects", json={"customerId": customer["customerId"], "name": "Roof, phase 1"}
    )
    assert response.status_code == 201, response.text
    assert response.json()["customer"]["customerId"] == customer["customerId"]

    project_id = response.json()["projectId"]
    body = client.get(f"/api/v1/projects/{project_id}").json()
    assert body["customer"]["displayName"] == "Anna Schmidt"
    assert body["name"] == "Roof, phase 1"


def test_creating_a_project_for_an_unknown_customer_is_refused(client) -> None:
    response = client.post("/api/v1/projects", json={"customerId": str(uuid.uuid4())})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CUSTOMER_NOT_FOUND"


# ---------------------------------------------------------------------------
# A customer's projects are findable
# ---------------------------------------------------------------------------


def test_a_customers_projects_are_listed_on_their_record(client) -> None:
    """Without this a project is unreachable once you leave the workspace.

    There is no project list anywhere else in the application and nobody keeps
    a project id, so a customer page that could only *create* work and never
    show any would strand every project it made.
    """
    customer = a_customer(client)
    finalised = _finalised(client, customer_id=customer["customerId"])

    body = client.get(f"/api/v1/customers/{customer['customerId']}").json()
    assert [p["projectId"] for p in body["projects"]] == [finalised["projectId"]]

    project = body["projects"][0]
    assert project["hasProposal"] is True
    assert project["shareToken"] == finalised["shareToken"]
    assert project["revisionNumber"] == 1
    assert project["systemSizeKwp"] == 6.0


def test_an_unfinalised_project_is_listed_too(client) -> None:
    customer = a_customer(client)
    project_id = client.post(
        "/api/v1/projects", json={"customerId": customer["customerId"]}
    ).json()["projectId"]

    project = client.get(f"/api/v1/customers/{customer['customerId']}").json()["projects"][0]
    assert project["projectId"] == project_id
    assert project["hasProposal"] is False
    assert project["shareToken"] is None


def test_every_revision_appears_because_each_one_was_sent(client) -> None:
    """A revision is a separate document with its own link.

    Listing only the newest would make "what have we sent this person" answer
    wrongly on the one screen that exists to answer it.
    """
    customer = a_customer(client)
    finalised = _finalised(client, customer_id=customer["customerId"])
    revision_id = _say(client, finalised["projectId"], "actually make it the largest option")[
        "projectId"
    ]

    projects = client.get(f"/api/v1/customers/{customer['customerId']}").json()["projects"]
    ids = {p["projectId"] for p in projects}
    assert ids == {finalised["projectId"], revision_id}

    revision = next(p for p in projects if p["projectId"] == revision_id)
    assert revision["isRevision"] is True
    assert revision["revisionOfProjectId"] == finalised["projectId"]


def test_the_customer_record_carries_their_recent_activity(client) -> None:
    customer = a_customer(client)
    _finalised(client, customer_id=customer["customerId"])

    events = client.get(f"/api/v1/customers/{customer['customerId']}").json()["activity"]
    types = [e["eventType"] for e in events]

    assert "customer.created" in types, "events about the person, not just their projects"
    assert "proposal.finalised" in types


def test_the_list_reports_how_many_projects_each_customer_has(client) -> None:
    token = uuid.uuid4().hex[:10]
    quiet = a_customer(client, lastName=f"Count{token}")
    busy = a_customer(client, lastName=f"Count{token}")

    for _ in range(2):
        client.post("/api/v1/projects", json={"customerId": busy["customerId"]})

    rows = client.get("/api/v1/customers", params={"q": f"count{token}"}).json()["customers"]
    counts = {row["customerId"]: row["projectCount"] for row in rows}

    assert counts[busy["customerId"]] == 2
    assert counts[quiet["customerId"]] == 0


def test_another_customers_projects_never_appear(client) -> None:
    mine, theirs = a_customer(client), a_customer(client)
    client.post("/api/v1/projects", json={"customerId": theirs["customerId"]})

    assert client.get(f"/api/v1/customers/{mine['customerId']}").json()["projects"] == []


# ---------------------------------------------------------------------------
# The all-projects list, which is the only way to reach an unlinked project
# ---------------------------------------------------------------------------


def test_a_project_with_no_customer_is_findable_here_and_nowhere_else(client) -> None:
    """The screen that exists for legacy rows and walk-in estimates.

    The customer pages are organised by person, so a project belonging to
    nobody cannot appear on them - and nobody keeps a project id. Without this
    list such a project is unreachable from the entire UI.
    """
    project_id = client.post("/api/v1/projects").json()["projectId"]

    rows = client.get("/api/v1/projects", params={"limit": 200}).json()["projects"]
    mine = next((p for p in rows if p["projectId"] == project_id), None)

    assert mine is not None, "a customerless project is invisible everywhere"
    assert mine["customer"] is None
    assert mine["hasProposal"] is False


def test_the_list_carries_the_customer_and_the_proposal(client) -> None:
    customer = a_customer(client)
    finalised = _finalised(client, customer_id=customer["customerId"])

    rows = client.get("/api/v1/projects", params={"limit": 200}).json()["projects"]
    mine = next(p for p in rows if p["projectId"] == finalised["projectId"])

    assert mine["customer"]["displayName"] == "Anna Schmidt"
    assert mine["hasProposal"] is True
    assert mine["shareToken"] == finalised["shareToken"]
    assert mine["revisionNumber"] == 1


def test_projects_can_be_searched_by_customer_or_by_name(client) -> None:
    token = uuid.uuid4().hex[:10]
    customer = a_customer(client, lastName=f"Finder{token}")
    client.post(
        "/api/v1/projects",
        json={"customerId": customer["customerId"], "name": f"Roof {token}"},
    )

    by_name = client.get("/api/v1/projects", params={"q": f"roof {token}"}).json()["projects"]
    by_customer = client.get("/api/v1/projects", params={"q": f"finder{token}"}).json()["projects"]

    assert len(by_name) == 1
    assert by_name == by_customer, "one search box has to serve both ways of remembering a job"


def test_the_project_list_never_exposes_a_customer_email(client) -> None:
    """It is a navigation list, not a contact record."""
    customer = a_customer(client)
    client.post("/api/v1/projects", json={"customerId": customer["customerId"]})

    raw = client.get("/api/v1/projects", params={"limit": 200}).text
    assert customer["email"] not in raw


def test_the_newest_project_comes_first(client) -> None:
    older = client.post("/api/v1/projects").json()["projectId"]
    newer = client.post("/api/v1/projects").json()["projectId"]

    ids = [
        p["projectId"]
        for p in client.get("/api/v1/projects", params={"pageSize": 200}).json()["projects"]
    ]
    assert ids.index(newer) < ids.index(older)


def test_the_project_list_pages_and_reports_its_totals(client) -> None:
    token = uuid.uuid4().hex[:10]
    customer = a_customer(client, lastName=f"Paged{token}")
    for index in range(5):
        client.post(
            "/api/v1/projects",
            json={"customerId": customer["customerId"], "name": f"Job {index} {token}"},
        )

    body = client.get("/api/v1/projects", params={"q": token, "page": 2, "pageSize": 2}).json()

    assert body["total"] == 5
    assert body["totalPages"] == 3
    assert body["page"] == 2
    assert len(body["projects"]) == 2


def test_a_project_created_for_a_customer_is_named_on_creation(client) -> None:
    """So it is identifiable in a list on the day it is made.

    Three "Draft project" rows for one customer tell an operator nothing.
    """
    customer = a_customer(client)
    created = client.post("/api/v1/projects", json={"customerId": customer["customerId"]})

    project = client.get(f"/api/v1/projects/{created.json()['projectId']}").json()
    assert project["name"], "an unnamed project is unidentifiable in a list"
    assert customer["displayName"] in project["name"]


def test_an_explicit_name_is_not_overwritten(client) -> None:
    customer = a_customer(client)
    created = client.post(
        "/api/v1/projects", json={"customerId": customer["customerId"], "name": "Phase 2"}
    )
    assert created.json()["customer"]["customerId"] == customer["customerId"]

    project = client.get(f"/api/v1/projects/{created.json()['projectId']}").json()
    assert project["name"] == "Phase 2"


def test_the_creation_event_names_the_project_and_the_customer(client) -> None:
    customer = a_customer(client)
    project_id = client.post(
        "/api/v1/projects", json={"customerId": customer["customerId"], "name": "Named job"}
    ).json()["projectId"]

    events = client.get(f"/api/v1/projects/{project_id}/activity").json()["events"]
    created = next(e for e in events if e["eventType"] == "project.created")

    assert created["metadata"]["projectName"] == "Named job"
    assert created["metadata"]["customerName"] == customer["displayName"]


# ---------------------------------------------------------------------------
# Assigning afterwards
# ---------------------------------------------------------------------------


def test_a_customer_can_be_assigned_to_an_existing_project(client) -> None:
    customer = a_customer(client)
    project_id = client.post("/api/v1/projects").json()["projectId"]

    response = client.post(
        f"/api/v1/projects/{project_id}/customer", json={"customerId": customer["customerId"]}
    )
    assert response.status_code == 200, response.text
    assert response.json()["customer"]["customerId"] == customer["customerId"]
    assert response.json()["projectId"] == project_id, "an unfinalised project must not fork"


def test_the_customer_can_be_changed_freely_before_finalisation(client) -> None:
    first, second = a_customer(client), a_customer(client)
    project_id = client.post("/api/v1/projects", json={"customerId": first["customerId"]}).json()[
        "projectId"
    ]

    body = client.post(
        f"/api/v1/projects/{project_id}/customer", json={"customerId": second["customerId"]}
    ).json()
    assert body["projectId"] == project_id
    assert body["customer"]["customerId"] == second["customerId"]


def test_assigning_an_unknown_customer_is_refused(client) -> None:
    project_id = client.post("/api/v1/projects").json()["projectId"]
    response = client.post(
        f"/api/v1/projects/{project_id}/customer", json={"customerId": str(uuid.uuid4())}
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# After finalisation
# ---------------------------------------------------------------------------


def test_changing_the_customer_after_finalisation_forks_a_revision(client) -> None:
    original_customer, new_customer = a_customer(client), a_customer(client)
    finalised = _finalised(client, customer_id=original_customer["customerId"])

    response = client.post(
        f"/api/v1/projects/{finalised['projectId']}/customer",
        json={"customerId": new_customer["customerId"]},
    )
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["projectId"] != finalised["projectId"], "the change did not fork"
    assert body["revisionOfProjectId"] == finalised["projectId"]
    assert body["customer"]["customerId"] == new_customer["customerId"]
    assert body["hasProposal"] is False, "a revision must not inherit a proposal"


def test_the_original_project_keeps_its_customer_after_the_fork(client) -> None:
    original_customer, new_customer = a_customer(client), a_customer(client)
    finalised = _finalised(client, customer_id=original_customer["customerId"])

    client.post(
        f"/api/v1/projects/{finalised['projectId']}/customer",
        json={"customerId": new_customer["customerId"]},
    )

    unchanged = client.get(f"/api/v1/projects/{finalised['projectId']}").json()
    assert unchanged["customer"]["customerId"] == original_customer["customerId"]


def test_a_customer_change_fork_is_immediately_finalisable(client) -> None:
    """Moving a proposal to a different recipient changes no figure.

    So the revision inherits the parent's completed analysis rather than being
    marked `recalculating`. That status is normally cleared by the recomputation
    the change triggered - but a customer change triggers none, so nothing would
    ever clear it and the revision would be permanently unfinalisable, with
    `validate_ready` refusing it and no way forward.
    """
    original_customer, new_customer = a_customer(client), a_customer(client)
    finalised = _finalised(client, customer_id=original_customer["customerId"])

    forked = client.post(
        f"/api/v1/projects/{finalised['projectId']}/customer",
        json={"customerId": new_customer["customerId"]},
    ).json()
    assert forked["analysisStatus"] == "complete", (
        f"the revision is stuck at {forked['analysisStatus']!r} with nothing to recompute"
    )

    reissued = client.post(f"/api/v1/projects/{forked['projectId']}/finalize")
    assert reissued.status_code == 200, reissued.text
    assert reissued.json()["shareToken"] != finalised["shareToken"]


def test_reassigning_the_same_customer_does_not_fork(client) -> None:
    """A no-op must not manufacture a revision.

    Otherwise an idempotent client - or a double-click - forks a draft that
    nobody asked for and that shows up in the revision history as though the
    proposal had been reissued.
    """
    customer = a_customer(client)
    finalised = _finalised(client, customer_id=customer["customerId"])

    body = client.post(
        f"/api/v1/projects/{finalised['projectId']}/customer",
        json={"customerId": customer["customerId"]},
    ).json()
    assert body["projectId"] == finalised["projectId"]


# ---------------------------------------------------------------------------
# Revisions carry the customer
# ---------------------------------------------------------------------------


def test_a_revision_forked_by_a_chat_edit_keeps_the_customer(client) -> None:
    customer = a_customer(client)
    finalised = _finalised(client, customer_id=customer["customerId"])

    revision_id = _say(client, finalised["projectId"], "actually make it the largest option")[
        "projectId"
    ]
    assert revision_id != finalised["projectId"], "the change must have forked a revision"

    revision = client.get(f"/api/v1/projects/{revision_id}").json()
    assert revision["customer"] is not None, "the revision lost its customer"
    assert revision["customer"]["customerId"] == customer["customerId"]


def test_a_revision_keeps_the_project_name(client) -> None:
    customer = a_customer(client)
    project_id = client.post(
        "/api/v1/projects", json={"customerId": customer["customerId"], "name": "Phase 1"}
    ).json()["projectId"]
    for message in (CASE_COORD, "1,150 kWh", "6 kWp"):
        _say(client, project_id, message)
    client.post(f"/api/v1/projects/{project_id}/run-analysis")
    assert client.post(f"/api/v1/projects/{project_id}/finalize").status_code == 200

    revision_id = _say(client, project_id, "actually make it the largest option")["projectId"]
    assert client.get(f"/api/v1/projects/{revision_id}").json()["name"] == "Phase 1"


# ---------------------------------------------------------------------------
# Renaming and deleting
# ---------------------------------------------------------------------------


def test_a_project_can_be_renamed_without_forking(client) -> None:
    """A label moves no figure, so it is safe even on a finalised project."""
    customer = a_customer(client)
    finalised = _finalised(client, customer_id=customer["customerId"])

    renamed = client.patch(
        f"/api/v1/projects/{finalised['projectId']}", json={"name": "Phase 2 roof"}
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Phase 2 roof"
    assert renamed.json()["projectId"] == finalised["projectId"], "a rename must not fork"

    # And the issued document is untouched.
    served = client.get(f"/api/v1/proposals/{finalised['shareToken']}")
    assert served.status_code == 200


def test_a_blank_name_clears_it(client) -> None:
    project_id = client.post("/api/v1/projects", json={"name": "temp"}).json()["projectId"]
    cleared = client.patch(f"/api/v1/projects/{project_id}", json={"name": "  "})
    assert cleared.json()["name"] is None


def test_a_draft_project_can_be_deleted(client) -> None:
    project_id = client.post("/api/v1/projects").json()["projectId"]

    assert client.delete(f"/api/v1/projects/{project_id}").status_code == 200
    assert client.get(f"/api/v1/projects/{project_id}").status_code == 404


def test_deleting_a_project_with_an_issued_proposal_is_refused(client) -> None:
    """The share link resolves through this row, and `proposals` cascades off it.

    Deleting it would stop a link a customer is holding from resolving - and
    the deletion would report success while doing it.
    """
    finalised = _finalised(client)

    response = client.delete(f"/api/v1/projects/{finalised['projectId']}")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DELETION_REFUSED"

    assert client.get(f"/api/v1/proposals/{finalised['shareToken']}").status_code == 200


def test_deleting_a_project_that_has_been_revised_is_refused(client) -> None:
    """It would strip the revision of its origin and break the chain."""
    original = _finalised(client)
    revision_id = _say(client, original["projectId"], "actually make it the largest option")[
        "projectId"
    ]

    response = client.delete(f"/api/v1/projects/{revision_id}")
    # The revision itself has no proposal yet, so what refuses is its parent's
    # link - deleting the *parent* is what is blocked here.
    assert response.status_code in {200, 409}

    parent = client.delete(f"/api/v1/projects/{original['projectId']}")
    assert parent.status_code == 409
    assert parent.json()["error"]["code"] == "DELETION_REFUSED"


def test_a_customer_with_no_projects_can_be_deleted(client) -> None:
    customer = a_customer(client)

    assert client.delete(f"/api/v1/customers/{customer['customerId']}").status_code == 200
    assert client.get(f"/api/v1/customers/{customer['customerId']}").status_code == 404


def test_deleting_a_customer_takes_their_projects_and_proposals_with_them(client) -> None:
    """A deliberate cascade, and the most destructive thing this API does.

    The FK from `projects` is SET NULL, so without deleting them explicitly the
    projects would survive belonging to nobody - visible on the all-projects
    list, attached to a customer who no longer exists. Removing them is the
    honest reading of "delete this customer".

    The cost is real and is reported rather than hidden: an issued proposal's
    share link stops resolving, and somebody may be holding it. The response
    says how much was destroyed so the confirmation can name it.
    """
    customer = a_customer(client)
    finalised = _finalised(client, customer_id=customer["customerId"])

    response = client.delete(f"/api/v1/customers/{customer['customerId']}")
    assert response.status_code == 200, response.text
    assert response.json() == {"deleted": True, "deletedProjects": 1, "deletedProposals": 1}

    assert client.get(f"/api/v1/customers/{customer['customerId']}").status_code == 404
    assert client.get(f"/api/v1/projects/{finalised['projectId']}").status_code == 404
    assert client.get(f"/api/v1/proposals/{finalised['shareToken']}").status_code == 404


def test_deleting_a_customer_leaves_no_orphaned_project_behind(client) -> None:
    """SET NULL would keep the project, owned by nobody, on the all-projects list."""
    customer = a_customer(client)
    project_id = client.post(
        "/api/v1/projects", json={"customerId": customer["customerId"]}
    ).json()["projectId"]

    assert client.delete(f"/api/v1/customers/{customer['customerId']}").status_code == 200

    listed = client.get("/api/v1/projects", params={"limit": 200}).json()["projects"]
    assert all(p["projectId"] != project_id for p in listed)


def test_deleting_one_customer_leaves_another_alone(client) -> None:
    mine, theirs = a_customer(client), a_customer(client)
    kept = _finalised(client, customer_id=theirs["customerId"])
    client.post("/api/v1/projects", json={"customerId": mine["customerId"]})

    assert client.delete(f"/api/v1/customers/{mine['customerId']}").status_code == 200

    assert client.get(f"/api/v1/customers/{theirs['customerId']}").status_code == 200
    assert client.get(f"/api/v1/proposals/{kept['shareToken']}").status_code == 200


def test_archiving_remains_the_non_destructive_alternative(client) -> None:
    """The reason the confirmation offers it: nothing is lost."""
    customer = a_customer(client)
    finalised = _finalised(client, customer_id=customer["customerId"])

    assert client.post(f"/api/v1/customers/{customer['customerId']}/archive").status_code == 200

    assert client.get(f"/api/v1/customers/{customer['customerId']}").status_code == 200
    assert client.get(f"/api/v1/proposals/{finalised['shareToken']}").status_code == 200


def test_archiving_is_reversible(client) -> None:
    customer = a_customer(client)

    archived = client.post(f"/api/v1/customers/{customer['customerId']}/archive").json()
    assert archived["customer"]["archivedAt"]

    restored = client.post(f"/api/v1/customers/{customer['customerId']}/unarchive").json()
    assert restored["customer"]["archivedAt"] is None


# ---------------------------------------------------------------------------
# The customer going away
# ---------------------------------------------------------------------------


def test_archiving_a_customer_leaves_their_projects_intact(client) -> None:
    customer = a_customer(client)
    finalised = _finalised(client, customer_id=customer["customerId"])

    assert client.post(f"/api/v1/customers/{customer['customerId']}/archive").status_code == 200

    project = client.get(f"/api/v1/projects/{finalised['projectId']}").json()
    assert project["customer"]["customerId"] == customer["customerId"]
    assert project["customer"]["archivedAt"]

    # And the issued document is untouched by any of it.
    assert client.get(f"/api/v1/proposals/{finalised['shareToken']}").status_code == 200


# ---------------------------------------------------------------------------
# Serving one customer's projects from the paged project route
# ---------------------------------------------------------------------------


def test_the_project_list_can_be_narrowed_to_one_customer(client) -> None:
    """What lets a customer's own screen use the same table `/projects` uses.

    It used to render an unpaginated list from the array on
    `GET /customers/{id}` — a second rendering of the same thing, which had
    already drifted: it carried no pager and no way to delete a project.
    """
    anna = a_customer(client)
    ben = a_customer(client, firstName="Ben")

    mine = _finalised(client, customer_id=anna["customerId"])
    theirs = _finalised(client, customer_id=ben["customerId"])

    body = client.get(f"/api/v1/projects?customerId={anna['customerId']}").json()
    ids = [row["projectId"] for row in body["projects"]]

    assert mine["projectId"] in ids
    assert theirs["projectId"] not in ids
    assert body["total"] == len(ids)
    assert all(row["customer"]["customerId"] == anna["customerId"] for row in body["projects"])


def test_the_narrowed_list_pages_like_the_unfiltered_one(client) -> None:
    customer = a_customer(client)
    for _ in range(3):
        client.post("/api/v1/projects", json={"customerId": customer["customerId"]})

    first = client.get(
        f"/api/v1/projects?customerId={customer['customerId']}&page=1&pageSize=2"
    ).json()
    second = client.get(
        f"/api/v1/projects?customerId={customer['customerId']}&page=2&pageSize=2"
    ).json()

    assert first["total"] == 3
    assert first["totalPages"] == 2
    assert len(first["projects"]) == 2
    assert len(second["projects"]) == 1
    # Disjoint pages: an unstable sort would repeat a row and hide another.
    assert not {p["projectId"] for p in first["projects"]} & {
        p["projectId"] for p in second["projects"]
    }


def test_an_unknown_customer_narrows_to_nothing_rather_than_everything(client) -> None:
    """A filter that silently falls back to "all" is worse than an error: the
    screen would show one customer another customer's projects."""
    _finalised(client, customer_id=a_customer(client)["customerId"])

    body = client.get(f"/api/v1/projects?customerId={uuid.uuid4()}").json()

    assert body["projects"] == []
    assert body["total"] == 0


def test_the_customer_filter_composes_with_the_search_term(client) -> None:
    """Both narrowings apply, not whichever one the query builder reached last.

    The second customer also has a "Garage", so a search that ignored the
    customer would return two rows and a customer filter that ignored the
    search would return two rows. Only applying both gives one.
    """
    mine = a_customer(client)
    theirs = a_customer(client, firstName="Ben")

    client.post("/api/v1/projects", json={"customerId": mine["customerId"], "name": "Garage"})
    client.post("/api/v1/projects", json={"customerId": mine["customerId"], "name": "House"})
    client.post("/api/v1/projects", json={"customerId": theirs["customerId"], "name": "Garage"})

    body = client.get(f"/api/v1/projects?customerId={mine['customerId']}&q=garage").json()

    assert body["total"] == 1
    assert [row["name"] for row in body["projects"]] == ["Garage"]
    assert body["projects"][0]["customer"]["customerId"] == mine["customerId"]
