"""Emailing a proposal, over HTTP.

The assertions that matter most are the ones about *not* sending: a preview
that sends nothing, an unconfirmed request that sends nothing, a misconfigured
provider that sends nothing and says so rather than quietly recording a
success.

The console provider makes this testable without a mail server. Its outbox is
in-process, so "was a message actually composed and handed over" is a direct
assertion rather than an inference from log output - and because the suite's
settings *refuse* to construct with `EMAIL_MODE=smtp`, none of this can
accidentally reach a real relay.
"""

from __future__ import annotations

import uuid

import pytest

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


def _ready_to_send(client) -> tuple[dict, dict]:
    customer = a_customer(client)
    return customer, _finalised(client, customer_id=customer["customerId"])


@pytest.fixture
def outbox(monkeypatch):
    """A single console sender shared by every send in one test.

    The route builds a sender per request, so without this each send would get
    a fresh outbox and "what was actually handed over" would be unobservable.
    """
    from app.core.config import get_settings
    from app.services import email as email_module
    from app.services import proposal_email
    from app.services.email import ConsoleEmailSender

    sender = ConsoleEmailSender(get_settings())
    monkeypatch.setattr(email_module, "build_sender", lambda settings: sender)
    monkeypatch.setattr(proposal_email, "build_sender", lambda settings: sender)
    return sender


# ---------------------------------------------------------------------------
# The preview sends nothing
# ---------------------------------------------------------------------------


def test_the_preview_renders_the_message_without_sending_it(client, outbox) -> None:
    customer, finalised = _ready_to_send(client)

    response = client.get(f"/api/v1/proposals/{finalised['proposalId']}/email-preview")
    assert response.status_code == 200, response.text

    preview = response.json()["preview"]
    assert preview["to"] == customer["email"]
    assert preview["toMasked"].endswith("@example.com")
    assert finalised["shareToken"] in preview["publicUrl"]
    assert preview["subject"].startswith("Your solar proposal")

    assert outbox.outbox == [], "the preview sent a message"
    assert client.get(
        f"/api/v1/proposals/{finalised['proposalId']}/deliveries"
    ).json()["deliveries"] == [], "the preview created a delivery record"


def test_the_preview_carries_the_real_figures_not_placeholders(client) -> None:
    _, finalised = _ready_to_send(client)
    served = client.get(f"/api/v1/proposals/{finalised['shareToken']}").json()

    preview = client.get(
        f"/api/v1/proposals/{finalised['proposalId']}/email-preview"
    ).json()["preview"]

    assert "6 kWp" in preview["textBody"]
    assert f"{float(served['energy']['totalAnnualProductionKwh']):,.0f}" in preview["textBody"]
    assert preview["reference"] in preview["textBody"]


def test_the_preview_reports_that_console_does_not_send(client) -> None:
    _, finalised = _ready_to_send(client)
    preview = client.get(
        f"/api/v1/proposals/{finalised['proposalId']}/email-preview"
    ).json()["preview"]

    assert preview["provider"] == "console"
    assert preview["providerSends"] is False, (
        "the UI reads this to avoid telling the operator a message was sent"
    )
    assert preview["providerAvailable"] is True


# ---------------------------------------------------------------------------
# Confirmation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("body", [{}, {"confirm": False}, {"resendNonce": "x"}])
def test_sending_without_an_explicit_confirmation_is_refused(client, outbox, body: dict) -> None:
    _, finalised = _ready_to_send(client)

    response = client.post(f"/api/v1/proposals/{finalised['proposalId']}/send", json=body)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SEND_CONFIRMATION_REQUIRED"
    assert outbox.outbox == [], "an unconfirmed request sent a message"


