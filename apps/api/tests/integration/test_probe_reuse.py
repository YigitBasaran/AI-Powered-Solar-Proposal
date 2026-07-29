"""When a stored observation may answer a new question.

The probes are taken at 1 kWp, before any system size is chosen, so the same
four observations answer every size. That makes a size change cost no PVGIS
traffic at all - which is only sound if "nothing that was sent has changed" is
checked rather than assumed.

Every assertion here is a **call count against the replay stub**. That is the
only way to tell reuse from a refetch: the numbers come out identical either
way, which is exactly why this needs its own tests.
"""

from __future__ import annotations

import pytest

CASE_COORD = "-34.04658242871865, 18.46491476666948"


def _say(client, project_id: str, message: str) -> dict:
    response = client.post(f"/api/v1/projects/{project_id}/chat", json={"message": message})
    assert response.status_code == 200, response.text
    return response.json()


def _analysed(client, size: str = "6 kWp") -> str:
    project_id = client.post("/api/v1/projects").json()["projectId"]
    for message in (CASE_COORD, "1,150 kWh", size):
        _say(client, project_id, message)
    assert client.post(f"/api/v1/projects/{project_id}/run-analysis").status_code == 200
    return project_id


# ---------------------------------------------------------------------------
# The counts
# ---------------------------------------------------------------------------


def test_a_new_project_probes_every_facet_exactly_once(client, stub_requests) -> None:
    _analysed(client)

    assert len(stub_requests) == 4, "one 1 kWp probe per roof facet, and no more"
    assert {round(float(r["aspect"])) for r in stub_requests} == {-169, -79, 11, 101}
    assert {float(r["peakpower"]) for r in stub_requests} == {1.0}


def test_a_consumption_change_makes_no_request_at_all(client, stub_requests) -> None:
    """Consumption moves coverage and savings. It cannot move the sun."""
    project_id = _analysed(client)
    stub_requests.clear()

    _say(client, project_id, "actually make it 400 kWh a month")

    assert stub_requests == []


def test_a_size_change_reuses_the_stored_observation(client, stub_requests) -> None:
    """The whole point of probing at 1 kWp before a size is chosen."""
    project_id = _analysed(client, "6 kWp")
    stub_requests.clear()

    _say(client, project_id, "change it to the largest option")

    assert stub_requests == [], "a size change re-asked PVGIS questions it already had answers to"

    project = client.get(f"/api/v1/projects/{project_id}").json()
    assert project["selectedSystemSizeKwp"] == 9.6
    assert project["analysis"]["layout"]["placedPanelCount"] == 24


def test_the_reused_observation_is_carried_across_verbatim(client) -> None:
    project_id = _analysed(client, "6 kWp")
    before = client.get(f"/api/v1/projects/{project_id}").json()["analysis"]["energy"]["pvgis"]

    _say(client, project_id, "change it to the largest option")
    after = client.get(f"/api/v1/projects/{project_id}").json()["analysis"]["energy"]["pvgis"]

    # Including the timestamps: this *is* the earlier observation, not a new
    # one that happens to agree.
    assert after == before


def test_a_reused_size_change_still_matches_a_fresh_analysis(client) -> None:
    """Reuse is a shortcut, not a different answer."""
    from app.services.conversation.invalidation import differing_paths

    edited = _analysed(client, "6 kWp")
    _say(client, edited, "change it to the largest option")
    fresh = _analysed(client, "9.6 kWp")

    a = client.get(f"/api/v1/projects/{edited}").json()["analysis"]
    b = client.get(f"/api/v1/projects/{fresh}").json()["analysis"]
    assert differing_paths(a, b) == set()


# ---------------------------------------------------------------------------
# When reuse is refused
# ---------------------------------------------------------------------------


def _snapshot(client) -> dict:
    project_id = _analysed(client)
    return client.get(f"/api/v1/projects/{project_id}").json()["analysis"]


@pytest.mark.parametrize(
    ("override", "why"),
    [
        ({"pvgis_system_loss_percent": 10.0}, "a different loss assumption"),
        ({"pvgis_technology": "CIS"}, "a different module technology"),
        ({"pvgis_mounting_place": "free"}, "a different mounting"),
        ({"case_resolved_latitude": -34.05}, "a different place"),
        ({"pvgis_base_url": "https://re.jrc.ec.europa.eu/api/v5_4"}, "a different API version"),
    ],
)
def test_a_changed_input_refuses_reuse(client, override, why) -> None:
    """Each of these makes the stored figures answers to a different question."""
    from app.core.config import get_settings
    from app.services.analysis import reusable_probes
    from app.services.roof import build_roof_model

    snapshot = _snapshot(client)
    settings = get_settings().model_copy(update=override)
    roof = build_roof_model(get_settings())

    assert reusable_probes(snapshot, roof, settings) is None, why


def test_unchanged_inputs_permit_reuse(client) -> None:
    from app.core.config import get_settings
    from app.services.analysis import reusable_probes
    from app.services.roof import build_roof_model

    snapshot = _snapshot(client)
    settings = get_settings()

    probes = reusable_probes(snapshot, build_roof_model(settings), settings)
    assert probes is not None
    assert set(probes.probes) == {"facet_n", "facet_s", "facet_e", "facet_w"}


def test_a_snapshot_without_provenance_is_never_reusable(client) -> None:
    """Fixture-era snapshots carry no `energy.pvgis`. Absent is not trusted."""
    from app.core.config import get_settings
    from app.services.analysis import reusable_probes
    from app.services.roof import build_roof_model

    snapshot = _snapshot(client)
    snapshot["energy"].pop("pvgis")
    settings = get_settings()

    assert reusable_probes(snapshot, build_roof_model(settings), settings) is None


def test_a_replay_observation_is_not_reusable_in_production(client) -> None:
    """The trust rule gates reuse, not only finalisation.

    Refetching does not launder replay into live: with the override off the
    stored probes are refused, a fresh batch is made, and because the endpoint
    is still the stub that batch is *also* replay. Nothing about asking again
    changes where the answer came from.
    """
    from app.core.config import get_settings
    from app.services.analysis import reusable_probes
    from app.services.roof import build_roof_model

    snapshot = _snapshot(client)
    assert snapshot["energy"]["pvgis"]["source"] == "replay"

    settings = get_settings()
    roof = build_roof_model(settings)

    # The suite permits replay, so reuse is allowed here.
    assert settings.allow_replay_proposals is True
    assert reusable_probes(snapshot, roof, settings) is not None

    # Production does not permit it, and then the stored probes are refused.
    production_like = settings.model_copy(update={"allow_replay_proposals": False})
    assert reusable_probes(snapshot, roof, production_like) is None
