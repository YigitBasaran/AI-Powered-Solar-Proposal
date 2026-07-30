"""per-project electricity tariff

Adds `projects.electricity_tariff_eur_per_kwh`, nullable.

Null means "use the configured case rate", so every existing project keeps the
figures it was quoted. A tariff is a property of the customer rather than of the
deployment - two people looking at the same roof can face different prices, and
the payback each is quoted has to reflect theirs.

Revision ID: 9b2c4d6e8f10
Revises: 7d3e8b1c0a54
Create Date: 2026-07-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9b2c4d6e8f10"
down_revision: str | None = "7d3e8b1c0a54"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Plain ADD COLUMN. SQLite supports adding a nullable column in place, so
    # this avoids the batch rebuild that would drop and recreate `projects` -
    # a table `proposals` and `chat_messages` both reference ON DELETE CASCADE.
    op.add_column(
        "projects", sa.Column("electricity_tariff_eur_per_kwh", sa.Float(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("projects", "electricity_tariff_eur_per_kwh")
