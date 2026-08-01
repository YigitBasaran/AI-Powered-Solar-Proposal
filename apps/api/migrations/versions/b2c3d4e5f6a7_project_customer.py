"""link a project to a customer

Adds `projects.customer_id` (nullable, FK to `customers`, ON DELETE SET NULL)
and `projects.name`.

Nullable on purpose. Every project that exists predates customers, and no
placeholder record is invented for them - a fabricated "Unknown Customer" would
be indistinguishable from a real one the moment it was written. They stay
unlinked, and the UI offers to assign someone.

SET NULL rather than CASCADE: archiving or removing a customer must never take
their projects, and through them their issued proposals, with it.

**Why the raw DDL.** Adding a foreign key to `projects` is the single most
dangerous operation in this migration chain. `batch_alter_table` rebuilds the
table - create, copy, **drop**, rename - and both `proposals` and
`chat_messages` reference it ON DELETE CASCADE, so with `PRAGMA foreign_keys`
on (which the application always sets) the drop empties them. That is the
failure `_foreign_keys_suspended` in `migrations/env.py` and
`test_a_migration_never_deletes_dependent_rows` exist to catch, and it once
cost fifteen proposals.

SQLite supports `ALTER TABLE ... ADD COLUMN ... REFERENCES` natively for a
nullable column defaulting to NULL, which is exactly this column. Alembic's
SQLite dialect will not *render* it - `op.add_column` with an inline
`sa.ForeignKey` raises `NotImplementedError: No support for ALTER of
constraints in SQLite dialect` and points at batch mode - so the statement is
issued directly. No rebuild, no drop, nothing for a cascade to act on.

Other dialects take the ordinary `op.add_column` path.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute(
            "ALTER TABLE projects ADD COLUMN customer_id VARCHAR(36) "
            "REFERENCES customers (id) ON DELETE SET NULL"
        )
    else:
        op.add_column(
            "projects",
            sa.Column(
                "customer_id",
                sa.String(length=36),
                sa.ForeignKey("customers.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )

    op.add_column("projects", sa.Column("name", sa.String(length=160), nullable=True))
    op.create_index(op.f("ix_projects_customer_id"), "projects", ["customer_id"], unique=False)


def downgrade() -> None:
    """The reverse is *not* symmetric, and cannot be portably.

    `ALTER TABLE ... DROP COLUMN` exists from SQLite 3.35, but whether it can
    drop a column named in a foreign-key definition - which `customer_id` is,
    because the upgrade added it with an inline `REFERENCES` - **depends on the
    SQLite version**:

        3.40.1 (the container's Debian build): refuses, with
            `error in table projects after drop column:
             unknown column "customer_id" in foreign key definition`
        3.45.3 (the local CPython build):      succeeds, rewriting the clause

    So the plain `op.drop_column` this originally used passed every local test
    and then stranded the *container* at this revision with no way back. The
    developer machine had the newer, more permissive engine; the deployment
    target did not. Found by running the stack in Docker, and not findable
    without it - which is why `docs/testing.md` now says so.

    So the downgrade rebuilds the table. That is the operation this chain
    otherwise avoids at all costs - it drops `projects`, and `proposals` and
    `chat_messages` reference it ON DELETE CASCADE - and it is survivable here
    only because `_foreign_keys_suspended` in `migrations/env.py` turns
    enforcement off around every migration. `test_a_migration_never_deletes_
    dependent_rows` and `test_every_migration_round_trips` both exercise it
    against a database with real rows in it.

    `name` is dropped inside the same batch, so the table is rebuilt once
    rather than twice.
    """
    op.drop_index(op.f("ix_projects_customer_id"), table_name="projects")
    with op.batch_alter_table("projects", schema=None) as batch_op:
        batch_op.drop_column("name")
        batch_op.drop_column("customer_id")
