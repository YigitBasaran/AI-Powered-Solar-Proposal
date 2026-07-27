"""Schema parity between Alembic migrations and the ORM metadata.

The application creates tables with `Base.metadata.create_all` so a fresh
clone runs with no migration step, while Alembic remains the source of truth
for schema evolution. That is only safe if the two cannot diverge — otherwise
a developer adds a column to the model, it appears locally via create_all,
and the migration that production runs never gets written.

This module is the guard `app/db/session.py` refers to. It builds one database
each way and compares the results.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.models.tables import Base

API_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = API_ROOT / "alembic.ini"
MIGRATIONS_DIR = API_ROOT / "migrations"


def _alembic_config(database_url: str) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _describe(database_url: str) -> dict[str, Any]:
    """Reflect a database into a comparable shape.

    Column types are compared by their SQLite affinity string rather than by
    SQLAlchemy type object, because the two construction paths legitimately
    spell some types differently while producing the same storage.
    """
    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        described: dict[str, Any] = {}
        for table in sorted(inspector.get_table_names()):
            if table == "alembic_version":
                continue
            described[table] = {
                "columns": {
                    column["name"]: {
                        "type": str(column["type"]).upper().split("(")[0],
                        "nullable": bool(column["nullable"]),
                    }
                    for column in inspector.get_columns(table)
                },
                "primary_key": sorted(
                    inspector.get_pk_constraint(table).get("constrained_columns") or []
                ),
                "indexes": sorted(
                    (index["name"] or "", tuple(index["column_names"] or ()))
                    for index in inspector.get_indexes(table)
                ),
                "foreign_keys": sorted(
                    (
                        tuple(fk["constrained_columns"]),
                        fk["referred_table"],
                        tuple(fk["referred_columns"]),
                    )
                    for fk in inspector.get_foreign_keys(table)
                ),
            }
        return described
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def from_migrations(tmp_path_factory) -> dict[str, Any]:
    path = tmp_path_factory.mktemp("alembic") / "migrated.db"
    url = f"sqlite:///{path.as_posix()}"
    command.upgrade(_alembic_config(url), "head")
    return _describe(url)


@pytest.fixture(scope="module")
def from_metadata(tmp_path_factory) -> dict[str, Any]:
    path = tmp_path_factory.mktemp("metadata") / "created.db"
    url = f"sqlite:///{path.as_posix()}"
    engine = create_engine(url)
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()
    return _describe(url)


# ---------------------------------------------------------------------------
# Configuration is present at all
# ---------------------------------------------------------------------------


def test_alembic_is_configured() -> None:
    assert ALEMBIC_INI.is_file(), "alembic.ini is missing"
    assert (MIGRATIONS_DIR / "env.py").is_file(), "migrations/env.py is missing"


def test_at_least_one_migration_exists() -> None:
    revisions = list((MIGRATIONS_DIR / "versions").glob("*.py"))
    assert revisions, "no migration revisions found"


def test_migrations_take_their_url_from_settings() -> None:
    """A migration must never run against a different database than the app."""
    ini = ALEMBIC_INI.read_text(encoding="utf-8")
    for line in ini.splitlines():
        stripped = line.strip()
        if stripped.startswith("sqlalchemy.url") and "=" in stripped:
            value = stripped.split("=", 1)[1].strip()
            assert not value, "alembic.ini must not hardcode a database URL"


# ---------------------------------------------------------------------------
# The parity guarantee
# ---------------------------------------------------------------------------


def test_same_tables(from_migrations, from_metadata) -> None:
    assert set(from_migrations) == set(from_metadata)


def test_every_table_has_the_same_columns(from_migrations, from_metadata) -> None:
    for table in sorted(from_metadata):
        migrated = set(from_migrations[table]["columns"])
        declared = set(from_metadata[table]["columns"])
        assert migrated == declared, (
            f"{table}: migration and model disagree on columns; "
            f"only in migration {migrated - declared}, only in model {declared - migrated}"
        )


def test_column_types_and_nullability_match(from_migrations, from_metadata) -> None:
    for table in sorted(from_metadata):
        for name, declared in from_metadata[table]["columns"].items():
            migrated = from_migrations[table]["columns"][name]
            assert migrated["type"] == declared["type"], f"{table}.{name} type"
            assert migrated["nullable"] == declared["nullable"], f"{table}.{name} nullable"


def test_primary_keys_match(from_migrations, from_metadata) -> None:
    for table in sorted(from_metadata):
        assert from_migrations[table]["primary_key"] == from_metadata[table]["primary_key"]


def test_indexes_match(from_migrations, from_metadata) -> None:
    for table in sorted(from_metadata):
        assert from_migrations[table]["indexes"] == from_metadata[table]["indexes"], table


def test_foreign_keys_match(from_migrations, from_metadata) -> None:
    for table in sorted(from_metadata):
        assert from_migrations[table]["foreign_keys"] == from_metadata[table]["foreign_keys"]


# ---------------------------------------------------------------------------
# The tables the brief asks for
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "table", ["projects", "proposals", "exchange_rate_cache", "proposal_views"]
)
def test_required_tables_exist(from_migrations, table: str) -> None:
    assert table in from_migrations


def test_share_token_is_indexed(from_migrations) -> None:
    """Public share lookups hit this column on every proposal view."""
    indexes = from_migrations["proposals"]["indexes"]
    assert any("share_token" in columns for _, columns in indexes)


def test_exchange_rate_cache_has_a_lookup_index(from_migrations) -> None:
    indexes = from_migrations["exchange_rate_cache"]["indexes"]
    assert any(
        {"base_currency", "quote_currency", "provider"} <= set(columns) for _, columns in indexes
    )


# ---------------------------------------------------------------------------
# create_all and Alembic must not fight over the same database
# ---------------------------------------------------------------------------


def test_a_database_built_by_the_app_is_stamped_at_head(tmp_path, monkeypatch) -> None:
    """`create_all` alone leaves `alembic_version` empty.

    An operator then runs `alembic upgrade head`, Alembic sees no revision
    applied, tries to CREATE TABLE over the existing tables and fails. This is
    exactly what happened in the container. `init_db` stamps the version so the
    upgrade is a clean no-op on a database the application built.
    """
    import asyncio

    import app.db.session as session_module
    from app.core.config import get_settings

    database = tmp_path / "stamped.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database.as_posix()}")
    get_settings.cache_clear()
    session_module._engine = None
    session_module._sessionmaker = None

    try:
        asyncio.run(session_module.init_db())

        engine = create_engine(f"sqlite:///{database.as_posix()}")
        try:
            inspector = inspect(engine)
            assert "alembic_version" in inspector.get_table_names()
            with engine.connect() as connection:
                version = connection.exec_driver_sql(
                    "SELECT version_num FROM alembic_version"
                ).scalar_one()
            assert version, "the database must carry a revision"
        finally:
            engine.dispose()

        # The upgrade an operator would run must now do nothing, not explode.
        command.upgrade(_alembic_config(f"sqlite:///{database.as_posix()}"), "head")
    finally:
        session_module._engine = None
        session_module._sessionmaker = None
        get_settings.cache_clear()
