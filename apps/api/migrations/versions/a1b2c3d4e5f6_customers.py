"""customer records

Adds the `customers` table.

The unique index on `email` is the case-insensitive uniqueness rule, and it
works because the address is normalised - trimmed and lower-cased - before it
is ever stored. See `app/domain/customers.py`. A functional index over
`lower(email)` was the alternative; it behaves differently on SQLite and
PostgreSQL, and it leaves the stored value in whatever case it arrived, so two
rows that collide look different when a human reads them.

Purely additive: no existing table is touched, so nothing here can disturb the
`projects` -> `proposals` cascade.

Revision ID: a1b2c3d4e5f6
Revises: 9b2c4d6e8f10
Create Date: 2026-07-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "9b2c4d6e8f10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("first_name", sa.String(length=120), nullable=False),
        sa.Column("last_name", sa.String(length=120), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("phone", sa.String(length=64), nullable=True),
        sa.Column("company_name", sa.String(length=255), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("customers", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_customers_email"), ["email"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("customers", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_customers_email"))
    op.drop_table("customers")
