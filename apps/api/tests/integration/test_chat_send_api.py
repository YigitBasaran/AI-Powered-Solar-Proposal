"""Sending a proposal by asking for it.

The happy path is the least interesting thing here. What matters is everything
that must *not* send:

* a question about sending ("can you email this?", "who would get it?"),
* a bare "yes" that was answering something else,
* a "yes" separated from the offer by any other turn,
* an offer made when there is no proposal, or nobody to send it to.

The mechanism is the one that already exists for reset: the reply carries a
`pendingConfirmation`, and `_pending_confirmation` reads only the *immediately
preceding* assistant message. So a stale confirmation is not something that has
to be expired - it simply stops being reachable the moment anything else is
said.
"""

from __future__ import annotations

import uuid

import pytest

CASE_COORD = "-34.04658242871865, 18.46491476666948"


@pytest.fixture
def outbox(monkeypatch):
    from app.core.config import get_settings
    from app.services import email as email_module
    from app.services import proposal_email
    from app.services.email import ConsoleEmailSender

    sender = ConsoleEmailSender(get_settings())
    monkeypatch.setattr(email_module, "build_sender", lambda settings: sender)
    monkeypatch.setattr(proposal_email, "build_sender", lambda settings: sender)
    return sender


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


def say(client, project_id: str, message: str) -> dict:
    response = client.post(f"/api/v1/projects/{project_id}/chat", json={"message": message})
    assert response.status_code == 200, response.text
    return response.json()


def finalised(client, *, with_customer: bool = True) -> dict:
    customer = a_customer(client) if with_customer else None
    body = {"customerId": customer["customerId"]} if customer else None
    project_id = client.post("/api/v1/projects", json=body).json()["projectId"]
    for message in (CASE_COORD, "1,150 kWh", "6 kWp"):
        say(client, project_id, message)
    assert client.post(f"/api/v1/projects/{project_id}/run-analysis").status_code == 200
    response = client.post(f"/api/v1/projects/{project_id}/finalize")
    assert response.status_code == 200, response.text
    return {"projectId": project_id, "customer": customer, **response.json()}


# ---------------------------------------------------------------------------
# Asking to send offers, and only offers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "send the proposal",
        "email it to the customer",
        "please send it to them",
        "share the link with her",
        "forward the proposal",
    ],
)
def test_asking_to_send_offers_rather_than_sends(client, outbox, message: str) -> None:
    project = finalised(client)
    reply = say(client, project["projectId"], message)

    assert "shall i send it" in reply["assistantMessage"].lower()
    assert outbox.outbox == [], f"{message!r} sent without confirmation"


def test_the_offer_names_the_recipient_masked(client, outbox) -> None:
    """The transcript is stored, so it carries the masked form."""
    project = finalised(client)
    reply = say(client, project["projectId"], "send the proposal")

    assert "***@example.com" in reply["assistantMessage"]
    assert project["customer"]["email"] not in reply["assistantMessage"]


def test_confirming_immediately_after_the_offer_sends(client, outbox) -> None:
    project = finalised(client)
    say(client, project["projectId"], "send the proposal")
    reply = say(client, project["projectId"], "yes")

    assert [m.to for m in outbox.outbox] == [project["customer"]["email"]]
    assert "recorded" in reply["assistantMessage"].lower()
    assert "console mode" in reply["assistantMessage"].lower(), (
        "console mode must not be reported as a real send"
    )


def test_a_second_confirmation_does_not_send_twice(client, outbox) -> None:
    project = finalised(client)
    say(client, project["projectId"], "send the proposal")
    say(client, project["projectId"], "yes")
    say(client, project["projectId"], "yes")

    assert len(outbox.outbox) == 1, "a repeated yes sent a second copy"


# ---------------------------------------------------------------------------
# Everything that must not send
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "can you email this?",
        "what would the email say?",
        "how do I send this to my customer?",
        "will they get an email?",
    ],
)
def test_a_question_about_sending_is_answered_not_acted_on(client, outbox, question: str) -> None:
    project = finalised(client)
    reply = say(client, project["projectId"], question)

    assert outbox.outbox == [], f"{question!r} sent a proposal"
    assert "shall i send it" not in reply["assistantMessage"].lower()


