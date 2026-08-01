"""SQLAlchemy 2.x table definitions."""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _uuid_str() -> str:
    return str(uuid.uuid4())


#: The last timestamp handed out, and the lock that keeps it single-valued.
_last_stamp = datetime.min.replace(tzinfo=UTC)
_stamp_lock = threading.Lock()


def _utcnow() -> datetime:
    """Now, but never the same instant twice.

    The system clock is not fine-grained enough to order rows written in quick
    succession - on Windows a whole chat turn can land inside one tick, so four
    messages share a `created_at`. Ordering then falls to the tiebreak, and the
    tiebreak is a random UUID, so "the previous assistant message" is decided by
    a coin flip.

    That is not a cosmetic ordering problem. `_pending_confirmation` reads the
    immediately preceding assistant message to decide what a bare "yes" answers;
    picking the wrong one honours an offer from an earlier turn, and one of the
    offers is "start over". A customer answering a question could have their
    project reset.

    So a timestamp is never re-issued: a colliding read is bumped by a
    microsecond, which keeps insertion order strictly increasing. Within one
    process, which is where it matters - a project's turns are handled one at a
    time, and the `id` tiebreak remains as a last resort across processes.
    """
    global _last_stamp
    with _stamp_lock:
        now = datetime.now(UTC)
        if now <= _last_stamp:
            now = _last_stamp + timedelta(microseconds=1)
        _last_stamp = now
        return now


def as_utc(value: datetime | None) -> datetime | None:
    """The read-side counterpart of :func:`_utcnow`.

    SQLite has no timezone type, so a column written as an aware UTC datetime
    comes back **naive**. The same record therefore serialises two different
    ways: `...+00:00` from the object still in the identity map after a write,
    and `...` with no offset once it has been loaded from disk.

    That is not cosmetic. `Date.parse` in a browser reads an ISO string with no
    offset as *local* time, so a timestamp round-tripped through the database
    renders hours away from the one returned by the request that created it.

    Every value in these columns is written by `_utcnow`, so a naive value read
    back *is* UTC - attaching it states what is already true rather than
    guessing. Same shape as the existing normalisation in
    `integrations/pvgis.py`.
    """
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def iso_utc(value: datetime | None) -> str | None:
    """`as_utc`, serialised. The one way a timestamp leaves this application."""
    normalised = as_utc(value)
    return normalised.isoformat() if normalised else None


