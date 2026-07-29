"""One analysis batch per project, and no stale write over a fresh one.

Two things are being defended here, and they fail in opposite directions.

Without a claim, two concurrent requests each issue a full set of PVGIS calls
and then race to write - so the customer pays for eight probes to get four, and
the winner is whichever *finished* last rather than whichever was *asked* last.

Without a fencing token, the claim's expiry is worse than having no claim: it
hands the project to a second batch while the first is still alive and still
intending to write, so the first can overwrite a fresher result with an older
one. Silently, because both are well-formed snapshots.
"""

from __future__ import annotations

import asyncio
from datetime import UTC

import pytest

CASE_COORD = "-34.04658242871865, 18.46491476666948"


def _intake(client) -> str:
    project_id = client.post("/api/v1/projects").json()["projectId"]
    for message in (CASE_COORD, "1,150 kWh", "6 kWp"):
        client.post(f"/api/v1/projects/{project_id}/chat", json={"message": message})
    return project_id


# ---------------------------------------------------------------------------
# The claim
# ---------------------------------------------------------------------------


def test_a_second_analysis_is_refused_while_one_holds_the_claim(client, stub_requests) -> None:
    """And the refusal costs no PVGIS traffic at all.

    The claim is planted rather than raced for. `TestClient` drives every
    request through one event loop, so whether two threads actually overlap
    depends on where the handler happens to await - on a cold start the first
    request finishes before the second begins, and two sequential analyses are
    correct behaviour that looks exactly like a broken claim. Holding the claim
    outright tests the thing that was specified. `test_two_concurrent_claims_
    produce_one_winner` covers the race itself, at the level where it happens.
    """
    project_id = _intake(client)
    _hold_the_claim(project_id)
    stub_requests.clear()

    response = client.post(f"/api/v1/projects/{project_id}/run-analysis")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ANALYSIS_IN_PROGRESS"
    assert stub_requests == [], "the refused request called PVGIS anyway"


def _hold_the_claim(project_id: str, *, seconds: float = 120.0) -> None:
    """Mark the project as claimed by a batch that is still running."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from app.models.tables import Project

    async def _apply() -> None:
        async with _session() as session:
            project = (
                await session.execute(select(Project).where(Project.id == project_id))
            ).scalar_one()
            project.analysis_status = "running"
            project.analysis_run_id = "a-batch-that-is-still-working"
            project.analysis_lease_until = datetime.now(UTC) + timedelta(seconds=seconds)
            await session.commit()

    asyncio.run(_apply())


async def test_two_concurrent_claims_produce_one_winner(offline_env) -> None:
    """The race itself, on two independent sessions.

    Both attempts read an idle project; only one may write. That has to be the
    database's decision - the API is meant to run behind more than one worker,
    so a lock held in one process would protect nothing.
    """
    from app.core.config import get_settings
    from app.core.errors import AnalysisInProgressError
    from app.models.tables import Project
    from app.services.analysis_claim import claim_analysis

    settings = get_settings()

    async with _session() as setup:
        project = Project()
        setup.add(project)
        await setup.commit()
        project_id = project.id

    async def _attempt() -> str:
        async with _session() as session:
            from sqlalchemy import select

            mine = (
                await session.execute(select(Project).where(Project.id == project_id))
            ).scalar_one()
            try:
                await claim_analysis(session, mine, status="running", settings=settings)
            except AnalysisInProgressError:
                return "refused"
            return "granted"

    outcomes = await asyncio.gather(_attempt(), _attempt())

    assert sorted(outcomes) == ["granted", "refused"], (
        f"both claims resolved the same way: {outcomes}"
    )


async def test_an_expired_claim_can_be_reclaimed(offline_env) -> None:
    """A hard kill must not block its project for ever."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from app.core.config import get_settings
    from app.models.tables import Project
    from app.services.analysis_claim import claim_analysis

    async with _session() as session:
        project = Project(analysis_status="running")
        project.analysis_run_id = "a-run-that-died"
        project.analysis_lease_until = datetime.now(UTC) - timedelta(seconds=1)
        session.add(project)
        await session.commit()
        project_id = project.id

        claim = await claim_analysis(
            session, project, status="running", settings=get_settings()
        )
        assert claim.run_id != "a-run-that-died"

        stored = (
            await session.execute(select(Project).where(Project.id == project_id))
        ).scalar_one()
        assert stored.analysis_run_id == claim.run_id


