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
        "GOOGLE_STATIC_MAPS_BASE_URL",
        "GOOGLE_MAPS_API_KEY",
        "ROOF_CALIBRATION_PATH",
        "PVGIS_BASE_URL",
        "ALLOW_REPLAY_PROPOSALS",
        "FX_MODE",
        "LLM_PROVIDER",
        "EMAIL_MODE",
        "SALESPERSON_EMAIL",
    )
    previous = {key: os.environ.get(key) for key in keys}
    os.environ.update(
        {
            # Named explicitly: several guards are only permitted in a test
            # environment, and they check this rather than trusting a flag.
            "APP_ENV": "test",
            "DATABASE_URL": f"sqlite+aiosqlite:///{db_path.as_posix()}",
            # Imagery has no fixture mode either: the app always makes a real
            # HTTP request, and the suite stays offline by answering it locally.
            # Without this a developer's own key in `.env` would send the whole
            # suite to Google - slowly, and at their expense.
            "GOOGLE_STATIC_MAPS_BASE_URL": f"{stub_url}/maps/api/staticmap",
            "GOOGLE_MAPS_API_KEY": "",
            # The one seam. There is no PVGIS mode any more: the application
            # always makes a real HTTP call, and this points it at a local
            # replay server rather than at Ispra. Which is why
            # `test_pvgis_is_a_real_call.py` watches the transport - the
            # figures are identical either way, so nothing else would notice
            # if the call stopped happening.
            "PVGIS_BASE_URL": f"{stub_url}/api/v5_3",
            # The stub is not the canonical PVGIS origin, so what it produces
            # is labelled `replay` and is not proposal-grade. Permitted here,
            # and only here, because APP_ENV says this is a test environment -
            # the settings themselves refuse it anywhere else.
            "ALLOW_REPLAY_PROPOSALS": "true",
            "FX_MODE": "fixture",
            "LLM_PROVIDER": "rules",
            # Pinned like every other provider above, and for a sharper
            # reason. A developer with working SMTP credentials in `.env` -
            # which is exactly what testing the live path requires - would
            # otherwise have those inherited here, and `Settings` refuses to
            # construct at all under `APP_ENV=test`. The whole suite fails to
            # collect, which is a confusing way to discover a guard that is
            # working correctly. Console mode is also what the console-outbox
            # assertions read.
            "EMAIL_MODE": "console",
            # Empty by default so the open-notification tests opt in explicitly
            # rather than inheriting a real salesperson address.
            "SALESPERSON_EMAIL": "",
        }
    )

    from app.core.config import get_settings

    get_settings.cache_clear()

    # A calibration bound to the stub's raster.
    #
    # The committed profile is bound to Google's imagery, which is deliberately
    # not in this repository, so the suite would otherwise be unable to satisfy
    # the verification it is meant to be testing. Writing a profile for the
    # synthetic raster keeps the *real* guard in the path - there is no bypass
    # flag, because a bypass flag is what eventually ships enabled.
    os.environ["ROOF_CALIBRATION_PATH"] = str(_test_calibration(tmpdir, stub_url))
    get_settings.cache_clear()

    yield db_path

    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    get_settings.cache_clear()


def _test_calibration(tmpdir: Path, stub_url: str) -> Path:
    """The committed roof geometry, re-bound to the stub's synthetic raster."""
    import json
    import urllib.request

    from app.core.config import get_settings
    from app.domain.imagery import (
        describe_request,
        imagery_sha256,
        perceptual_hash,
        request_signature,
    )
    from app.services.roof import CALIBRATION_PATH

    cfg = get_settings().satellite_image_config
    params = (
        f"?center={cfg.center_latitude},{cfg.center_longitude}"
        f"&zoom={cfg.zoom}&size={cfg.requested_width_px}x{cfg.requested_height_px}"
        f"&scale={cfg.scale}&maptype={cfg.map_type}"
    )
    with urllib.request.urlopen(f"{stub_url}/maps/api/staticmap{params}", timeout=30) as raw:
        raster = raw.read()

    data = json.loads(CALIBRATION_PATH.read_text(encoding="utf-8"))
    data["calibration_metadata"] = {
        **data.get("calibration_metadata", {}),
        "traced_against": "the test stub's synthetic raster",
        "request_signature": request_signature(cfg),
        "request": describe_request(cfg),
        "imagery": {
            "perceptual_hash": perceptual_hash(raster),
            "sha256": imagery_sha256(raster),
        },
    }
    path = tmpdir / "test_roof_calibration.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


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