class Customer(Base):
    """The person a proposal is for.

    Deliberately thin. This is the smallest record that lets a proposal be
    addressed, found again and attributed - not a CRM contact, which is why
    there is no pipeline stage, no owner, no tags and no free-form notes.
    """

    __tablename__ = "customers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)

    first_name: Mapped[str] = mapped_column(String(120), nullable=False)
    last_name: Mapped[str] = mapped_column(String(120), nullable=False)

    #: The only name shown to the customer, and the only one that ever appears
    #: on the public proposal page. Derived from first + last unless set.
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)

    #: Stored already lower-cased and trimmed, so this unique index *is* the
    #: case-insensitive uniqueness rule. Normalising on write rather than
    #: indexing `lower(email)` keeps SQLite and PostgreSQL identical and keeps
    #: the stored value readable. See `app/domain/customers.py`.
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)

    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Soft delete. A hard delete would strand finalised proposals whose frozen
    #: snapshot names this person, so the record is retired rather than removed.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)

    #: Who this project is for. Nullable, and deliberately so.
    #:
    #: Every existing project predates customers, the chat-first entry point
    #: creates a project before anyone has been named, and a quick estimate for
    #: a walk-in never needs a record at all. Requiring one here would break all
    #: three. The requirement lands where it actually matters - a proposal
    #: cannot be *emailed* to nobody - rather than at the start of the funnel.
    #:
    #: ON DELETE SET NULL rather than CASCADE: removing a customer must never
    #: take their projects, and their issued proposals, with it.
    customer_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("customers.id", ondelete="SET NULL"), index=True, nullable=True
    )

    #: An optional human label, for telling two projects for one customer apart.
    name: Mapped[str | None] = mapped_column(String(160), nullable=True)

    current_step: Mapped[str] = mapped_column(String(32), nullable=False, default="location")

    raw_location_input: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    resolved_longitude: Mapped[float | None] = mapped_column(Float, nullable=True)

    monthly_consumption_kwh: Mapped[float | None] = mapped_column(Float, nullable=True)
    selected_system_size_kwp: Mapped[float | None] = mapped_column(Float, nullable=True)

    #: "pending" | "running" | "complete" | "recalculating" | "stale" | "failed".
    #:
    #: `stale` and `recalculating` exist so a snapshot can be known not to
    #: describe the project's current inputs. Without them a changed value
    #: silently leaves correct-looking figures in place that describe something
    #: else.
    #:
    #: `failed` is different in kind: there is no usable analysis *at all*.
    #: `stale` still means "the previous figures are here"; `failed` means the
    #: first analysis never produced any, so `analysis_json` is null and there
    #: is nothing to show, reuse or finalise.
    analysis_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    analysis_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    #: The customer's own electricity tariff, in EUR per kWh.
    #:
    #: Null means "use the configured case rate". Stored per project rather than
    #: read from settings alone because a tariff is a property of the customer,
    #: not of the deployment - two people looking at the same roof can face
    #: different prices, and the payback they are quoted has to reflect theirs.
    electricity_tariff_eur_per_kwh: Mapped[float | None] = mapped_column(Float, nullable=True)

    #: Why the last analysis failed - a structured `{code, message, details}`.
    #:
    #: Not inside `analysis_json`, because `validate_ready` and the whole read
    #: path key off that column's *presence*. A failure reason stored there
    #: would read as a usable analysis.
    analysis_error_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    #: The claim on this project's analysis: who holds it, and until when.
    #:
    #: Deliberately *not* `updated_at`. Any write to the row bumps that - every
    #: chat turn does - so it would silently extend the lease of an analysis
    #: that had already died, and it carries no identity, so it could not fence
    #: anything. `analysis_run_id` is that identity: every terminal write
    #: carries it in its WHERE clause, so a run whose lease expired while a
    #: newer run took over cannot clobber the fresher result.
    analysis_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    analysis_lease_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    #: The project this one is a revision of.
    #:
    #: UNIQUE, and that is the whole idempotency mechanism. SQLite treats NULLs
    #: as distinct, so any number of root projects coexist, while a parent may
    #: have **at most one** direct child. A retried or concurrent delivery of
    #: the same change therefore cannot fork two drafts - the database refuses
    #: the second insert and the loser re-selects the winner's row. Revisions
    #: form a chain, not a tree.
    revision_of_project_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="SET NULL"), unique=True, nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    messages: Mapped[list[ChatMessage]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="ChatMessage.created_at"
    )
    proposals: Mapped[list[Proposal]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )

    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    step: Mapped[str | None] = mapped_column(String(32), nullable=True)
    parser_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    project: Mapped[Project] = relationship(back_populates="messages")


