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


# ---------------------------------------------------------------------------
# Something has to actually run the migrations
# ---------------------------------------------------------------------------


def test_an_existing_database_is_upgraded_in_place(tmp_path, monkeypatch) -> None:
    """The gap the parity test cannot see.

    Parity proves the ORM and the migrations *describe* the same schema. It
    proves nothing about whether either one is ever applied. `create_all`
    creates missing tables and can never alter an existing one, and the version
    stamp was skipped whenever `alembic_version` was already present - so on a
    container with a persistent volume, a new column landed nowhere and every
    insert failed with `table projects has no column named ...`.

    This builds a database at the *previous* revision and asserts start-up
    brings it to head with the new column present.
    """


    path = tmp_path / "existing.db"
    url = f"sqlite:///{path.as_posix()}"

    # A database as it stood before the newest migration.
    previous = _previous_revision()
    command.upgrade(_alembic_config(url), previous)

    engine = create_engine(url)
    try:
        before = {c["name"] for c in inspect(engine).get_columns("projects")}
    finally:
        engine.dispose()

    # Derived, not named. Hard-coding the column the newest migration happens to
    # add makes this test need an edit every time one lands - and an edit to a
    # test is exactly how a test stops asserting what it was written for. This
    # asks the schema instead, and covers *every* column head adds rather than
    # one chosen by hand.
    expected = _columns_added_by_head(tmp_path) - before
    assert expected, "the fixture is not actually behind head"

    # Start up against it, exactly as the container does.
    _upgrade_through_the_app(path, monkeypatch)

    engine = create_engine(url)
    try:
        after = {c["name"] for c in inspect(engine).get_columns("projects")}
        version = engine.connect().execute(
            __import__("sqlalchemy").text("select version_num from alembic_version")
        ).scalar_one()
    finally:
        engine.dispose()

    missing = expected - after
    assert not missing, f"start-up did not apply the pending migration: {sorted(missing)} absent"
    assert version != previous, "the recorded version is still the old one"


def _columns_added_by_head(tmp_path: Path) -> set[str]:
    """Every `projects` column that exists at head, from a throwaway database."""
    path = tmp_path / "head-reference.db"
    url = f"sqlite:///{path.as_posix()}"
    command.upgrade(_alembic_config(url), "head")
    engine = create_engine(url)
    try:
        return {c["name"] for c in inspect(engine).get_columns("projects")}
    finally:
        engine.dispose()


def _previous_revision() -> str:
    """The revision immediately before head, from the migration scripts."""
    from alembic.script import ScriptDirectory

    script = ScriptDirectory(str(MIGRATIONS_DIR))
    head = script.get_current_head()
    assert head is not None
    down = script.get_revision(head).down_revision
    assert isinstance(down, str), "this test needs at least two migrations"
    return down


def _settings_for(database_url: str):
    from app.core.config import get_settings

    return get_settings().model_copy(update={"database_url": database_url})


def test_a_migration_never_deletes_dependent_rows(tmp_path, monkeypatch) -> None:
    """The trap that cost fifteen proposals.

    SQLite cannot alter a constraint in place, so Alembic's batch mode rebuilds
    the table: create, copy, **drop the old one**, rename. `proposals` and
    `chat_messages` reference `projects` with ON DELETE CASCADE and the
    application enables `PRAGMA foreign_keys` on every connection, so the drop
    cascades and empties them.

    Migrating a database that has real rows in it is the only way to see that.
    A migration run against an empty schema - which is all the parity fixtures
    do - passes happily while destroying every customer's proposal.
    """
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    path = tmp_path / "populated.db"
    url = f"sqlite:///{path.as_posix()}"

    command.upgrade(_alembic_config(url), _previous_revision())

    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(text("PRAGMA foreign_keys=ON"))
            conn.execute(
                text(
                    "insert into projects (id, current_step, analysis_status, created_at, "
                    "updated_at) values ('p1', 'completed', 'complete', "
                    "'2026-01-01', '2026-01-01')"
                )
            )
            conn.execute(
                text(
                    "insert into chat_messages (id, project_id, role, content, created_at) "
                    "values ('m1', 'p1', 'user', 'hello', '2026-01-01')"
                )
            )
            conn.execute(
                text(
                    "insert into proposals (id, project_id, share_token, "
                    "requested_system_size_kwp, feasible_system_size_kwp, requested_panel_count, "
                    "panel_count, annual_production_kwh, annual_savings_eur, original_capex_usd, "
                    "converted_capex_eur, exchange_rate, exchange_rate_date, "
                    "exchange_rate_source, exchange_rate_provider, proposal_data_json, "
                    "created_at) values ('r1', 'p1', 'tok', 6.0, 6.0, 15, 15, 9502.2, 2375.55, "
                    "10000, 8789.70, 0.87897, '2026-07-24', 'fixture', 'ECB', '{}', "
                    "'2026-01-01')"
                )
            )
    finally:
        engine.dispose()

    # Upgraded the way the container does it: through `init_db`, over the
    # application's own engine. That is the path that matters, because
    # `db/session.py` turns `PRAGMA foreign_keys` ON for every application
    # connection - and a bare `alembic upgrade` does not, so running the
    # migration that way hides the cascade entirely.
    _upgrade_through_the_app(path, monkeypatch)

    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            projects = conn.execute(text("select count(*) from projects")).scalar_one()
            proposals = conn.execute(text("select count(*) from proposals")).scalar_one()
            messages = conn.execute(text("select count(*) from chat_messages")).scalar_one()

        # And the constraint still bites afterwards - suspending enforcement
        # for the rebuild must not leave the schema without it. (The pragma is
        # per-connection and off by default in SQLite, so it is set here the
        # same way `db/session.py` sets it for the application.)
        with engine.begin() as conn:
            conn.execute(text("PRAGMA foreign_keys=ON"))
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        "insert into chat_messages (id, project_id, role, content, created_at) "
                        "values ('m2', 'nonexistent', 'user', 'x', '2026-01-01')"
                    )
                )
    finally:
        engine.dispose()

    assert projects == 1, "the table being rebuilt lost its own rows"
    assert proposals == 1, "the rebuild cascade-deleted every proposal"
    assert messages == 1, "the rebuild cascade-deleted every transcript"


def _upgrade_through_the_app(path, monkeypatch) -> None:
    """Run start-up against `path`, exactly as the container does."""
    import asyncio

    import app.db.session as db_session

    monkeypatch.setattr(
        db_session, "get_settings", lambda: _settings_for(f"sqlite+aiosqlite:///{path.as_posix()}")
    )
    db_session._engine = None
    db_session._sessionmaker = None
    try:
        asyncio.run(db_session.init_db())
    finally:
        db_session._engine = None
        db_session._sessionmaker = None
