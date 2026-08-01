"""Project activity: what happened, when, and to whom.

An append-only trail, written alongside the state changes it describes. Three
decisions shape it.

**Metadata is a per-event allow-list of scalars.** Not a free-form dict. An
audit table that accumulates whatever the caller passed is exactly how email
bodies, provider responses, credentials and full recipient addresses end up
inside something nobody thinks of as personal data - and it happens gradually,
one convenient extra key at a time. A key not named for its event type is
dropped and logged; a non-scalar value is rejected outright.

**Two write modes, chosen per event.** Some events *are* part of what happened
and are written in the same transaction as the change: losing "this proposal
was finalised" while keeping the proposal would misrepresent the record.
Others are observations about it, and are best-effort: an audit write must
never break a customer's page view or roll back a send that already succeeded.

**Ordering is by `(occurred_at, id)`.** `_utcnow` never re-issues a timestamp
within a process, so events written in one request stay in the order they
happened rather than being reordered by a tiebreak on a random UUID.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import ActivityEvent, iso_utc

logger = logging.getLogger("solarvis.activity")

# --- event types -----------------------------------------------------------

CUSTOMER_CREATED = "customer.created"
CUSTOMER_UPDATED = "customer.updated"
PROJECT_CREATED = "project.created"
PROJECT_CUSTOMER_ASSIGNED = "project.customer_assigned"
PROJECT_REVISED = "project.revised"
ANALYSIS_COMPLETED = "analysis.completed"
ANALYSIS_FAILED = "analysis.failed"
PROPOSAL_FINALISED = "proposal.finalised"
PROPOSAL_SEND_REQUESTED = "proposal.send_requested"
PROPOSAL_EMAIL_SENT = "proposal.email_sent"
PROPOSAL_EMAIL_FAILED = "proposal.email_failed"
PROPOSAL_VIEWED = "proposal.viewed"
PROPOSAL_PDF_DOWNLOADED = "proposal.pdf_downloaded"

#: The complete set of keys each event type may carry.
#:
#: Every one of these is a scalar an operator can read at a glance. Note what
#: is *absent* and will stay absent: message bodies, subjects, provider
#: responses, credentials, raw IP addresses, and any unmasked email address.
EVENT_METADATA: dict[str, frozenset[str]] = {
    CUSTOMER_CREATED: frozenset({"displayName"}),
    CUSTOMER_UPDATED: frozenset({"changedFields"}),
    # `projectName` and `customerName` both, so a timeline entry says *which*
    # project started rather than only that one did. On a customer with four
    # projects, "Project started" alone identifies nothing.
    PROJECT_CREATED: frozenset({"projectName", "customerName"}),
    PROJECT_CUSTOMER_ASSIGNED: frozenset({"displayName", "forkedRevision"}),
    PROJECT_REVISED: frozenset({"revisionNumber", "reason"}),
    ANALYSIS_COMPLETED: frozenset({"systemSizeKwp", "annualProductionKwh", "panelCount"}),
    ANALYSIS_FAILED: frozenset({"errorCode"}),
    PROPOSAL_FINALISED: frozenset(
        {"revisionNumber", "reference", "systemSizeKwp", "annualProductionKwh"}
    ),
    PROPOSAL_SEND_REQUESTED: frozenset({"recipientMasked", "revisionNumber"}),
    PROPOSAL_EMAIL_SENT: frozenset({"recipientMasked", "provider", "revisionNumber"}),
    PROPOSAL_EMAIL_FAILED: frozenset({"recipientMasked", "provider", "errorCode"}),
    PROPOSAL_VIEWED: frozenset({"viewCount"}),
    PROPOSAL_PDF_DOWNLOADED: frozenset(),
}

ACTORS = frozenset({"user", "system", "customer"})

DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500

_SCALARS = (str, int, float, bool)


def sanitise(event_type: str, metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    """Reduce `metadata` to the keys this event type is allowed to carry.

    Dropped keys are logged rather than raising: a mislabelled audit field must
    not fail the operation it was describing. What it must not do is get stored.
    """
    allowed = EVENT_METADATA.get(event_type)
    if allowed is None:
        raise ValueError(f"{event_type} is not a known activity event type")
    if not metadata:
        return None

    kept: dict[str, Any] = {}
    for key, value in metadata.items():
        if key not in allowed:
            logger.warning("activity: dropped %r from %s (not in the allow-list)", key, event_type)
            continue
        if value is None:
            continue
        if not isinstance(value, _SCALARS):
            # A nested structure is how a whole provider response, or a whole
            # customer record, arrives somewhere it was never meant to be.
            logger.warning(
                "activity: dropped %r from %s (%s is not a scalar)",
                key,
                event_type,
                type(value).__name__,
            )
            continue
        kept[key] = value

    return kept or None


async def record(
    session: AsyncSession,
    *,
    event_type: str,
    actor: str = "system",
    project_id: str | None = None,
    customer_id: str | None = None,
    proposal_id: str | None = None,
    delivery_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> ActivityEvent:
    """Write one event. Raises - use for events that are part of the change.

    Not committed here: the caller's transaction decides, which is what makes
    "atomic with the state change" true rather than merely intended.
    """
    if actor not in ACTORS:
        raise ValueError(f"{actor!r} is not a known actor")

    event = ActivityEvent(
        event_type=event_type,
        actor=actor,
        project_id=project_id,
        customer_id=customer_id,
        proposal_id=proposal_id,
        delivery_id=delivery_id,
        metadata_json=sanitise(event_type, metadata),
    )
    session.add(event)
    await session.flush()
    return event


async def record_best_effort(session: AsyncSession, **kwargs: Any) -> ActivityEvent | None:
    """Write one event, swallowing any failure.

    For observations *about* a change rather than part of it. A failed audit
    write must never break a customer's page view, and must never roll back a
    send that the provider has already accepted - at that point the email
    exists whatever this table says.
    """
    try:
        return await record(session, **kwargs)
    except Exception:
        logger.exception("activity: failed to record %s", kwargs.get("event_type"))
        return None


async def list_for_customer(
    session: AsyncSession, customer_id: str, *, limit: int = 20
) -> list[ActivityEvent]:
    """This customer's recent events, newest first.

    Scoped by `customer_id` rather than by their projects, so events that belong
    to the person rather than to a project - `customer.created`,
    `customer.updated` - appear too.
    """
    size = max(1, min(int(limit), MAX_PAGE_SIZE))
    rows = (
        await session.execute(
            select(ActivityEvent)
            .where(ActivityEvent.customer_id == customer_id)
            .order_by(ActivityEvent.occurred_at.desc(), ActivityEvent.id.desc())
            .limit(size)
        )
    ).scalars()
    return list(rows)


def serialise(event: ActivityEvent) -> dict[str, Any]:
    return {
        "eventId": event.id,
        "eventType": event.event_type,
        "actor": event.actor,
        "projectId": event.project_id,
        "customerId": event.customer_id,
        "proposalId": event.proposal_id,
        "deliveryId": event.delivery_id,
        "metadata": event.metadata_json or {},
        "occurredAt": iso_utc(event.occurred_at),
    }


def _stored_form(value: datetime) -> datetime:
    """The naive-UTC form SQLite compares against. See `services/customers.py`."""
    return value.replace(tzinfo=None) if value.tzinfo is not None else value


def _decode_cursor(cursor: str) -> tuple[datetime, str] | None:
    occurred, _, identifier = cursor.partition("|")
    if not occurred or not identifier:
        logger.info("ignoring malformed activity cursor")
        return None
    try:
        return _stored_form(datetime.fromisoformat(occurred)), identifier
    except ValueError:
        logger.info("ignoring unparseable activity cursor timestamp")
        return None


async def list_for_project(
    session: AsyncSession,
    project_ids: list[str],
    *,
    limit: int = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
) -> tuple[list[ActivityEvent], str | None]:
    """Newest first, across every project in a revision lineage.

    Takes a list rather than one id because a revision is a *different project*
    row: a timeline showing only the current one would begin halfway through the
    story, with the original analysis and the first proposal missing.
    """
    if not project_ids:
        return [], None

    size = max(1, min(int(limit), MAX_PAGE_SIZE))
    statement = select(ActivityEvent).where(ActivityEvent.project_id.in_(project_ids))

    decoded = _decode_cursor(cursor) if cursor else None
    if decoded is not None:
        when, identifier = decoded
        statement = statement.where(
            (ActivityEvent.occurred_at < when)
            | ((ActivityEvent.occurred_at == when) & (ActivityEvent.id < identifier))
        )

    statement = statement.order_by(
        ActivityEvent.occurred_at.desc(), ActivityEvent.id.desc()
    ).limit(size + 1)

    rows = list((await session.execute(statement)).scalars().all())
    next_cursor = None
    if len(rows) > size:
        last = rows[size - 1]
        next_cursor = f"{_stored_form(last.occurred_at).isoformat()}|{last.id}"

    return rows[:size], next_cursor


__all__ = [
    "ANALYSIS_COMPLETED",
    "ANALYSIS_FAILED",
    "CUSTOMER_CREATED",
    "CUSTOMER_UPDATED",
    "EVENT_METADATA",
    "PROJECT_CREATED",
    "PROJECT_CUSTOMER_ASSIGNED",
    "PROJECT_REVISED",
    "PROPOSAL_EMAIL_FAILED",
    "PROPOSAL_EMAIL_SENT",
    "PROPOSAL_FINALISED",
    "PROPOSAL_PDF_DOWNLOADED",
    "PROPOSAL_SEND_REQUESTED",
    "PROPOSAL_VIEWED",
    "list_for_customer",
    "list_for_project",
    "record",
    "record_best_effort",
    "sanitise",
    "serialise",
]
