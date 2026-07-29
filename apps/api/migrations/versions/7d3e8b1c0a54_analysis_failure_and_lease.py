"""analysis failure reason and lease

Adds three nullable columns to `projects`:

* `analysis_error_json`  - why the last analysis failed
* `analysis_run_id`      - the fencing token identifying the run that holds the claim
* `analysis_lease_until` - when that claim expires

The failure reason cannot live inside `analysis_json`: `validate_ready` and the
whole read path key off that column's *presence*, so a failure stored there
would read as a usable analysis.

The lease cannot ride on `updated_at`: every chat turn bumps it, which would
silently extend the claim of an analysis that had already died, and it carries
no identity, so it could not fence a stale write.

All three are nullable with no default, so existing rows are untouched and the
migration is a metadata-only change.

Revision ID: 7d3e8b1c0a54
Revises: 4a1f7c2b9e30
Create Date: 2026-07-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7d3e8b1c0a54"
down_revision: str | None = "4a1f7c2b9e30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Plain ADD COLUMN, not a batch rebuild. SQLite supports adding a nullable
    # column in place, and that matters here: a batch rebuild drops and
    # recreates `projects`, and `proposals` and `chat_messages` both reference
    # it ON DELETE CASCADE. `migrations/env.py` suspends foreign keys for the
    # whole run so that is survivable, but not needing the rebuild at all is
    # better than relying on the pragma.
    op.add_column("projects", sa.Column("analysis_error_json", sa.JSON(), nullable=True))
    op.add_column("projects", sa.Column("analysis_run_id", sa.String(length=36), nullable=True))
    op.add_column(
        "projects", sa.Column("analysis_lease_until", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("projects", "analysis_lease_until")
    op.drop_column("projects", "analysis_run_id")
    op.drop_column("projects", "analysis_error_json")
