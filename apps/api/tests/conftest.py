"""Shared test configuration.

Integration tests run entirely in fixture mode against a throwaway database, so
the suite is deterministic, offline and leaves nothing behind. Tests that
genuinely need the network are marked ``live`` and deselected by default.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def offline_env() -> Iterator[Path]:
    """Point the app at fixture modes and a temporary database."""
    tmpdir = Path(tempfile.mkdtemp(prefix="solarvis-test-"))
    db_path = tmpdir / "test.db"

    previous = {
        key: os.environ.get(key)
        for key in ("DATABASE_URL", "MAPS_MODE", "PVGIS_MODE", "FX_MODE", "LLM_PROVIDER")
    }
    os.environ.update(
        {
            "DATABASE_URL": f"sqlite+aiosqlite:///{db_path.as_posix()}",
            "MAPS_MODE": "fixture",
            "PVGIS_MODE": "fixture",
            "FX_MODE": "fixture",
            "LLM_PROVIDER": "rules",
        }
    )

    from app.core.config import get_settings

    get_settings.cache_clear()

    yield db_path

    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def client(offline_env):
    """A TestClient bound to the offline configuration."""
    from fastapi.testclient import TestClient

    import app.db.session as db_session
    from app.main import create_app

    # Force a fresh engine so the temporary database URL takes effect.
    db_session._engine = None
    db_session._sessionmaker = None

    with TestClient(create_app()) as test_client:
        yield test_client

    db_session._engine = None
    db_session._sessionmaker = None
