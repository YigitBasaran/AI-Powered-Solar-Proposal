"""project revisions

Adds `projects.revision_of_project_id` - a nullable self-referencing foreign
key with a UNIQUE constraint.

The uniqueness is the point, not a tidiness measure. Editing a finalised
project forks a revision, and a retried or concurrent delivery of the same
change must not produce two drafts. SQLite (and every SQL engine here) treats
NULLs as distinct under a unique index, so any number of root projects coexist
while a parent may have at most one direct child. The insert race is resolved
by the database rather than by application timing: the loser catches
`IntegrityError` and re-selects the winner's row.

`ondelete="SET NULL"` rather than CASCADE: deleting a parent must not delete a
revision the customer is still working on.

Revision ID: 4a1f7c2b9e30
Revises: 1c779d205bda
Create Date: 2026-07-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4a1f7c2b9e30"
down_revision: str | None = "1c779d205bda"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLite cannot add a constraint to an existing table, so the batch context
    # rebuilds it. Harmless on engines that can.
    with op.batch_alter_table("projects", schema=None) as batch_op:
        batch_op.add_column(sa.Column("revision_of_project_id", sa.String(length=36), nullable=True))
        batch_op.create_unique_constraint(
            "uq_projects_revision_of_project_id", ["revision_of_project_id"]
        )
        batch_op.create_foreign_key(
            "fk_projects_revision_of_project_id",
            "projects",
            ["revision_of_project_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("projects", schema=None) as batch_op:
        batch_op.drop_constraint("fk_projects_revision_of_project_id", type_="foreignkey")
        batch_op.drop_constraint("uq_projects_revision_of_project_id", type_="unique")
        batch_op.drop_column("revision_of_project_id")
