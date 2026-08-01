"""Proposal-view tracking: what counts as someone looking.

The count is the number an operator reads to decide whether to chase a
customer, so the failure modes that matter are the ones that *inflate* it.
Three of them are defended here.

A **refresh**. The share page posts on every mount, so scrolling back or
reopening a tab would otherwise each register as a fresh opening.

A **link unfurler**. Pasting the share link into a chat app fetches it
immediately - so without a crawler filter, a proposal reads as viewed before
the customer has seen anything at all. This is the worst of the three, because
it happens at exactly the moment the operator is watching.

A **PDF download**. A different act from opening the page, recorded separately.

And one thing that is *not* here, deliberately: there is no email-open
tracking, so nothing in this file or the UI ever uses the word "opened" about
an email.
"""

from __future__ import annotations

import pytest

CASE_COORD = "-34.04658242871865, 18.46491476666948"


def finalised(client) -> dict:
    project_id = client.post("/api/v1/projects").json()["projectId"]
    for message in (CASE_COORD, "1,150 kWh", "6 kWp"):
        client.post(f"/api/v1/projects/{project_id}/chat", json={"message": message})
    client.post(f"/api/v1/projects/{project_id}/run-analysis")
    response = client.post(f"/api/v1/projects/{project_id}/finalize")
    assert response.status_code == 200
    return {"projectId": project_id, **response.json()}


def _view(client, token: str, **headers) -> dict:
    return client.post(f"/api/v1/proposals/{token}/view", headers=headers).json()


def _stats(client, token: str) -> dict:
    return client.get(f"/api/v1/proposals/{token}").json()["views"]


# ---------------------------------------------------------------------------
# First and last
# ---------------------------------------------------------------------------


def test_an_unviewed_proposal_reports_no_views(client) -> None:
    stats = _stats(client, finalised(client)["shareToken"])
    assert stats == {"viewCount": 0, "firstOpenedAt": None, "lastOpenedAt": None}


def test_the_first_view_sets_both_timestamps(client) -> None:
    token = finalised(client)["shareToken"]
    _view(client, token)

    stats = _stats(client, token)
    assert stats["viewCount"] == 1
    assert stats["firstOpenedAt"] == stats["lastOpenedAt"]


def test_a_later_view_moves_only_the_last_timestamp(client) -> None:
    """Two different questions: "have they looked at all" and "are they still"."""
    token = finalised(client)["shareToken"]
    _view(client, token, **{"user-agent": "reader-one/1.0"})
    first = _stats(client, token)

    _view(client, token, **{"user-agent": "reader-two/1.0"})
    later = _stats(client, token)

    assert later["viewCount"] == 2
    assert later["firstOpenedAt"] == first["firstOpenedAt"], "the first opening was rewritten"
    assert later["lastOpenedAt"] > first["lastOpenedAt"]


def test_the_timestamps_are_utc_aware(client) -> None:
    """A naive ISO string is parsed as *local* time by a browser."""
    token = finalised(client)["shareToken"]
    _view(client, token)

    stats = _stats(client, token)
    assert stats["firstOpenedAt"].endswith("+00:00")
    assert stats["lastOpenedAt"].endswith("+00:00")


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def test_rapid_refreshes_are_one_visit(client) -> None:
    token = finalised(client)["shareToken"]
    for _ in range(6):
        _view(client, token)

    assert _stats(client, token)["viewCount"] == 1


def test_a_suppressed_view_still_returns_the_real_total(client) -> None:
    """The customer's page must behave identically either way."""
    token = finalised(client)["shareToken"]
    _view(client, token)
    repeat = _view(client, token)

    assert repeat["recorded"] is True
    assert repeat["viewCount"] == 1
    assert repeat["counted"] is False


def test_a_zero_window_counts_every_request(client, monkeypatch) -> None:
    """The window is configuration, not a hardcoded rule."""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "proposal_view_dedup_minutes", 0.0)
    token = finalised(client)["shareToken"]

    _view(client, token)
    second = _view(client, token)
    assert second["viewCount"] == 2


# ---------------------------------------------------------------------------
# Crawlers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "agent",
    [
        "WhatsApp/2.23.20",
        "TelegramBot (like TwitterBot)",
        "Slackbot-LinkExpanding 1.0",
        "facebookexternalhit/1.1",
        "Mozilla/5.0 (compatible; Googlebot/2.1)",
        "curl/8.4.0",
        "python-requests/2.31 spider",
        "HeadlessChrome/120.0",
    ],
)
def test_a_crawler_is_not_counted(client, agent: str) -> None:
    """The failure that would surface at the worst possible moment.

    Pasting the link into a chat fetches it at once, so without this the
    operator sees "viewed" seconds after sending - before the customer has
    opened anything.
    """
    token = finalised(client)["shareToken"]
    body = _view(client, token, **{"user-agent": agent})

    assert body["counted"] is False
    assert _stats(client, token)["viewCount"] == 0
    assert _stats(client, token)["firstOpenedAt"] is None


def test_a_crawler_does_not_block_the_real_customer(client) -> None:
    token = finalised(client)["shareToken"]
    _view(client, token, **{"user-agent": "WhatsApp/2.23.20"})
    real = _view(client, token, **{"user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)"})

    assert real["counted"] is True
    assert _stats(client, token)["viewCount"] == 1