def test_a_bare_yes_with_no_offer_sends_nothing(client, outbox) -> None:
    project = finalised(client)
    say(client, project["projectId"], "yes")
    assert outbox.outbox == []


def test_a_yes_separated_from_the_offer_by_another_turn_sends_nothing(client, outbox) -> None:
    """The reason confirmation is read from the last message only.

    An offer two turns ago is not something a later "yes" can be assumed to be
    answering - and the cost of guessing wrong is an email in a real inbox.
    """
    project = finalised(client)
    say(client, project["projectId"], "send the proposal")
    say(client, project["projectId"], "what is my payback?")
    say(client, project["projectId"], "yes")

    assert outbox.outbox == [], "a stale confirmation sent the proposal"


def test_cancelling_the_offer_sends_nothing(client, outbox) -> None:
    project = finalised(client)
    say(client, project["projectId"], "send the proposal")
    say(client, project["projectId"], "never mind")
    say(client, project["projectId"], "yes")

    assert outbox.outbox == []


def test_a_message_merely_mentioning_email_is_not_a_send(client, outbox) -> None:
    project = finalised(client)
    say(client, project["projectId"], "my email provider is slow")
    assert outbox.outbox == []


# ---------------------------------------------------------------------------
# Nothing to send, or nobody to send to
# ---------------------------------------------------------------------------


def test_sending_before_finalisation_is_refused(client, outbox) -> None:
    project_id = client.post("/api/v1/projects").json()["projectId"]
    for message in (CASE_COORD, "1,150 kWh", "6 kWp"):
        say(client, project_id, message)

    reply = say(client, project_id, "send the proposal")

    assert "no finalised proposal" in reply["assistantMessage"].lower()
    assert outbox.outbox == []


def test_sending_a_proposal_with_no_customer_is_refused_with_a_way_forward(
    client, outbox
) -> None:
    project = finalised(client, with_customer=False)
    reply = say(client, project["projectId"], "send the proposal")

    assert "no customer linked" in reply["assistantMessage"].lower()
    assert "add a customer" in reply["assistantMessage"].lower()
    assert outbox.outbox == []


# ---------------------------------------------------------------------------
# The model cannot redirect it
# ---------------------------------------------------------------------------


def test_a_message_naming_a_different_recipient_cannot_redirect_the_send(
    client, outbox
) -> None:
    """The address comes from the frozen snapshot, never from the message.

    Nothing in the extraction types can carry an email address, so no phrasing
    - and no model output - has anywhere to put one.
    """
    project = finalised(client)
    say(client, project["projectId"], "send the proposal to attacker@evil.test")
    say(client, project["projectId"], "yes")

    assert [m.to for m in outbox.outbox] == [project["customer"]["email"]]
    assert all("attacker@evil.test" not in m.to for m in outbox.outbox)


def test_the_extraction_type_has_no_field_for_an_address(client) -> None:
    """Asserted on the type, because that is what makes it unrepresentable."""
    from app.domain.models import ExtractedValues

    fields = set(ExtractedValues.model_fields)
    assert not any("email" in f.lower() or "recipient" in f.lower() for f in fields), (
        f"the model can now express a recipient: {sorted(fields)}"
    )


# ---------------------------------------------------------------------------
# Failure is reported honestly
# ---------------------------------------------------------------------------


def test_a_failed_send_says_so_and_offers_the_link(client, outbox, monkeypatch) -> None:
    from app.services.email import EmailSendError

    project = finalised(client)

    async def _refuse(message):
        raise EmailSendError("EMAIL_SEND_FAILED", "The mail server refused the message.")

    say(client, project["projectId"], "send the proposal")
    monkeypatch.setattr(outbox, "send", _refuse)
    reply = say(client, project["projectId"], "yes")

    text = reply["assistantMessage"]
    assert "could not send" in text.lower()
    assert project["shareToken"] in text, "the fallback link was not offered"

    # And the proposal is untouched by the failure.
    assert client.get(f"/api/v1/proposals/{project['shareToken']}").status_code == 200


def test_a_send_is_recorded_on_the_timeline(client, outbox) -> None:
    project = finalised(client)
    say(client, project["projectId"], "send the proposal")
    say(client, project["projectId"], "yes")

    events = client.get(f"/api/v1/projects/{project['projectId']}/activity").json()["events"]
    assert "proposal.email_sent" in [e["eventType"] for e in events]
