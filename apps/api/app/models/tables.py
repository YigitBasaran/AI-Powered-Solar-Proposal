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


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)

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
