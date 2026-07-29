"""Shared test configuration.

The suite is offline and deterministic, but **not** because the application has
an offline mode. PVGIS is always a real HTTP call; `offline_env` starts a local
replay server and points `PVGIS_BASE_URL` at it, so every analysis test
exercises the same transport, retry and parse code a production call does.

That is the whole reason the stub exists rather than a respx router: the same
mechanism serves pytest, the Playwright launchers and both verification
scripts, and a call count is assertable across a process boundary. Tests that
genuinely need the network are marked ``live`` and deselected by default.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
import respx

from tests.support.pvgis_stub import PvgisStub, start_stub


@pytest.fixture(scope="session")
def pvgis_stub() -> Iterator[tuple[str, PvgisStub]]:
    """The replay server every analysis in this suite talks to."""
    base_url, stub, stop = start_stub()
    try:
        yield base_url, stub
    finally:
        stop()


@pytest.fixture(scope="session")
def offline_env(pvgis_stub) -> Iterator[Path]:
    """Point the app at the replay stub and a temporary database."""
    stub_url, _ = pvgis_stub
    tmpdir = Path(tempfile.mkdtemp(prefix="solarvis-test-"))
    db_path = tmpdir / "test.db"

    keys = (
        "APP_ENV",
        "DATABASE_URL",
        "MAPS_MODE",
        "PVGIS_MODE",
        "PVGIS_BASE_URL",
        "ALLOW_REPLAY_PROPOSALS",
        "FX_MODE",
        "LLM_PROVIDER",
    )
    previous = {key: os.environ.get(key) for key in keys}
    os.environ.update(
        {
            # Named explicitly: several guards are only permitted in a test
            # environment, and they check this rather than trusting a flag.
            "APP_ENV": "test",
            "DATABASE_URL": f"sqlite+aiosqlite:///{db_path.as_posix()}",
            "MAPS_MODE": "fixture",
            # Set explicitly rather than left to the field default, because a
            # developer's own `.env` may say `fixture` - and if it does, the
            # whole suite silently goes back to reading captures off disk while
            # still passing. That happened once; `test_the_suite_reaches_the
            # _stub` below is what makes it impossible to miss again. The
            # setting disappears entirely once fixture mode is deleted.
            "PVGIS_MODE": "live",
            "PVGIS_BASE_URL": f"{stub_url}/api/v5_3",
            # The stub is not the canonical PVGIS origin, so what it produces
            # is labelled `replay` and is not proposal-grade. Permitted here,
            # and only here, because APP_ENV says this is a test environment -
            # the settings themselves refuse it anywhere else.
            "ALLOW_REPLAY_PROPOSALS": "true",
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


@pytest.fixture
def stub_requests(pvgis_stub) -> Iterator[list[dict]]:
    """The PVGIS requests this test caused, cleared before it runs.

    "How many live calls did that make?" is a requirement, not a curiosity: a
    consumption change must make none, a size change with unchanged inputs must
    make none, and a new project must make exactly four.
    """
    _, stub = pvgis_stub
    stub.requests.clear()
    yield stub.requests
    stub.requests.clear()


@contextlib.contextmanager
def mock_ollama() -> Iterator[respx.MockRouter]:
    """A respx router that leaves the PVGIS stub alone.

    respx patches the httpx transport, so a bare `respx.mock` block would also
    intercept traffic to a local stub - silently, and only for tests that happen
    to run an analysis inside the block. Passing 127.0.0.1 through keeps the two
    mechanisms from colliding.
    """
    with respx.mock as router:
        router.route(host="127.0.0.1").pass_through()
        yield router


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
