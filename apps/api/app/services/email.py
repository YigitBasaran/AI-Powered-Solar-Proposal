"""Sending email, and refusing to pretend.

Two providers, both named by the case brief: `console` records the rendered
message locally, `smtp` hands it to a relay. Nothing else - no vendor SDK, no
API key, no new dependency. `smtplib` and `email.message` are stdlib.

Three rules hold this together.

**Console is never reported as delivery.** The provider name travels with the
result and onto the delivery record, so the caller and the UI can say "recorded
locally (console mode)" instead of "sent". A development mode that reports
success indistinguishable from a real send is a lie that only surfaces when a
customer says they never received anything.

**Misconfiguration fails loudly and early.** `available()` answers before any
work is done, so a blank `SMTP_HOST` under `EMAIL_MODE=smtp` produces a clear
503 and an unchanged proposal - never a silent fallback to console, which would
be the same lie by a different route.

**A header can never be forged.** Every header value is checked for CR and LF
before the message is built. `email.message.EmailMessage` would raise on some
of these anyway, but not all, and not before the value has been logged.

`smtplib` is blocking, so the send runs in a worker thread with an explicit
timeout. That is a deliberate limitation rather than an oversight: there is no
job queue in this application, and introducing one for a single outbound call
would be a larger change than the feature. Documented in known-limitations.
"""

from __future__ import annotations

import asyncio
import logging
import re
import smtplib
import ssl
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.message import EmailMessage as MimeMessage
from email.utils import formataddr, make_msgid
from typing import Protocol

from app.core.config import EmailMode, Settings
from app.domain.customers import mask_email

logger = logging.getLogger("solarvis.email")

#: CR or LF anywhere in a header value. The primitive behind header injection:
#: `Subject: x\r\nBcc: attacker@evil.test` becomes two headers.
_HEADER_BREAK = re.compile(r"[\r\n]")

MAX_SUBJECT_LENGTH = 300


class EmailSendError(Exception):
    """A send that did not happen, with a code the delivery record can store.

    Carries a *mapped* code rather than a provider traceback. The raw exception
    text can contain a relay's own prose, a hostname, or - with some
    configurations - part of a credential, and none of that belongs in an API
    response or a stored row.
    """

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class EmailAttachment:
    filename: str
    content: bytes
    media_type: str = "application/pdf"


