"""The email providers, and the guarantees that keep them honest.

Four things are asserted here that nothing else can assert.

**A test environment cannot be configured to send real mail.** Not by
convention - the settings refuse to construct. The failure this prevents is not
a red test; it is real mail arriving at a real customer from a suite run, which
nothing in the suite would notice.

**Console never claims delivery.** Its result carries `delivered_to_provider =
False` and `provider = "console"`, which is what lets the UI say "recorded
locally" instead of "sent".

**A misconfigured relay is unavailable, not silently downgraded.** There is no
"smtp, falling back to console" - that fallback is the same lie as a fake
success, arrived at by a different route.

**A header cannot be forged.** Checked before the message is built, because by
the time `EmailMessage` would object the value has already been logged.
"""

from __future__ import annotations

import pytest

from app.core.config import EmailMode, Settings
from app.services.email import (
    ConsoleEmailSender,
    EmailMessage,
    EmailSendError,
    SmtpEmailSender,
    assert_header_safe,
    build_mime,
    build_sender,
)


def settings_for(**overrides) -> Settings:
    # `allow_replay_proposals` is pinned off because the suite's own
    # environment sets it, and a bare `Settings()` reads the environment - so
    # without this every construction here would trip the *replay* validator
    # instead of exercising the email one.
    # Every field this module asserts on is pinned, because `Settings` reads
    # the developer's `.env`. These tests describe an *unconfigured* relay, and
    # they only did so for as long as nobody had real SMTP credentials sitting
    # there - the moment someone configured the live path to try it, the
    # "refuses when misconfigured" tests started passing a configured relay and
    # failing. A test's inputs come from the test.
    base = {
        "app_env": "development",
        "allow_replay_proposals": False,
        "email_mode": "console",
        "smtp_host": "",
        "smtp_port": 0,
        "smtp_username": "",
        "smtp_password": "",
        "email_from": "proposals@solarvis.test",
        "email_from_name": "SolarVis",
        "email_reply_to": "",
    }
    return Settings(**{**base, **overrides})


def a_message(**overrides) -> EmailMessage:
    base = {
        "to": "anna@example.com",
        "subject": "Your solar proposal",
        "text_body": "Hi Anna,\n\nYour proposal is ready.\n",
    }
    return EmailMessage(**{**base, **overrides})


# ---------------------------------------------------------------------------
# Tests cannot send real email
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("app_env", ["test", "e2e", "verification", "TEST", "E2e"])
def test_smtp_is_refused_in_a_test_environment(app_env: str) -> None:
    """The settings refuse to construct. The process does not start."""
    with pytest.raises(ValueError, match="EMAIL_MODE=smtp is refused"):
        settings_for(app_env=app_env, email_mode="smtp")


@pytest.mark.parametrize("app_env", ["development", "production", "staging"])
def test_smtp_is_permitted_everywhere_else(app_env: str) -> None:
    assert settings_for(app_env=app_env, email_mode="smtp").email_mode is EmailMode.SMTP


def test_console_is_permitted_in_a_test_environment(app_env: str = "test") -> None:
    assert settings_for(app_env=app_env).email_mode is EmailMode.CONSOLE


def test_the_suites_own_configuration_cannot_send(client) -> None:
    """Belt and braces: what the running application is actually configured with."""
    from app.core.config import get_settings

    settings = get_settings()
    assert settings.app_env.lower() in {"test", "e2e", "verification"}
    assert settings.email_mode is EmailMode.CONSOLE
    assert build_sender(settings).name == "console"


# ---------------------------------------------------------------------------
# Readiness reports it, without calling anything
# ---------------------------------------------------------------------------


def test_readiness_reports_that_console_mode_does_not_send(client) -> None:
    """The one place an operator can see it without reading the environment.

    `sends: false` is the field that stops console mode being mistaken for a
    working mail configuration - a check that only reported `ready: true` would
    read as "email works".
    """
    email = client.get("/api/v1/health/ready").json()["checks"]["email"]

    assert email["provider"] == "console"
    assert email["sends"] is False
    assert email["ready"] is True
    assert "sends nothing" in (email["detail"] or "")


def test_readiness_does_not_open_a_connection(client, monkeypatch) -> None:
    """A readiness probe that dialled a relay would be a fine way to get blocked."""
    import smtplib

    def _forbidden(*args, **kwargs):
        raise AssertionError("readiness opened an SMTP connection")

    monkeypatch.setattr(smtplib, "SMTP", _forbidden)
    assert client.get("/api/v1/health/ready").status_code == 200


# ---------------------------------------------------------------------------
# Provider selection
# ---------------------------------------------------------------------------


def test_console_mode_builds_the_console_sender() -> None:
    assert isinstance(build_sender(settings_for()), ConsoleEmailSender)


def test_smtp_mode_builds_the_smtp_sender() -> None:
    sender = build_sender(settings_for(email_mode="smtp", smtp_host="mail.test", smtp_port=587))
    assert isinstance(sender, SmtpEmailSender)


def test_a_misconfigured_relay_is_unavailable_rather_than_downgraded() -> None:
    """The absence of a fallback is the point.

    Falling back to console here would report a send that never left the
    building - indistinguishable, to the operator, from one that did.
    """
    sender = build_sender(settings_for(email_mode="smtp"))
    ready, reason = sender.available()

    assert isinstance(sender, SmtpEmailSender), "it must not quietly become the console sender"
    assert ready is False
    assert reason and "SMTP_HOST" in reason


