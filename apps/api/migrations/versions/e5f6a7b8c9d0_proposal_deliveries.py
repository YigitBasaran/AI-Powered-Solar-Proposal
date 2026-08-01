"""proposal deliveries

Adds the `proposal_deliveries` table.

`idempotency_key` is UNIQUE, and that constraint is the entire duplicate-send
defence. The key is derived from the proposal, the recipient and the revision,
so a double click, a browser refresh mid-send and a client-side retry all
compute the same value - and the database, rather than application timing,
decides that they are one send.

Four statuses only: pending, sending, sent, failed. There is deliberately no
`delivered`, `bounced` or `opened` column or value, because SMTP provides none
of them. A schema that offered those states would outlive whoever added it and
would eventually be filled in with guesses.

Purely additive - a new table, no existing one touched.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-07-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "proposal_deliveries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("proposal_id", sa.String(length=36), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("recipient", sa.String(length=320), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["proposal_id"], ["proposals.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("proposal_deliveries", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_proposal_deliveries_idempotency_key"),
            ["idempotency_key"],
            unique=True,
        )
        batch_op.create_index(
            batch_op.f("ix_proposal_deliveries_proposal_id"), ["proposal_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("proposal_deliveries", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_proposal_deliveries_proposal_id"))
        batch_op.drop_index(batch_op.f("ix_proposal_deliveries_idempotency_key"))
    op.drop_table("proposal_deliveries")
