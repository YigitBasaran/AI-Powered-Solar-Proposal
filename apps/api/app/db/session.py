"""Async database session management."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings
from app.models.tables import Base

logger = logging.getLogger("solarvis.db")

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None

# Concurrent requests are normal - several browser tabs, or a test suite with
# parallel workers, all writing through one SQLite file.
SQLITE_BUSY_TIMEOUT_MS = 5_000


def _apply_sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
    """Make SQLite safe under concurrent writers.

    SQLite's default rollback journal takes an exclusive lock for the whole of
    every write transaction, so a second concurrent writer fails *immediately*
    with "database is locked" rather than waiting. Two pragmas fix that:

    * ``journal_mode=WAL`` lets readers continue during a write, which is the
      common case here (one write, several reads of the same proposal).
    * ``busy_timeout`` makes a blocked writer wait and retry for a bounded
      period instead of failing instantly.

    ``foreign_keys`` is off by default in SQLite, which would silently permit
    orphaned rows; the schema declares the constraints, so enforce them.
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        # NORMAL is the recommended pairing with WAL: durable across process
        # crashes, and only at risk from an OS-level crash mid-write.
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        is_sqlite = settings.database_url.startswith("sqlite")
        _engine = create_async_engine(
            settings.database_url,
            echo=False,
            future=True,
            # SQLite needs this so a single connection can be shared across the
            # async request lifecycle without tripping thread-affinity checks.
            connect_args={"check_same_thread": False} if is_sqlite else {},
        )
        if is_sqlite:
            event.listen(_engine.sync_engine, "connect", _apply_sqlite_pragmas)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            get_engine(), expire_on_commit=False, class_=AsyncSession
        )
    return _sessionmaker


def _stamp_alembic_head(connection: Any) -> None:
    """Record that this database is already at the latest revision.

    Without this, `create_all` and Alembic disagree about a fresh database:
    the tables exist but `alembic_version` is empty, so `alembic upgrade head`
    tries to CREATE TABLE over them and fails. Stamping makes the upgrade a
    no-op on a database the application built, and leaves it working normally
    on one built by migrations.
    """
    from alembic import command
    from alembic.config import Config

    api_root = Path(__file__).resolve().parents[2]
    ini = api_root / "alembic.ini"
    if not ini.is_file():  # pragma: no cover - only if migrations were stripped
        logger.warning("alembic.ini not found; skipping version stamp")
        return

    config = Config(str(ini))
    config.set_main_option("script_location", str(api_root / "migrations"))
    config.attributes["connection"] = connection
    command.stamp(config, "head")


async def init_db() -> None:
    """Create tables if they do not exist, and mark the schema version.

    Alembic remains the source of truth for schema evolution; this exists so a
    fresh clone runs with no migration step. `tests/unit/test_schema_parity.py`
    asserts the two descriptions of the schema cannot drift.
    """
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Stamping needs its own transaction: it opens a second Alembic-managed
    # context over the same connection.
    try:
        async with engine.begin() as conn:
            already = await conn.run_sync(
                lambda sync_conn: sync_conn.dialect.has_table(sync_conn, "alembic_version")
            )
            if not already:
                await conn.run_sync(_stamp_alembic_head)
                logger.info("stamped database at alembic head")
    except Exception:
        # A failure here must not stop the application booting: the schema is
        # already correct, only the version marker is missing.
        logger.warning("could not stamp the alembic version", exc_info=True)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a transactional session.

    The commit here is a **safety net, not the guarantee**. FastAPI runs the
    exit code of a ``yield`` dependency *after the response has been sent*, so
    a client that receives 200 and immediately issues a dependent request can
    beat this commit and read a database that does not yet contain its own
    write. A handler that mutates therefore calls :func:`commit_before_response`
    itself, before returning.

    This is not a test artefact: it is exactly what a user double-clicking, or
    a UI chaining two calls, would hit. It surfaced as an intermittent 409 from
    ``run-analysis`` for a project whose intake had just been accepted.
    """
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def commit_before_response(session: AsyncSession) -> None:
    """Make this request's writes durable before the client is told they are.

    Call at the end of every handler that mutates. See :func:`get_session` for
    why the dependency's own commit is too late.
    """
    await session.commit()


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
