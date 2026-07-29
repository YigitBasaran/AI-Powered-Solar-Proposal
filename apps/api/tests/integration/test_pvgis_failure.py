"""What happens when PVGIS cannot answer.

Until this change the answer was: a captured payload was substituted and the
analysis completed, so a customer received a proposal quoting an annual
production figure that had never been observed for their roof. The substitution
was labelled in the snapshot, and nothing downstream read the label.

The answer now is that the analysis fails, says why, and cannot be finalised.
That is a worse experience and a better document.
"""

from __future__ import annotations

import contextlib

import pytest

CASE_COORD = "-34.04658242871865, 18.46491476666948"


def _intake(client) -> str:
    project_id = client.post("/api/v1/projects").json()["projectId"]
    for message in (CASE_COORD, "1,150 kWh", "6 kWp"):
        client.post(f"/api/v1/projects/{project_id}/chat", json={"message": message})
    return project_id


@contextlib.contextmanager
def pvgis_down(pvgis_stub):
    """An app whose PVGIS answers 503 to everything.

    A dependency override on its own app rather than a monkeypatch on the
    settings module: the override cannot leak into the shared `client`, which
    matters for the retry test below, where a healthy call has to follow a
    failed one on the same database.
    """
    from fastapi.testclient import TestClient

    from app.core.config import get_settings
    from app.main import create_app

    stub_url, _ = pvgis_stub
    patched = get_settings().model_copy(
        update={
            "pvgis_base_url": f"{stub_url}/__fault/unavailable/api/v5_3",
            # The retry policy itself is exercised in `test_pvgis.py` against an
            # injected clock. Here it only needs to end quickly.
            "pvgis_max_attempts": 2,
            "pvgis_retry_base_delay_seconds": 0.01,
        }
    )

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: patched
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def down(pvgis_stub):
    with pvgis_down(pvgis_stub) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# The failure itself
# ---------------------------------------------------------------------------


def test_an_outage_fails_the_analysis_with_a_structured_error(down) -> None:
    project_id = _intake(down)

    response = down.post(f"/api/v1/projects/{project_id}/run-analysis")

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "PVGIS_UNAVAILABLE"


def test_no_snapshot_is_stored_and_the_status_says_failed(down) -> None:
    """Not `running`, which reads as "still working" and never resolves.

    `run_analysis` was unguarded before this change; a PVGIS failure left the
    project at `running` for ever, and `validate_ready` treats that as a
    recalculation in flight. The fixture fallback hid it by almost never
    failing.
    """
    project_id = _intake(down)
    down.post(f"/api/v1/projects/{project_id}/run-analysis")

    project = down.get(f"/api/v1/projects/{project_id}").json()

    assert project["analysisStatus"] == "failed"
    assert project["analysis"] is None


def test_the_reason_is_kept_and_shown(down) -> None:
    project_id = _intake(down)
    down.post(f"/api/v1/projects/{project_id}/run-analysis")

    project = down.get(f"/api/v1/projects/{project_id}").json()
    error = project["analysisError"]

    assert error["code"] == "PVGIS_UNAVAILABLE"
    assert error["message"]


def test_a_failed_analysis_cannot_be_finalised(down) -> None:
    project_id = _intake(down)
    down.post(f"/api/v1/projects/{project_id}/run-analysis")

    response = down.post(f"/api/v1/projects/{project_id}/finalize")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PROPOSAL_INCOMPLETE"


def test_a_retry_after_the_outage_clears_succeeds(client, pvgis_stub) -> None:
    """The failure is a state, not a sentence. A later run overwrites it."""
    with pvgis_down(pvgis_stub) as down:
        project_id = _intake(down)
        assert down.post(f"/api/v1/projects/{project_id}/run-analysis").status_code == 502

    # `client` is the same database on a healthy endpoint.
    retried = client.post(f"/api/v1/projects/{project_id}/run-analysis")

    assert retried.status_code == 200
    project = client.get(f"/api/v1/projects/{project_id}").json()
    assert project["analysisStatus"] == "complete"
    assert project["analysisError"] is None


# ---------------------------------------------------------------------------
# One bad facet is not a smaller problem than none
# ---------------------------------------------------------------------------


async def test_one_failed_facet_fails_the_whole_analysis(pvgis_stub, offline_env) -> None:
    """Three of four is not a partial answer; it is a different roof.

    The optimiser ranks the facets against each other, so a missing yield does
    not add noise - it silently changes which planes get panels. There is no
    three-of-four path, deliberately.
    """
    from app.core.config import get_settings
    from app.core.errors import PvgisUnavailableError
    from app.services.analysis import run_analysis

    stub_url, _ = pvgis_stub
    settings = get_settings().model_copy(
        update={
            "pvgis_base_url": f"{stub_url}/__fault/one-facet/-79.38/api/v5_3",
            "pvgis_max_attempts": 2,
            "pvgis_retry_base_delay_seconds": 0.01,
        }
    )

    with pytest.raises(PvgisUnavailableError) as caught:
        await run_analysis(
            monthly_consumption_kwh=1150.0, system_size_kwp=6.0, settings=settings
        )

    assert "facet_e" in str(caught.value.details or caught.value.message)
