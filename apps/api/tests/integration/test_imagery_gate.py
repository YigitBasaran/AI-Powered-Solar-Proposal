"""What happens when the imagery is not the imagery the roof was traced on.

The rule, and it is deliberately asymmetric: the **map still renders**, because
a customer looking at their own roof is not harmed by looking at it. Everything
*measured* from it stops, because a misplaced outline produces an area, a panel
count and a payback that all look exactly as confident as correct ones.

That asymmetry is the whole point, so both halves are asserted here.
"""

from __future__ import annotations

import json

import pytest

from app.core.errors import RoofCalibrationUnverifiedError

CASE_COORD = "-34.04658242871865, 18.46491476666948"


@pytest.fixture
def stale_calibration(tmp_path, monkeypatch, offline_env):
    """A calibration bound to imagery that is not what the stub serves.

    Simulates the real failure: Google re-flies the tile, the request
    configuration is still word-for-word correct, and the vertices now describe
    a roof that has moved.
    """
    from app.core.config import get_settings
    from app.services import imagery
    from app.services.roof import calibration_path_for

    data = json.loads(calibration_path_for().read_text(encoding="utf-8"))
    data["calibration_metadata"]["imagery"]["perceptual_hash"] = "0" * 16

    path = tmp_path / "stale_calibration.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    monkeypatch.setenv("ROOF_CALIBRATION_PATH", str(path))
    get_settings.cache_clear()
    imagery.reset_cache()
    yield path
    get_settings.cache_clear()
    imagery.reset_cache()


# ---------------------------------------------------------------------------
# The map still renders
# ---------------------------------------------------------------------------


def test_the_map_is_still_served_when_the_imagery_is_unverified(client, stale_calibration) -> None:
    response = client.get("/api/v1/maps/satellite")

    assert response.status_code == 200, "the picture is still worth showing"
    assert response.headers["X-Imagery-Verified"] == "false"


def test_the_config_still_publishes_the_raster_contract(client, stale_calibration) -> None:
    """The overlay must still be placeable, even while it is not trustworthy."""
    config = client.get("/api/v1/maps/config").json()

    assert config["sourceWidthPx"] == 1280
    assert config["groundMetresPerSourcePixel"] == pytest.approx(0.06185, abs=1e-5)


# ---------------------------------------------------------------------------
# Measurement stops
# ---------------------------------------------------------------------------


async def test_measurement_is_refused(stale_calibration) -> None:
    from app.services.imagery import require_calibrated_imagery

    with pytest.raises(RoofCalibrationUnverifiedError) as caught:
        await require_calibrated_imagery()

    assert caught.value.code == "ROOF_CALIBRATION_UNVERIFIED"
    assert caught.value.details["recalibrationRequired"] is True
    assert caught.value.details["hammingDistance"] > 8


def test_an_analysis_is_refused_with_a_recalibration_message(client, stale_calibration) -> None:
    project_id = client.post("/api/v1/projects").json()["projectId"]
    for message in (CASE_COORD, "1,150 kWh", "6 kWp"):
        client.post(f"/api/v1/projects/{project_id}/chat", json={"message": message})

    response = client.post(f"/api/v1/projects/{project_id}/run-analysis")

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "ROOF_CALIBRATION_UNVERIFIED"
    assert "re-trac" in error["message"].lower()


def test_no_snapshot_is_stored_so_nothing_can_be_finalised(client, stale_calibration) -> None:
    project_id = client.post("/api/v1/projects").json()["projectId"]
    for message in (CASE_COORD, "1,150 kWh", "6 kWp"):
        client.post(f"/api/v1/projects/{project_id}/chat", json={"message": message})
    client.post(f"/api/v1/projects/{project_id}/run-analysis")

    project = client.get(f"/api/v1/projects/{project_id}").json()
    assert project["analysis"] is None

    refused = client.post(f"/api/v1/projects/{project_id}/finalize")
    assert refused.status_code == 409


# ---------------------------------------------------------------------------
# And the healthy path still works, so the guard is not vacuous
# ---------------------------------------------------------------------------


def test_a_matching_calibration_permits_the_analysis(client) -> None:
    project_id = client.post("/api/v1/projects").json()["projectId"]
    for message in (CASE_COORD, "1,150 kWh", "6 kWp"):
        client.post(f"/api/v1/projects/{project_id}/chat", json={"message": message})

    assert client.post(f"/api/v1/projects/{project_id}/run-analysis").status_code == 200