def test_a_confirmed_send_is_recorded_and_reported_honestly(client, outbox) -> None:
    customer, finalised = _ready_to_send(client)

    response = client.post(
        f"/api/v1/proposals/{finalised['proposalId']}/send", json={"confirm": True}
    )
    assert response.status_code == 200, response.text

    delivery = response.json()["delivery"]
    assert delivery["status"] == "sent"
    assert delivery["provider"] == "console"
    assert delivery["providerSends"] is False, "console mode must not read as delivered"
    assert delivery["attemptCount"] == 1
    assert delivery["sentAt"]
    assert delivery["errorCode"] is None

    assert [m.to for m in outbox.outbox] == [customer["email"]]


def test_the_delivery_list_masks_the_recipient(client, outbox) -> None:
    customer, finalised = _ready_to_send(client)
    client.post(f"/api/v1/proposals/{finalised['proposalId']}/send", json={"confirm": True})

    body = client.get(f"/api/v1/proposals/{finalised['proposalId']}/deliveries").text
    assert customer["email"] not in body, "the full address is only shown at confirmation"
    assert "***@example.com" in body


# ---------------------------------------------------------------------------
# There has to be somebody to send to
# ---------------------------------------------------------------------------


def test_a_proposal_with_no_customer_cannot_be_sent(client, outbox) -> None:
    finalised = _finalised(client)

    response = client.post(
        f"/api/v1/proposals/{finalised['proposalId']}/send", json={"confirm": True}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "RECIPIENT_UNAVAILABLE"
    assert outbox.outbox == []


def test_the_preview_of_a_customerless_proposal_explains_rather_than_errors(client) -> None:
    """A preview describes what *would* happen, including "nothing".

    Refusing here would leave the caller with a failed request and nothing to
    show the operator - the exact dead end the panel exists to avoid. The
    *send* still refuses; only the description is permissive.
    """
    finalised = _finalised(client)

    response = client.get(f"/api/v1/proposals/{finalised['proposalId']}/email-preview")
    assert response.status_code == 200, response.text

    preview = response.json()["preview"]
    assert preview["to"] is None
    assert preview["subject"] is None
    assert preview["publicUrl"].endswith(finalised["shareToken"])


def test_an_unknown_proposal_is_not_found(client) -> None:
    response = client.post(f"/api/v1/proposals/{uuid.uuid4()}/send", json={"confirm": True})
    assert response.status_code == 404


def test_the_send_route_is_not_reachable_with_a_share_token(client, outbox) -> None:
    """The token is what customers hold. It must not cause mail to be sent."""
    _, finalised = _ready_to_send(client)

    response = client.post(
        f"/api/v1/proposals/{finalised['shareToken']}/send", json={"confirm": True}
    )
    assert response.status_code == 404
    assert outbox.outbox == []


# ---------------------------------------------------------------------------
# Duplicates
# ---------------------------------------------------------------------------


def test_sending_the_same_proposal_twice_is_refused(client, outbox) -> None:
    """The second copy would land in a real person's inbox."""
    _, finalised = _ready_to_send(client)
    url = f"/api/v1/proposals/{finalised['proposalId']}/send"

    assert client.post(url, json={"confirm": True}).status_code == 200
    second = client.post(url, json={"confirm": True})

    assert second.status_code == 409
    assert second.json()["error"]["code"] == "PROPOSAL_ALREADY_SENT"
    assert second.json()["error"]["details"]["deliveryId"]
    assert len(outbox.outbox) == 1, "a duplicate request produced a second email"


def test_a_deliberate_resend_is_allowed_and_recorded_separately(client, outbox) -> None:
    _, finalised = _ready_to_send(client)
    url = f"/api/v1/proposals/{finalised['proposalId']}/send"

    assert client.post(url, json={"confirm": True}).status_code == 200
    resend = client.post(url, json={"confirm": True, "resendNonce": "operator-asked-again"})

    assert resend.status_code == 200, resend.text
    assert len(outbox.outbox) == 2

    deliveries = client.get(
        f"/api/v1/proposals/{finalised['proposalId']}/deliveries"
    ).json()["deliveries"]
    assert len(deliveries) == 2
    assert {d["status"] for d in deliveries} == {"sent"}


def test_the_idempotency_key_is_stable_for_one_intent(client) -> None:
    """Why a double click cannot produce two rows."""
    import asyncio

    from sqlalchemy import select

    from app.db.session import get_sessionmaker
    from app.models.tables import Proposal
    from app.services.proposal_email import idempotency_key

    _, finalised = _ready_to_send(client)

    async def _keys() -> tuple[str, str, str]:
        async with get_sessionmaker()() as session:
            proposal = (
                await session.execute(
                    select(Proposal).where(Proposal.id == finalised["proposalId"])
                )
            ).scalar_one()
            return (
                idempotency_key(proposal, "anna@example.com"),
                idempotency_key(proposal, "anna@example.com"),
                idempotency_key(proposal, "anna@example.com", nonce="again"),
            )

    first, second, with_nonce = asyncio.run(_keys())
    assert first == second, "the same intent computed two different keys"
    assert first != with_nonce, "a deliberate resend must not collide with the original"


# ---------------------------------------------------------------------------
# The race itself
# ---------------------------------------------------------------------------


async def test_two_concurrent_sends_produce_one_claim(client, outbox) -> None:
    """Tested on two independent sessions, where the race actually happens.

    `TestClient` drives every request through one event loop, so two threaded
    posts may simply run in sequence - which looks identical to a broken claim.
    This exercises the claim directly, on separate sessions, because the API is
    meant to run behind more than one worker: a lock held in one process would
    protect nothing.
    """
    import asyncio

    from sqlalchemy import select

    from app.core.config import get_settings
    from app.core.errors import DeliveryInProgressError
    from app.db.session import get_sessionmaker
    from app.models.tables import Proposal
    from app.services.proposal_email import claim, recipient_for

    _, finalised = _ready_to_send(client)
    settings = get_settings()

    async def _attempt() -> str:
        async with get_sessionmaker()() as session:
            proposal = (
                await session.execute(
                    select(Proposal).where(Proposal.id == finalised["proposalId"])
                )
            ).scalar_one()
            try:
                await claim(
                    session,
                    proposal,
                    recipient=recipient_for(proposal),
                    provider="console",
                    settings=settings,
                )
            except DeliveryInProgressError:
                return "refused"
            await session.commit()
            return "granted"

    outcomes = await asyncio.gather(_attempt(), _attempt(), return_exceptions=True)
    resolved = [o for o in outcomes if isinstance(o, str)]

    assert sorted(resolved) == ["granted", "refused"], (
        f"both sends claimed the same delivery: {outcomes}"
    )


def test_a_second_send_while_one_is_in_flight_is_refused(client, outbox) -> None:
    """The claim planted rather than raced for, so the refusal is deterministic.

    A `sending` row means somebody else owns this send. The second request must
    not compose a message or reach the provider - it must simply decline.
    """
    import asyncio

    from sqlalchemy import select

    from app.db.session import get_sessionmaker
    from app.models.tables import Proposal, ProposalDelivery, _utcnow
    from app.services.proposal_email import SENDING, idempotency_key, recipient_for

    _, finalised = _ready_to_send(client)

    async def _hold() -> None:
        async with get_sessionmaker()() as session:
            proposal = (
                await session.execute(
                    select(Proposal).where(Proposal.id == finalised["proposalId"])
                )
            ).scalar_one()
            recipient = recipient_for(proposal)
            session.add(
                ProposalDelivery(
                    proposal_id=proposal.id,
                    recipient=recipient,
                    provider="console",
                    status=SENDING,
                    idempotency_key=idempotency_key(proposal, recipient),
                    attempt_count=1,
                    last_attempt_at=_utcnow(),
                )
            )
            await session.commit()

    asyncio.run(_hold())
    outbox.outbox.clear()

    response = client.post(
        f"/api/v1/proposals/{finalised['proposalId']}/send", json={"confirm": True}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DELIVERY_IN_PROGRESS"
    assert outbox.outbox == [], "the refused request reached the provider anyway"


# ---------------------------------------------------------------------------
# Failure leaves the proposal usable
# ---------------------------------------------------------------------------


def test_a_provider_failure_is_recorded_and_the_proposal_still_works(
    client, outbox, monkeypatch
) -> None:
    """The fallback the UI offers: the link still works, so copy it."""
    from app.services.email import EmailSendError

    _, finalised = _ready_to_send(client)

    async def _refuse(message):
        raise EmailSendError("EMAIL_SEND_FAILED", "The mail server refused the message.")

    monkeypatch.setattr(outbox, "send", _refuse)

    response = client.post(
        f"/api/v1/proposals/{finalised['proposalId']}/send", json={"confirm": True}
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "EMAIL_SEND_FAILED"

    deliveries = client.get(
        f"/api/v1/proposals/{finalised['proposalId']}/deliveries"
    ).json()["deliveries"]
    assert deliveries[0]["status"] == "failed"
    assert deliveries[0]["errorCode"] == "EMAIL_SEND_FAILED"

    # The document and its link are untouched by a failed send.
    served = client.get(f"/api/v1/proposals/{finalised['shareToken']}")
    assert served.status_code == 200
    assert served.json()["financial"]


def test_a_failed_delivery_can_be_retried_and_succeed(client, outbox, monkeypatch) -> None:
    from app.services.email import EmailSendError

    _, finalised = _ready_to_send(client)

    async def _refuse(message):
        raise EmailSendError("EMAIL_SEND_FAILED", "temporary")

    monkeypatch.setattr(outbox, "send", _refuse)
    failed = client.post(
        f"/api/v1/proposals/{finalised['proposalId']}/send", json={"confirm": True}
    )
    assert failed.status_code == 502

    delivery_id = client.get(
        f"/api/v1/proposals/{finalised['proposalId']}/deliveries"
    ).json()["deliveries"][0]["deliveryId"]

    monkeypatch.undo()
    retried = client.post(
        f"/api/v1/proposals/{finalised['proposalId']}/deliveries/{delivery_id}/retry",
        json={"confirm": True},
    )
    assert retried.status_code == 200, retried.text
    assert retried.json()["delivery"]["status"] == "sent"
    assert retried.json()["delivery"]["attemptCount"] == 2, "the retry reused the same row"
    assert retried.json()["delivery"]["errorCode"] is None


def test_a_retry_also_requires_confirmation(client, outbox) -> None:
    """A retry is still a send, and the first one may have gone to the wrong person."""
    _, finalised = _ready_to_send(client)
    client.post(f"/api/v1/proposals/{finalised['proposalId']}/send", json={"confirm": True})
    delivery_id = client.get(
        f"/api/v1/proposals/{finalised['proposalId']}/deliveries"
    ).json()["deliveries"][0]["deliveryId"]

    response = client.post(
        f"/api/v1/proposals/{finalised['proposalId']}/deliveries/{delivery_id}/retry",
        json={"confirm": False},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SEND_CONFIRMATION_REQUIRED"


def test_a_timeout_is_reported_as_unknown_rather_than_failed(client, outbox, monkeypatch) -> None:
    """The one outcome that is genuinely ambiguous.

    The relay may have accepted the message before going quiet, so reporting
    "not sent" would be a guess dressed as a fact.
    """
    from app.services.email import EmailSendError

    _, finalised = _ready_to_send(client)

    async def _hang(message):
        raise EmailSendError("EMAIL_SEND_TIMEOUT", "The mail server did not respond within 10s.")

    monkeypatch.setattr(outbox, "send", _hang)

    response = client.post(
        f"/api/v1/proposals/{finalised['proposalId']}/send", json={"confirm": True}
    )
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "EMAIL_SEND_TIMEOUT"
    assert "not known" in response.json()["error"]["message"]


# ---------------------------------------------------------------------------
# A provider that cannot send never pretends
# ---------------------------------------------------------------------------


def test_an_unavailable_provider_refuses_rather_than_recording_a_success(
    client, monkeypatch
) -> None:
    """No fallback to console. The absence of one is the point."""
    from app.core.config import get_settings
    from app.services import proposal_email
    from app.services.email import SmtpEmailSender

    _, finalised = _ready_to_send(client)

    # An SMTP sender with no host: available() is false before anything
    # happens. The host is blanked explicitly rather than assumed absent -
    # `get_settings()` reads the developer's `.env`, which may well carry
    # working credentials for testing the live path.
    broken = SmtpEmailSender(get_settings().model_copy(update={"smtp_host": ""}))
    monkeypatch.setattr(proposal_email, "build_sender", lambda settings: broken)

    response = client.post(
        f"/api/v1/proposals/{finalised['proposalId']}/send", json={"confirm": True}
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "EMAIL_PROVIDER_UNAVAILABLE"

    assert client.get(
        f"/api/v1/proposals/{finalised['proposalId']}/deliveries"
    ).json()["deliveries"] == [], "a delivery row was created for a send that never happened"

    assert client.get(f"/api/v1/proposals/{finalised['shareToken']}").status_code == 200


def test_disabling_proposal_email_refuses_without_pretending(client, monkeypatch) -> None:
    from app.core.config import get_settings

    _, finalised = _ready_to_send(client)

    settings = get_settings()
    monkeypatch.setattr(settings, "proposal_email_enabled", False)

    response = client.post(
        f"/api/v1/proposals/{finalised['proposalId']}/send", json={"confirm": True}
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "EMAIL_PROVIDER_UNAVAILABLE"


# ---------------------------------------------------------------------------
# The message itself
# ---------------------------------------------------------------------------


def test_the_email_contains_the_public_link_and_no_customer_address(client, outbox) -> None:
    customer, finalised = _ready_to_send(client)
    client.post(f"/api/v1/proposals/{finalised['proposalId']}/send", json={"confirm": True})

    message = outbox.outbox[0]
    assert finalised["shareToken"] in message.text_body
    assert finalised["shareToken"] in (message.html_body or "")
    # The address is the envelope, never the body - a forwarded proposal must
    # not carry the original recipient's address in its text.
    assert customer["email"] not in message.text_body
    assert customer["email"] not in (message.html_body or "")


def test_a_customer_display_name_is_escaped_in_the_html_body(client, outbox) -> None:
    """Operator-entered free text reaches an HTML document unmodified."""
    customer = a_customer(client, displayName="<script>alert(1)</script>")
    finalised = _finalised(client, customer_id=customer["customerId"])
    client.post(f"/api/v1/proposals/{finalised['proposalId']}/send", json={"confirm": True})

    html = outbox.outbox[0].html_body or ""
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_the_email_figures_match_the_issued_proposal(client, outbox) -> None:
    """Both read the frozen snapshot, so they cannot disagree."""
    _, finalised = _ready_to_send(client)
    served = client.get(f"/api/v1/proposals/{finalised['shareToken']}").json()

    client.post(f"/api/v1/proposals/{finalised['proposalId']}/send", json={"confirm": True})
    body = outbox.outbox[0].text_body

    assert f"{float(served['financial']['annualSavingsEur']):,.0f}" in body
    assert f"{float(served['energy']['totalAnnualProductionKwh']):,.0f}" in body


# ---------------------------------------------------------------------------
# The timeline records it
# ---------------------------------------------------------------------------


def test_a_send_appears_on_the_project_timeline_with_a_masked_recipient(client, outbox) -> None:
    customer, finalised = _ready_to_send(client)
    client.post(f"/api/v1/proposals/{finalised['proposalId']}/send", json={"confirm": True})

    events = client.get(f"/api/v1/projects/{finalised['projectId']}/activity").json()["events"]
    types = [e["eventType"] for e in events]

    assert "proposal.send_requested" in types
    assert "proposal.email_sent" in types

    sent = next(e for e in events if e["eventType"] == "proposal.email_sent")
    assert sent["metadata"]["provider"] == "console"
    assert sent["metadata"]["recipientMasked"].endswith("@example.com")
    assert customer["email"] not in str(events), "the timeline carried a full address"
