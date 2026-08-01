"""append-only project activity

Adds the `activity_events` table.

`delivery_id` is deliberately a plain column rather than a foreign key. An
audit row has to outlive what it describes: "an email was sent" stays true
after the delivery record it refers to is gone, and ON DELETE SET NULL would
quietly erase the only link between the two.

The composite `(project_id, occurred_at)` index is what the timeline query
reads; the single-column indexes serve the customer and proposal views.

Purely additive - a new table, no existing one touched.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-07-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "activity_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("customer_id", sa.String(length=36), nullable=True),
        sa.Column("proposal_id", sa.String(length=36), nullable=True),
        sa.Column("delivery_id", sa.String(length=36), nullable=True),
        sa.Column("event_type", sa.String(length=48), nullable=False),
        sa.Column("actor", sa.String(length=32), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["proposal_id"], ["proposals.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("activity_events", schema=None) as batch_op:
        batch_op.create_index(
            "ix_activity_project_time", ["project_id", "occurred_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_activity_events_customer_id"), ["customer_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_activity_events_occurred_at"), ["occurred_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_activity_events_project_id"), ["project_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_activity_events_proposal_id"), ["proposal_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("activity_events", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_activity_events_proposal_id"))
        batch_op.drop_index(batch_op.f("ix_activity_events_project_id"))
        batch_op.drop_index(batch_op.f("ix_activity_events_occurred_at"))
        batch_op.drop_index(batch_op.f("ix_activity_events_customer_id"))
        batch_op.drop_index("ix_activity_project_time")
    op.drop_table("activity_events")