async def test_an_unexpired_claim_is_refused(offline_env) -> None:
    from datetime import UTC, datetime, timedelta

    from app.core.config import get_settings
    from app.core.errors import AnalysisInProgressError
    from app.models.tables import Project
    from app.services.analysis_claim import claim_analysis

    async with _session() as session:
        project = Project(analysis_status="running")
        project.analysis_run_id = "a-live-run"
        project.analysis_lease_until = datetime.now(UTC) + timedelta(seconds=60)
        session.add(project)
        await session.commit()

        with pytest.raises(AnalysisInProgressError) as caught:
            await claim_analysis(session, project, status="running", settings=get_settings())
        assert caught.value.code == "ANALYSIS_IN_PROGRESS"
        assert caught.value.status_code == 409


# ---------------------------------------------------------------------------
# The fence
# ---------------------------------------------------------------------------


async def test_an_expired_run_cannot_overwrite_a_newer_analysis(offline_env) -> None:
    """The reason the lease has an identity and not only an expiry.

    Run A claims, its lease expires, run B claims and completes. A is still
    alive and still holding a snapshot it computed - and it must not write it.
    """
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from app.core.config import get_settings
    from app.core.errors import AnalysisSupersededError
    from app.models.tables import Project
    from app.services.analysis_claim import claim_analysis, complete_analysis

    settings = get_settings()

    async with _session() as session:
        project = Project()
        session.add(project)
        await session.commit()
        project_id = project.id

        run_a = await claim_analysis(session, project, status="running", settings=settings)

        # A's lease expires while it is still working.
        project.analysis_lease_until = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

        run_b = await claim_analysis(session, project, status="running", settings=settings)
        assert run_b.run_id != run_a.run_id
        await complete_analysis(session, run_b, snapshot={"whose": "B"})

        with pytest.raises(AnalysisSupersededError) as caught:
            await complete_analysis(session, run_a, snapshot={"whose": "A"})
        assert caught.value.status_code == 409

        stored = (
            await session.execute(select(Project).where(Project.id == project_id))
        ).scalar_one()
        assert stored.analysis_json == {"whose": "B"}, "the stale run clobbered a fresher result"


async def test_a_chat_message_does_not_extend_an_analysis_lease(client, offline_env) -> None:
    """Which is why the lease is not `updated_at`.

    Every chat turn bumps that column. Riding the lease on it would silently
    keep renewing the claim of a batch that had already died.
    """
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from app.models.tables import Project

    project_id = client.post("/api/v1/projects").json()["projectId"]
    deadline = datetime.now(UTC) + timedelta(seconds=45)

    async with _session() as session:
        project = (
            await session.execute(select(Project).where(Project.id == project_id))
        ).scalar_one()
        project.analysis_status = "running"
        project.analysis_run_id = "held"
        project.analysis_lease_until = deadline
        await session.commit()
        before = project.updated_at

    client.post(f"/api/v1/projects/{project_id}/chat", json={"message": CASE_COORD})

    async with _session() as session:
        project = (
            await session.execute(select(Project).where(Project.id == project_id))
        ).scalar_one()
        # That the chat turn wrote is asserted on what it wrote, not on a
        # timestamp: the location it carried is now stored. A `updated_at`
        # comparison would be asserting the clock's resolution.
        assert project.raw_location_input, "the chat turn did not touch the row"
        assert _naive(project.updated_at) >= _naive(before)
        assert project.analysis_lease_until is not None
        drift = abs((_naive(project.analysis_lease_until) - _naive(deadline)).total_seconds())
        assert drift < 1.0, "the chat turn moved the analysis lease"


def _session():
    from app.db.session import get_sessionmaker

    return get_sessionmaker()()


def _naive(when):
    """The same instant without a timezone, so SQLite's values compare."""
    if when.tzinfo is None:
        return when
    return when.astimezone(UTC).replace(tzinfo=None)
