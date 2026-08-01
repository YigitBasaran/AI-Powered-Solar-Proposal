"""freeze the customer and the revision number on a proposal

Adds `proposals.customer_snapshot_json`, `proposals.revision_number` and
`proposals.reference`.

All three are nullable, and `revision_number` deliberately stays nullable
rather than being backfilled to a NOT NULL column. SQLite cannot add a NOT NULL
constraint after the fact without a table rebuild, and rebuilding `proposals`
is not worth it for a default the reader can apply: a row written before this
column existed is revision 1, and `public_payload` reads it as 1.

Nothing is backfilled into `customer_snapshot_json`. Existing proposals were
issued to nobody the system knows about, and inventing a placeholder customer
for them would put a fabricated record inside a document whose entire purpose
is to be exactly what was sent.

Purely additive columns on an existing table - plain `op.add_column`, no batch
rebuild, so the `projects` -> `proposals` cascade is never exposed.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-07-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("proposals", sa.Column("customer_snapshot_json", sa.JSON(), nullable=True))
    op.add_column("proposals", sa.Column("revision_number", sa.Integer(), nullable=True))
    op.add_column("proposals", sa.Column("reference", sa.String(length=32), nullable=True))

    # Existing proposals are, by definition, the first for their project chain.
    op.execute("UPDATE proposals SET revision_number = 1 WHERE revision_number IS NULL")


def downgrade() -> None:
    op.drop_column("proposals", "reference")
    op.drop_column("proposals", "revision_number")
    op.drop_column("proposals", "customer_snapshot_json")