@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({"smtp_port": 587, "email_from": "a@b.test"}, "SMTP_HOST"),
        ({"smtp_host": "mail.test", "email_from": "a@b.test"}, "SMTP_PORT"),
        ({"smtp_host": "mail.test", "smtp_port": 587, "email_from": ""}, "EMAIL_FROM"),
    ],
)
def test_each_missing_setting_is_named(overrides: dict, expected: str) -> None:
    sender = SmtpEmailSender(settings_for(email_mode="smtp", **overrides))
    ready, reason = sender.available()
    assert ready is False
    assert reason and expected in reason


def test_a_fully_configured_relay_reports_ready() -> None:
    sender = SmtpEmailSender(settings_for(email_mode="smtp", smtp_host="mail.test", smtp_port=587))
    assert sender.available() == (True, None)


# ---------------------------------------------------------------------------
# Console records, and says it did not send
# ---------------------------------------------------------------------------


async def test_console_records_without_claiming_delivery() -> None:
    sender = ConsoleEmailSender(settings_for())
    result = await sender.send(a_message())

    assert result.provider == "console"
    assert result.delivered_to_provider is False, "console must never report delivery"
    assert result.provider_message_id
    assert [m.to for m in sender.outbox] == ["anna@example.com"]


async def test_console_still_validates_the_message_it_records() -> None:
    """Rendering and header faults must surface in development, not production."""
    sender = ConsoleEmailSender(settings_for())
    with pytest.raises(EmailSendError):
        await sender.send(a_message(subject="Broken\r\nBcc: attacker@evil.test"))


async def test_the_console_outbox_is_bounded() -> None:
    sender = ConsoleEmailSender(settings_for(), outbox_limit=3)
    for index in range(6):
        await sender.send(a_message(subject=f"Proposal {index}"))
    assert [m.subject for m in sender.outbox] == ["Proposal 3", "Proposal 4", "Proposal 5"]


async def test_smtp_refuses_to_attempt_a_send_it_cannot_make() -> None:
    """Refused before any socket is opened, so nothing is half-done."""
    sender = SmtpEmailSender(settings_for(email_mode="smtp"))
    with pytest.raises(EmailSendError) as raised:
        await sender.send(a_message())
    assert raised.value.code == "EMAIL_PROVIDER_UNAVAILABLE"


# ---------------------------------------------------------------------------
# Header injection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "Your proposal\r\nBcc: attacker@evil.test",
        "Your proposal\nBcc: attacker@evil.test",
        "Your proposal\r",
        "Your\nproposal",
    ],
)
def test_a_header_value_containing_a_line_break_is_refused(value: str) -> None:
    with pytest.raises(EmailSendError) as raised:
        assert_header_safe(value, field_name="Subject")
    assert raised.value.code == "EMAIL_HEADER_INVALID"


def test_an_injected_subject_never_reaches_the_built_message() -> None:
    with pytest.raises(EmailSendError):
        build_mime(a_message(subject="Hello\r\nBcc: attacker@evil.test"), settings_for())


def test_an_injected_recipient_never_reaches_the_built_message() -> None:
    with pytest.raises(EmailSendError):
        build_mime(a_message(to="anna@example.com\r\nBcc: attacker@evil.test"), settings_for())


def test_an_injected_sender_name_never_reaches_the_built_message() -> None:
    with pytest.raises(EmailSendError):
        build_mime(a_message(), settings_for(email_from_name="SolarVis\r\nBcc: a@evil.test"))


# ---------------------------------------------------------------------------
# The built message
# ---------------------------------------------------------------------------


def test_the_message_carries_the_configured_sender() -> None:
    mime = build_mime(a_message(), settings_for())
    assert mime["From"] == "SolarVis <proposals@solarvis.test>"
    assert mime["To"] == "anna@example.com"
    assert mime["Subject"] == "Your solar proposal"
    assert mime["Message-ID"]


def test_the_message_id_is_stamped_with_the_senders_domain() -> None:
    """Not the hostname, which in a container is its id.

    `make_msgid()` with no argument produced `<...@c1ab3f949ac8>`: a domain
    that cannot exist, which spam filters score against, and which publishes
    the container id in a header that reaches every recipient and stays in
    their mailbox indefinitely.
    """
    message_id = str(build_mime(a_message(), settings_for())["Message-ID"])

    assert message_id.endswith("@solarvis.test>"), message_id
    assert "localhost" not in message_id


def test_a_reply_to_is_set_when_configured() -> None:
    mime = build_mime(a_message(), settings_for(email_reply_to="sales@solarvis.test"))
    assert mime["Reply-To"] == "sales@solarvis.test"


def test_no_reply_to_header_when_none_is_configured() -> None:
    assert build_mime(a_message(), settings_for())["Reply-To"] is None


def test_an_over_long_subject_is_truncated_rather_than_refused() -> None:
    mime = build_mime(a_message(subject="x" * 500), settings_for())
    assert len(str(mime["Subject"])) <= 300


def test_an_html_alternative_is_attached_when_supplied() -> None:
    mime = build_mime(a_message(html_body="<p>Hi Anna</p>"), settings_for())
    assert mime.is_multipart()
    types = {part.get_content_type() for part in mime.walk()}
    assert {"text/plain", "text/html"} <= types


def test_the_text_body_survives_intact() -> None:
    mime = build_mime(a_message(), settings_for())
    body = mime.get_body(preferencelist=("plain",))
    assert body is not None
    assert "Your proposal is ready." in body.get_content()
