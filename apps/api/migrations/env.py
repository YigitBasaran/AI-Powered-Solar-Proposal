"""Alembic environment.

The URL comes from application settings rather than alembic.ini, so a
migration can never run against a different database than the app. The async
driver is swapped for its sync equivalent here — migrations are a
short synchronous operation and do not need the async stack.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import get_settings
from app.models.tables import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def sync_database_url() -> str:
    """The URL to migrate, with any async driver stripped.

    Defaults to application settings, so `alembic upgrade head` with no
    arguments always targets the database the app actually uses. An explicit
    `sqlalchemy.url` still wins, because tests and one-off operational runs
    legitimately need to point at a specific database.
    """
    override = config.get_main_option("sqlalchemy.url", None)
    url = override or get_settings().database_url
    return url.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg")


def run_migrations_offline() -> None:
    context.configure(
        url=sync_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # SQLite cannot ALTER most things in place; batch mode rewrites the
        # table instead, which is what makes migrations usable here at all.
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # The application passes its own connection when stamping a freshly
    # created database; reuse it rather than opening a second one.
    supplied = config.attributes.get("connection")
    if supplied is not None:
        context.configure(
            connection=supplied,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
        return

    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = sync_database_url()

    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