@dataclass(frozen=True)
class EmailMessage:
    """A message ready to send. Built by `services/proposal_email.py`."""

    to: str
    subject: str
    text_body: str
    html_body: str | None = None
    reply_to: str | None = None
    attachments: tuple[EmailAttachment, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class SendResult:
    provider: str
    provider_message_id: str | None
    accepted_at: datetime
    #: False for `console`. The one field that stops "recorded" being read as
    #: "delivered" anywhere downstream.
    delivered_to_provider: bool


def assert_header_safe(value: str, *, field_name: str) -> str:
    """Refuse a header value that could terminate its own line."""
    if _HEADER_BREAK.search(value):
        raise EmailSendError(
            "EMAIL_HEADER_INVALID",
            f"{field_name} contains a line break and cannot be used as a header.",
        )
    return value


def _sender_address(settings: Settings) -> str:
    return settings.email_from.strip()


def build_mime(message: EmailMessage, settings: Settings) -> MimeMessage:
    """The wire format, with every header checked before it is set."""
    subject = assert_header_safe(message.subject.strip(), field_name="Subject")[
        :MAX_SUBJECT_LENGTH
    ]
    recipient = assert_header_safe(message.to.strip(), field_name="Recipient")
    sender = assert_header_safe(_sender_address(settings), field_name="Sender")
    display = assert_header_safe(settings.email_from_name.strip(), field_name="Sender name")

    mime = MimeMessage()
    mime["Subject"] = subject
    mime["From"] = formataddr((display, sender)) if display else sender
    mime["To"] = recipient
    mime["Date"] = datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S +0000")

    # The Message-ID's domain comes from the sender, not from the host.
    #
    # `make_msgid()` with no argument uses the local hostname, which inside a
    # container is its id - so every message went out stamped
    # `<...@c1ab3f949ac8>`. That is a domain that cannot exist, which spam
    # filters score against, and it publishes the container id in a header that
    # travels to every recipient and sits in their mail forever.
    _, _, sender_domain = sender.rpartition("@")
    mime["Message-ID"] = make_msgid(domain=sender_domain or None)

    reply_to = (message.reply_to or settings.email_reply_to or "").strip()
    if reply_to:
        mime["Reply-To"] = assert_header_safe(reply_to, field_name="Reply-To")

    mime.set_content(message.text_body)
    if message.html_body:
        mime.add_alternative(message.html_body, subtype="html")

    for attachment in message.attachments:
        maintype, _, subtype = attachment.media_type.partition("/")
        mime.add_attachment(
            attachment.content,
            maintype=maintype or "application",
            subtype=subtype or "octet-stream",
            filename=assert_header_safe(attachment.filename, field_name="Attachment filename"),
        )

    return mime


class ProposalEmailSender(Protocol):
    """What a provider has to offer. Deliberately three methods wide."""

    name: str

    def available(self) -> tuple[bool, str | None]:
        """`(ready, reason_if_not)`. Never makes a network call."""
        ...

    async def send(self, message: EmailMessage) -> SendResult: ...


class ConsoleEmailSender:
    """Records the rendered message. Sends nothing, and says so.

    The development and test provider. Every result it returns carries
    `delivered_to_provider=False` and `provider="console"`, which is what makes
    "recorded locally" surfaceable all the way to the UI rather than being a
    detail only the log knows.

    The outbox is in-process and unbounded-but-trimmed: it exists so tests can
    assert what *would* have been sent without reading log output.
    """

    name = "console"

    def __init__(self, settings: Settings, *, outbox_limit: int = 100) -> None:
        self._settings = settings
        self._outbox_limit = outbox_limit
        self.outbox: list[EmailMessage] = []

    def available(self) -> tuple[bool, str | None]:
        return True, None

    async def send(self, message: EmailMessage) -> SendResult:
        # Built even though nothing is transmitted: header validation and
        # rendering are most of what can go wrong, and console mode is where
        # that should be discovered rather than in production.
        mime = build_mime(message, self._settings)

        self.outbox.append(message)
        del self.outbox[: max(0, len(self.outbox) - self._outbox_limit)]

        # The body is printed, not just the envelope. The case brief specifies
        # a console notification and shows its shape - `[Proposal Viewed]` with
        # the reference, the time and the count - and that block lives in the
        # message body. Logging only To/Subject/size would satisfy "a console
        # notification exists" while showing an operator none of what it says.
        logger.info(
            "[Email recorded - console mode, NOT sent]\n"
            "  To:      %s\n"
            "  Subject: %s\n"
            "  Bytes:   %d\n"
            "--------------------------------------------------\n"
            "%s"
            "--------------------------------------------------",
            mask_email(message.to),
            message.subject,
            len(bytes(mime)),
            message.text_body,
        )
        return SendResult(
            provider=self.name,
            provider_message_id=str(mime["Message-ID"]),
            accepted_at=datetime.now(UTC),
            delivered_to_provider=False,
        )


class SmtpEmailSender:
    """Hands the message to a relay over stdlib SMTP.

    `sent` means the relay accepted it. Not that it was delivered, not that it
    reached an inbox, and certainly not that anyone read it - SMTP offers no
    way to know any of those, and inventing a status that implied otherwise
    would be the same dishonesty as a fake success.
    """

    name = "smtp"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def available(self) -> tuple[bool, str | None]:
        settings = self._settings
        if not settings.smtp_host.strip():
            return False, "SMTP_HOST is not configured."
        if settings.smtp_port <= 0:
            return False, "SMTP_PORT is not configured."
        if not settings.email_from.strip():
            return False, "EMAIL_FROM is not configured."
        return True, None

    async def send(self, message: EmailMessage) -> SendResult:
        ready, reason = self.available()
        if not ready:
            raise EmailSendError("EMAIL_PROVIDER_UNAVAILABLE", reason or "SMTP is not configured.")

        mime = build_mime(message, self._settings)
        timeout = self._settings.smtp_timeout_seconds

        try:
            # Blocking stdlib call, moved off the event loop. The outer timeout
            # bounds the whole attempt rather than one socket read - a relay can
            # accept the connection and then stall indefinitely.
            await asyncio.wait_for(
                asyncio.to_thread(self._deliver, mime), timeout=timeout + 1.0
            )
        except TimeoutError as error:
            # Deliberately its own code. A timeout is *ambiguous*: the relay may
            # have accepted the message before going quiet, so the caller must
            # not treat this as "definitely not sent".
            raise EmailSendError(
                "EMAIL_SEND_TIMEOUT",
                f"The mail server did not respond within {timeout:g}s.",
            ) from error
        except smtplib.SMTPAuthenticationError as error:
            raise EmailSendError(
                "EMAIL_SEND_FAILED", "The mail server rejected the credentials."
            ) from error
        except smtplib.SMTPRecipientsRefused as error:
            raise EmailSendError(
                "EMAIL_SEND_FAILED", "The mail server refused the recipient address."
            ) from error
        except smtplib.SMTPException as error:
            # The class name, never `str(error)`: a relay's own text can carry a
            # hostname or part of a credential, and this string is stored and
            # served.
            raise EmailSendError(
                "EMAIL_SEND_FAILED",
                f"The mail server refused the message ({type(error).__name__}).",
            ) from error
        except OSError as error:
            raise EmailSendError(
                "EMAIL_SEND_FAILED",
                f"The mail server could not be reached ({type(error).__name__}).",
            ) from error

        logger.info(
            "email accepted by %s for %s",
            self._settings.smtp_host,
            mask_email(message.to),
        )
        return SendResult(
            provider=self.name,
            provider_message_id=str(mime["Message-ID"]),
            accepted_at=datetime.now(UTC),
            delivered_to_provider=True,
        )

    def _deliver(self, mime: MimeMessage) -> None:
        settings = self._settings
        with smtplib.SMTP(
            settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout_seconds
        ) as client:
            if settings.smtp_use_tls:
                client.starttls(context=ssl.create_default_context())
            if settings.smtp_username:
                client.login(settings.smtp_username, settings.smtp_password)
            client.send_message(mime)


def build_sender(settings: Settings) -> ProposalEmailSender:
    """The provider this configuration asks for.

    Note what is absent: there is no "smtp, falling back to console". A
    misconfigured relay produces an unavailable provider and a clear refusal,
    because the alternative is a proposal the operator believes was sent.
    """
    if settings.email_mode is EmailMode.SMTP:
        return SmtpEmailSender(settings)
    return ConsoleEmailSender(settings)


__all__ = [
    "ConsoleEmailSender",
    "EmailAttachment",
    "EmailMessage",
    "EmailSendError",
    "ProposalEmailSender",
    "SendResult",
    "SmtpEmailSender",
    "assert_header_safe",
    "build_mime",
    "build_sender",
]