class Proposal(Base):
    """An immutable snapshot.

    `proposal_data_json` is the whole rendered domain object. The PDF renderer
    and the public share page both read that blob and nothing else, so a
    proposal can never silently change when a market rate moves.
    """

    __tablename__ = "proposals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )

    share_token: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)

    requested_system_size_kwp: Mapped[float] = mapped_column(Float, nullable=False)
    feasible_system_size_kwp: Mapped[float] = mapped_column(Float, nullable=False)
    requested_panel_count: Mapped[int] = mapped_column(Integer, nullable=False)
    panel_count: Mapped[int] = mapped_column(Integer, nullable=False)

    annual_production_kwh: Mapped[float] = mapped_column(Float, nullable=False)
    annual_savings_eur: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)

    original_capex_usd: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    converted_capex_eur: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)

    exchange_rate: Mapped[float] = mapped_column(Numeric(18, 8), nullable=False)
    exchange_rate_date: Mapped[date] = mapped_column(Date, nullable=False)
    exchange_rate_source: Mapped[str] = mapped_column(String(32), nullable=False)
    exchange_rate_provider: Mapped[str] = mapped_column(String(32), nullable=False)

    payback_years: Mapped[float | None] = mapped_column(Float, nullable=True)

    proposal_data_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    layout_snapshot_path: Mapped[str | None] = mapped_column(String(512), nullable=True)

    ai_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    capacity_warning: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Who this document was for, frozen at finalisation.
    #:
    #: A snapshot rather than a join, for the same reason the figures are: a
    #: proposal is a document someone was sent. Editing the customer afterwards
    #: - a corrected surname, a new address - must not restate what was issued.
    #: Null for a project that had no customer, including every legacy row.
    customer_snapshot_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    #: Depth in the project revision chain: 1 for the first proposal, 2 for the
    #: proposal of its revision, and so on.
    #:
    #: Derived at finalisation and then frozen, because it is printed in the
    #: email subject and on the document. Recomputing it on read would let a
    #: reference the customer is holding change under them. Nullable only so
    #: rows that predate the column can exist; they are read as 1.
    revision_number: Mapped[int | None] = mapped_column(Integer, nullable=True)

    #: The human reference, e.g. `SOL-A1B2C3-R2`. For quoting in an email or on
    #: the phone. The share token remains the identifier; this is not unique and
    #: nothing looks anything up by it.
    reference: Mapped[str | None] = mapped_column(String(32), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    project: Mapped[Project] = relationship(back_populates="proposals")
    views: Mapped[list[ProposalView]] = relationship(
        back_populates="proposal", cascade="all, delete-orphan"
    )


class ExchangeRateCache(Base):
    __tablename__ = "exchange_rate_cache"
    __table_args__ = (
        UniqueConstraint(
            "base_currency", "quote_currency", "provider", "rate_date", name="uq_fx_rate"
        ),
        Index("ix_fx_lookup", "base_currency", "quote_currency", "provider", "rate_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)

    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    quote_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)

    rate: Mapped[float] = mapped_column(Numeric(18, 8), nullable=False)
    rate_date: Mapped[date] = mapped_column(Date, nullable=False)

    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    raw_response_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class PvgisCache(Base):
    __tablename__ = "pvgis_cache"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    cache_key: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)

    response_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ProposalDelivery(Base):
    """One attempt to put a proposal in front of its customer.

    **Four statuses, and no more.** `pending`, `sending`, `sent`, `failed`.
    There is no `delivered`, no `bounced` and no `opened`, because SMTP offers
    no way to know any of them - a status that implied otherwise would be the
    same dishonesty as a fake success, written into the schema where it would
    outlive whoever added it. `sent` means, everywhere, *the provider accepted
    the message*.

    `idempotency_key` is UNIQUE and is the whole duplicate-send defence. It is
    derived from the proposal, the recipient and the revision, so a double
    click, a refresh mid-send and a retried request all compute the same value
    and the database - not application timing - decides which one wins.
    """

    __tablename__ = "proposal_deliveries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    proposal_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("proposals.id", ondelete="CASCADE"), index=True
    )

    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="email")

    #: Frozen at request time from the proposal's customer snapshot, not read
    #: live. Editing the customer between requesting and retrying a send must
    #: not silently redirect a message the operator already confirmed.
    recipient: Mapped[str] = mapped_column(String(320), nullable=False)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")

    #: `console` or `smtp`. Travels with the record so nothing downstream has to
    #: re-read configuration to know whether this was a real send - which is
    #: what keeps "recorded locally" distinguishable from "sent".
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    idempotency_key: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    #: A mapped code and a sanitised message - never a provider traceback. A
    #: relay's own text can carry a hostname or part of a credential, and this
    #: column is served to the client.
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class ActivityEvent(Base):
    """Append-only project history. Never updated, never deleted in place.

    This is what makes "what happened to this deal, and when" answerable
    without reconstructing it from six other tables. It is deliberately *not* a
    general event bus: there are no subscribers, nothing is dispatched, and the
    only consumer is the timeline the operator reads.

    Metadata is a strict per-event-type allow-list of scalars - see
    `app/services/activity.py`. An audit trail that quietly accumulates whatever
    a caller passed is how email bodies, provider responses and full recipient
    addresses end up in a table nobody thinks of as personal data.
    """

    __tablename__ = "activity_events"
    __table_args__ = (Index("ix_activity_project_time", "project_id", "occurred_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)

    project_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=True
    )
    customer_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("customers.id", ondelete="SET NULL"), index=True, nullable=True
    )
    proposal_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("proposals.id", ondelete="CASCADE"), index=True, nullable=True
    )

    #: Intentionally *not* a foreign key.
    #:
    #: An audit row has to outlive what it describes. "An email was sent" stays
    #: true after the delivery record it refers to is gone, and a SET NULL would
    #: quietly erase the only link between the two. Nothing joins on this; it is
    #: a breadcrumb for an operator reading the timeline.
    delivery_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    #: "user" | "system" | "customer" - who caused it, not who is logged in
    #: (there is no authentication). `customer` means the person holding the
    #: public link, which is the only actor outside the operator's own session.
    actor: Mapped[str] = mapped_column(String(32), nullable=False, default="system")

    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )


class ProposalView(Base):
    __tablename__ = "proposal_views"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    proposal_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("proposals.id", ondelete="CASCADE"), index=True
    )

    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    referrer: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notified: Mapped[bool] = mapped_column(Boolean, default=False)

    proposal: Mapped[Proposal] = relationship(back_populates="views")
