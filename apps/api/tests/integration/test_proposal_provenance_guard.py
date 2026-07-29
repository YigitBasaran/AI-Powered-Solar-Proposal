"""A proposal states a live observation, or it is not issued.

Labelling the source is not enough. A snapshot that says `"source": "replay"`
still renders as a document full of confident annual-production figures, and the
label is read by nobody who matters. So the guard is at finalisation: the
document is refused rather than annotated.

The escape hatch exists only because the stub-backed suites have to exercise
finalisation end to end, and the settings refuse it outside a test environment -
see `test_endpoint_trust.py`. Both directions are asserted here, because a guard
that is only ever tested in its refusing direction can be vacuous.
"""

from __future__ import annotations

import pytest

CASE_COORD = "-34.04658242871865, 18.46491476666948"


def _analysed(client) -> str:
    project_id = client.post("/api/v1/projects").json()["projectId"]
    for message in (CASE_COORD, "1,150 kWh", "6 kWp"):
        client.post(f"/api/v1/projects/{project_id}/chat", json={"message": message})
    assert client.post(f"/api/v1/projects/{project_id}/run-analysis").status_code == 200
    return project_id


def _token(client, project_id: str) -> str:
    from sqlalchemy import select

    from app.models.tables import Proposal

    async def _load():
        from app.db.session import get_sessionmaker

        async with get_sessionmaker()() as session:
            return (
                await session.execute(select(Proposal).where(Proposal.project_id == project_id))
            ).scalar_one().share_token

    import asyncio

    return asyncio.run(_load())


def _project(client, project_id: str):
    from sqlalchemy import select

    from app.models.tables import Project

    async def _load():
        from app.db.session import get_sessionmaker

        async with get_sessionmaker()() as session:
            return (
                await session.execute(select(Project).where(Project.id == project_id))
            ).scalar_one()

    import asyncio

    return asyncio.run(_load())


# ---------------------------------------------------------------------------
# Both directions
# ---------------------------------------------------------------------------


def test_a_replay_backed_analysis_is_refused_by_default(client) -> None:
    from app.core.config import get_settings
    from app.core.errors import ProposalIncompleteError
    from app.services.proposal import validate_ready

    project_id = _analysed(client)
    project = _project(client, project_id)
    assert project.analysis_json["energy"]["pvgis"]["source"] == "replay"

    production_like = get_settings().model_copy(update={"allow_replay_proposals": False})

    with pytest.raises(ProposalIncompleteError, match="replayed capture"):
        validate_ready(project, production_like)


def test_the_same_analysis_is_accepted_where_replay_is_permitted(client) -> None:
    """Otherwise the refusal above could be caused by anything at all."""
    from app.core.config import get_settings
    from app.services.proposal import validate_ready

    project_id = _analysed(client)
    project = _project(client, project_id)

    snapshot = validate_ready(project, get_settings())

    assert snapshot["energy"]["pvgis"]["source"] == "replay"
    assert get_settings().allow_replay_proposals is True


def test_the_suite_can_still_finalise(client) -> None:
    """The end-to-end consequence of the override, exercised through the API."""
    project_id = _analysed(client)
    response = client.post(f"/api/v1/projects/{project_id}/finalize")
    assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
# Absent provenance is untrusted provenance
# ---------------------------------------------------------------------------


def test_a_legacy_snapshot_cannot_be_finalised_even_where_replay_is_permitted(client) -> None:
    """A fixture-era snapshot carries no `energy.pvgis` at all.

    That is not an exemption. Its production figures cannot be attributed to
    any retrieval, so there is nothing to trust or distrust - which is exactly
    the state this whole change exists to make impossible to ship.
    """
    from app.core.config import get_settings
    from app.core.errors import ProposalIncompleteError
    from app.services.proposal import validate_ready

    project_id = _analysed(client)
    project = _project(client, project_id)

    legacy = dict(project.analysis_json)
    legacy["energy"] = {k: v for k, v in legacy["energy"].items() if k != "pvgis"}
    project.analysis_json = legacy

    # Permitted, and still refused: the override is about *replay*, not about
    # having no provenance.
    assert get_settings().allow_replay_proposals is True
    with pytest.raises(ProposalIncompleteError, match="predates live PVGIS provenance"):
        validate_ready(project, get_settings())


def test_a_legacy_snapshot_still_renders_for_an_already_issued_proposal(client) -> None:
    """Documents already in customers' hands must not break.

    The guard is at finalisation, which a finalised proposal has already passed.
    Its snapshot is frozen and read straight from the proposal row, so the
    public payload and the PDF never come back through `validate_ready`.
    """
    from app.core.config import get_settings
    from app.services.pdf import build_context, render_html

    project_id = _analysed(client)
    assert client.post(f"/api/v1/projects/{project_id}/finalize").status_code == 200

    token = _token(client, project_id)
    share = client.get(f"/api/v1/proposals/{token}").json()

    # Strip the provenance from the frozen snapshot, exactly as a fixture-era
    # proposal would have been stored. The share page reads it as-is.
    legacy = {k: v for k, v in share.items() if k != "energy"}
    legacy["energy"] = {k: v for k, v in share["energy"].items() if k != "pvgis"}
    assert legacy["energy"]["totalAnnualProductionKwh"] > 0

    html = render_html(
        build_context(
            legacy,
            share_token=token,
            created_at=share["createdAt"],
            settings=get_settings(),
        )
    )
    assert "kWh" in html