def test_an_ordinary_browser_is_counted(client) -> None:
    """Guard: a deny-list that matched everything would pass every test above."""
    token = finalised(client)["shareToken"]
    body = _view(
        client,
        token,
        **{
            "user-agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        },
    )
    assert body["counted"] is True


# ---------------------------------------------------------------------------
# The stored hash
# ---------------------------------------------------------------------------


def test_the_ip_is_hashed_and_salted() -> None:
    """An unsalted digest of an IP address is not anonymisation.

    The IPv4 space is four billion values, so a rainbow table over it is
    trivial and the digest identifies the address exactly.
    """
    from app.services.proposal import hash_ip

    raw = "203.0.113.9"
    unsalted = hash_ip(raw)
    salted = hash_ip(raw, salt="deployment-secret")

    assert unsalted != raw
    assert salted != unsalted, "the salt had no effect"
    assert len(salted or "") == 32
    assert hash_ip(None, salt="x") is None


def test_the_same_address_hashes_consistently_under_one_salt(client) -> None:
    """Which is what makes dedup work at all."""
    from app.services.proposal import hash_ip

    assert hash_ip("203.0.113.9", salt="s") == hash_ip("203.0.113.9", salt="s")
    assert hash_ip("203.0.113.9", salt="s") != hash_ip("203.0.113.10", salt="s")


def test_no_raw_address_is_stored(client) -> None:
    import asyncio

    from sqlalchemy import select

    from app.db.session import get_sessionmaker
    from app.models.tables import Proposal, ProposalView

    token = finalised(client)["shareToken"]
    _view(client, token)

    async def _rows() -> list[str | None]:
        async with get_sessionmaker()() as session:
            proposal = (
                await session.execute(select(Proposal).where(Proposal.share_token == token))
            ).scalar_one()
            views = (
                await session.execute(
                    select(ProposalView).where(ProposalView.proposal_id == proposal.id)
                )
            ).scalars()
            return [v.ip_hash for v in views]

    for stored in asyncio.run(_rows()):
        assert stored is None or "." not in stored, "an address was stored, not a hash"


# ---------------------------------------------------------------------------
# The open notification
# ---------------------------------------------------------------------------


def test_no_notification_is_sent_when_no_recipient_is_configured(client, monkeypatch) -> None:
    """The honest default: there is no operator account to infer an address from."""
    from app.core.config import get_settings
    from app.services import proposal_email
    from app.services.email import ConsoleEmailSender

    settings = get_settings()
    monkeypatch.setattr(settings, "salesperson_email", "")
    sender = ConsoleEmailSender(settings)
    monkeypatch.setattr(proposal_email, "build_sender", lambda s: sender)

    token = finalised(client)["shareToken"]
    _view(client, token)

    assert sender.outbox == []


def test_a_counted_view_notifies_the_salesperson_once(client, monkeypatch) -> None:
    from app.core.config import get_settings
    from app.services import proposal_email
    from app.services.email import ConsoleEmailSender

    settings = get_settings()
    monkeypatch.setattr(settings, "salesperson_email", "sales@solarvis.test")
    sender = ConsoleEmailSender(settings)
    monkeypatch.setattr(proposal_email, "build_sender", lambda s: sender)

    token = finalised(client)["shareToken"]
    _view(client, token)
    _view(client, token)  # a refresh: same visit, no second notification

    assert [m.to for m in sender.outbox] == ["sales@solarvis.test"]
    assert "[Proposal Viewed]" in sender.outbox[0].text_body


def test_the_notification_says_it_is_a_page_view_not_an_email_open(
    client, monkeypatch
) -> None:
    """There is no open tracking, so nothing may imply there is."""
    from app.core.config import get_settings
    from app.services import proposal_email
    from app.services.email import ConsoleEmailSender

    settings = get_settings()
    monkeypatch.setattr(settings, "salesperson_email", "sales@solarvis.test")
    sender = ConsoleEmailSender(settings)
    monkeypatch.setattr(proposal_email, "build_sender", lambda s: sender)

    token = finalised(client)["shareToken"]
    _view(client, token)

    body = sender.outbox[0].text_body
    assert "not an email open" in body
    assert "no open tracking" in body

    # And it carries the block the case brief prints for the console
    # notification, so both modes say the same thing.
    assert "[Proposal Viewed]" in body
    for field in ("Proposal:", "Opened at:", "View count:"):
        assert field in body, f"the notification is missing {field!r}"


def test_a_failing_notification_does_not_break_the_customers_page(
    client, monkeypatch
) -> None:
    from app.core.config import get_settings
    from app.services import proposal_email

    settings = get_settings()
    monkeypatch.setattr(settings, "salesperson_email", "sales@solarvis.test")

    async def _explode(*args, **kwargs):
        raise RuntimeError("the mail server is on fire")

    monkeypatch.setattr(proposal_email, "notify_opened", _explode)

    token = finalised(client)["shareToken"]
    response = client.post(f"/api/v1/proposals/{token}/view")

    assert response.status_code == 200, "a notification failure reached the customer"
    assert response.json()["viewCount"] == 1


def test_a_crawler_never_pages_the_salesperson(client, monkeypatch) -> None:
    from app.core.config import get_settings
    from app.services import proposal_email
    from app.services.email import ConsoleEmailSender

    settings = get_settings()
    monkeypatch.setattr(settings, "salesperson_email", "sales@solarvis.test")
    sender = ConsoleEmailSender(settings)
    monkeypatch.setattr(proposal_email, "build_sender", lambda s: sender)

    token = finalised(client)["shareToken"]
    _view(client, token, **{"user-agent": "WhatsApp/2.23.20"})

    assert sender.outbox == [], "a link preview notified the salesperson"


def test_an_unknown_proposal_is_not_found(client) -> None:
    assert client.post("/api/v1/proposals/" + "q" * 40 + "/view").status_code == 404


def test_a_view_never_appears_on_a_different_proposal(client) -> None:
    first, second = finalised(client), finalised(client)
    _view(client, first["shareToken"])

    assert _stats(client, first["shareToken"])["viewCount"] == 1
    assert _stats(client, second["shareToken"])["viewCount"] == 0
