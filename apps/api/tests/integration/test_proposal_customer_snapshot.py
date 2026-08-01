"""The customer a proposal was issued to, and what the public link reveals.

Two guarantees, and they pull in opposite directions.

**Immutability.** A proposal is a document someone was sent. The customer is
frozen into it at finalisation, so a corrected surname or a new address six
months later cannot restate what was issued - the same rule the figures already
obey.

**Privacy.** The public route is reachable by anyone holding the link. The
snapshot it renders from contains an email address, and that address must never
reach it. The projection is an allow-list, so this is testable as an absence:
the address does not appear anywhere in the served bytes.
"""

from __future__ import annotations

import uuid

CASE_COORD = "-34.04658242871865, 18.46491476666948"


def a_customer(client, **overrides) -> dict:
    body = {
        "firstName": "Anna",
        "lastName": "Schmidt",
        "email": f"anna.{uuid.uuid4().hex[:12]}@example.com",
        "phone": "+27 21 555 0100",
        "address": "12 Galway Road, Cape Town",
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
# The snapshot is taken, and it is frozen
# ---------------------------------------------------------------------------


def test_finalising_freezes_the_customer(client) -> None:
    customer = a_customer(client)
    finalised = _finalised(client, customer_id=customer["customerId"])

    served = client.get(f"/api/v1/proposals/{finalised['shareToken']}").json()
    assert served["customer"] == {"displayName": "Anna Schmidt"}


def test_editing_the_customer_does_not_restate_an_issued_proposal(client) -> None:
    """The immutability boundary, applied to the recipient rather than the money."""
    customer = a_customer(client)
    finalised = _finalised(client, customer_id=customer["customerId"])
    before = client.get(f"/api/v1/proposals/{finalised['shareToken']}").json()

    updated = client.patch(
        f"/api/v1/customers/{customer['customerId']}",
        json={"lastName": "Meyer", "email": f"new.{uuid.uuid4().hex[:8]}@example.com"},
    )
    assert updated.status_code == 200, updated.text

    after = client.get(f"/api/v1/proposals/{finalised['shareToken']}").json()
    assert after["customer"] == before["customer"] == {"displayName": "Anna Schmidt"}


def test_archiving_the_customer_does_not_alter_the_proposal(client) -> None:
    customer = a_customer(client)
    finalised = _finalised(client, customer_id=customer["customerId"])

    client.post(f"/api/v1/customers/{customer['customerId']}/archive")

    served = client.get(f"/api/v1/proposals/{finalised['shareToken']}")
    assert served.status_code == 200
    assert served.json()["customer"] == {"displayName": "Anna Schmidt"}


def test_a_proposal_with_no_customer_is_finalised_and_served(client) -> None:
    """Legacy rows and walk-in estimates both land here."""
    finalised = _finalised(client)
    served = client.get(f"/api/v1/proposals/{finalised['shareToken']}").json()
    assert served["customer"] is None


# ---------------------------------------------------------------------------
# Privacy: what the public link does not carry
# ---------------------------------------------------------------------------


def test_the_public_payload_never_carries_the_email_address(client) -> None:
    """Asserted against the whole serialised body, not against named keys.

    A key-by-key assertion only proves the fields someone thought to check. The
    address is a single distinctive string, so its absence from the entire
    response is the stronger claim - and it survives someone later adding a
    field that happens to include it.
    """
    customer = a_customer(client)
    finalised = _finalised(client, customer_id=customer["customerId"])

    raw = client.get(f"/api/v1/proposals/{finalised['shareToken']}").text
    assert customer["email"] not in raw
    assert customer["email"].split("@")[0] not in raw


def test_the_public_payload_carries_no_phone_or_customer_id(client) -> None:
    customer = a_customer(client)
    finalised = _finalised(client, customer_id=customer["customerId"])

    raw = client.get(f"/api/v1/proposals/{finalised['shareToken']}").text
    assert customer["phone"] not in raw
    assert customer["customerId"] not in raw


def test_the_public_customer_is_an_allow_list_of_one_field(client) -> None:
    customer = a_customer(client)
    finalised = _finalised(client, customer_id=customer["customerId"])

    served = client.get(f"/api/v1/proposals/{finalised['shareToken']}").json()
    assert set(served["customer"]) == {"displayName"}, (
        "the projection names the fields it publishes, so a column added to the "
        "snapshot later cannot leak by default"
    )


def test_the_rendered_pdf_never_carries_the_email_address(client) -> None:
    from app.core.config import get_settings
    from app.services.pdf import build_context, render_html

    customer = a_customer(client)
    finalised = _finalised(client, customer_id=customer["customerId"])

    served = client.get(f"/api/v1/proposals/{finalised['shareToken']}").json()
    html = render_html(
        build_context(
            served,
            share_token=finalised["shareToken"],
            created_at=served["createdAt"],
            settings=get_settings(),
        )
    )
    assert customer["email"] not in html


def test_the_snapshot_itself_does_keep_the_address(client) -> None:
    """The internal record is complete; only the *projection* is narrow.

    Worth pinning, because a "fix" that simply stopped storing the address would
    make the privacy tests above pass and leave nothing to send the proposal to.
    """
    import asyncio

    from sqlalchemy import select

    from app.db.session import get_sessionmaker
    from app.models.tables import Proposal

    customer = a_customer(client)
    finalised = _finalised(client, customer_id=customer["customerId"])

    async def _load() -> dict:
        async with get_sessionmaker()() as session:
            row = (
                await session.execute(
                    select(Proposal).where(Proposal.share_token == finalised["shareToken"])
                )
            ).scalar_one()
            return dict(row.customer_snapshot_json or {})

    snapshot = asyncio.run(_load())
    assert snapshot["email"] == customer["email"]
    assert snapshot["customerId"] == customer["customerId"]
    assert snapshot["capturedAt"]


# ---------------------------------------------------------------------------
# Revision numbering
# ---------------------------------------------------------------------------


def test_the_first_proposal_is_revision_one(client) -> None:
    finalised = _finalised(client)
    served = client.get(f"/api/v1/proposals/{finalised['shareToken']}").json()
    assert served["revisionNumber"] == 1
    assert served["reference"].endswith("-R1")
    assert served["reference"].startswith("SOL-")


def test_a_revision_is_numbered_two_and_the_first_stays_one(client) -> None:
    original = _finalised(client)
    revision_id = _say(client, original["projectId"], "actually make it the largest option")[
        "projectId"
    ]
    revised = client.post(f"/api/v1/projects/{revision_id}/finalize")
    assert revised.status_code == 200, revised.text

    new = client.get(f"/api/v1/proposals/{revised.json()['shareToken']}").json()
    old = client.get(f"/api/v1/proposals/{original['shareToken']}").json()

    assert new["revisionNumber"] == 2
    assert old["revisionNumber"] == 1, "the issued document was renumbered"
    assert new["reference"] != old["reference"]


def test_a_revision_carries_the_customer_forward(client) -> None:
    customer = a_customer(client)
    original = _finalised(client, customer_id=customer["customerId"])

    revision_id = _say(client, original["projectId"], "actually make it the largest option")[
        "projectId"
    ]
    revised = client.post(f"/api/v1/projects/{revision_id}/finalize")
    assert revised.status_code == 200

    served = client.get(f"/api/v1/proposals/{revised.json()['shareToken']}").json()
    assert served["customer"] == {"displayName": "Anna Schmidt"}


def test_changing_the_customer_then_finalising_leaves_the_first_proposal_addressed(
    client,
) -> None:
    """The case the whole snapshot exists for.

    Reassigning after finalisation forks a revision. The issued proposal keeps
    naming the person it was sent to; only the new one names the new customer.
    """
    original_customer = a_customer(client)
    new_customer = a_customer(client, firstName="Bruno", lastName="Weiss")
    original = _finalised(client, customer_id=original_customer["customerId"])

    forked = client.post(
        f"/api/v1/projects/{original['projectId']}/customer",
        json={"customerId": new_customer["customerId"]},
    ).json()
    assert forked["projectId"] != original["projectId"]

    revised = client.post(f"/api/v1/projects/{forked['projectId']}/finalize")
    assert revised.status_code == 200, revised.text

    issued = client.get(f"/api/v1/proposals/{original['shareToken']}").json()
    reissued = client.get(f"/api/v1/proposals/{revised.json()['shareToken']}").json()

    assert issued["customer"] == {"displayName": "Anna Schmidt"}
    assert reissued["customer"] == {"displayName": "Bruno Weiss"}


# ---------------------------------------------------------------------------
# The revision list
# ---------------------------------------------------------------------------


def test_the_revision_list_shows_the_whole_chain_from_either_end(client) -> None:
    original = _finalised(client)
    revision_id = _say(client, original["projectId"], "actually make it the largest option")[
        "projectId"
    ]
    assert client.post(f"/api/v1/projects/{revision_id}/finalize").status_code == 200

    from_original = client.get(f"/api/v1/projects/{original['projectId']}/revisions").json()
    from_revision = client.get(f"/api/v1/projects/{revision_id}/revisions").json()

    assert from_original == from_revision, "the history must read the same from either end"

    rows = from_original["revisions"]
    assert [row["revisionNumber"] for row in rows] == [1, 2]
    assert [row["projectId"] for row in rows] == [original["projectId"], revision_id]
    assert rows[0]["shareToken"] == original["shareToken"]
    assert rows[0]["isSuperseded"] is True
    assert rows[1]["isSuperseded"] is False
    assert rows[1]["isCurrent"] is True


def test_an_unfinalised_revision_appears_with_no_proposal(client) -> None:
    original = _finalised(client)
    revision_id = _say(client, original["projectId"], "actually make it the largest option")[
        "projectId"
    ]

    rows = client.get(f"/api/v1/projects/{original['projectId']}/revisions").json()["revisions"]
    assert rows[1]["projectId"] == revision_id
    assert rows[1]["proposalId"] is None
    assert rows[1]["shareToken"] is None
    assert rows[0]["isSuperseded"] is False, (
        "a draft that was never issued cannot supersede an issued document"
    )
