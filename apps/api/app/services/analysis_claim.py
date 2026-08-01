"""One analysis batch per project, enforced by the database.

Two `run-analysis` requests for the same project used to issue two full sets of
PVGIS calls and then race to write. Whichever finished second won, which is not
the same as whichever was *asked* second - so the stored snapshot could describe
inputs that had already been superseded, and the customer paid for eight probes
to get four.

The fix is a claim, and the claim is taken in the database rather than in
process memory. The API is meant to run behind more than one worker; a lock held
in one of them protects nothing.

Two columns carry it, and they are dedicated rather than borrowed:

* `analysis_lease_until` - when the claim expires, so a process that dies
  mid-analysis does not block its project for ever;
* `analysis_run_id` - **who** holds it, which is what makes the lease safe.

`updated_at` could not serve either purpose. Every chat turn bumps it, so it
would silently extend the claim of a batch that had already died, and it carries
no identity, so it could not tell a live holder from an expired one.

The identity is the important half. A lease alone is *worse* than no lease: it
hands the project to a second batch while the first is still running and still
intending to write. So every terminal write carries the run id in its WHERE
clause. A batch whose lease expired finds `rowcount == 0`, learns that a newer
run owns the project, and refuses to write rather than clobbering a fresher
analysis with a staler one.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import CursorResult, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AnalysisInProgressError, AnalysisSupersededError
from app.models.tables import Project

logger = logging.getLogger("solarvis.analysis.claim")

#: The statuses that mean a batch is working on this project right now.
ACTIVE_STATUSES = ("running", "recalculating")


@dataclass(frozen=True)
class AnalysisClaim:
    """Proof that this process owns the project's analysis, until it expires."""

    project_id: str
    run_id: str
    expires_at: datetime


async def claim_analysis(
    session: AsyncSession, project: Project, *, status: str, settings: Settings
) -> AnalysisClaim:
    """Take the claim, or refuse the request without calling PVGIS.

    Conditional UPDATE rather than read-then-write: the check and the write are
    one statement, so two requests arriving together cannot both observe an idle
    project and both proceed.

    A claim is available when no batch holds one, or when the holder's lease has
    expired - the second is what stops a killed process blocking its project
    permanently.
    """
    # End the caller's read transaction before attempting the claim.
    #
    # The route loads the project - and, on the recompute path, has already
    # applied the customer's change - which opens a transaction with a snapshot
    # of the row as it was *then*. Evaluating the conditional UPDATE inside that
    # snapshot is how two requests both saw an idle project and both proceeded:
    # observed as two 200s and eight PVGIS probes where there should have been
    # four. Committing here publishes the caller's work and starts the claim in
    # a fresh transaction, so the database compares against current state.
    await session.commit()

    now = datetime.now(UTC)
    run_id = str(uuid.uuid4())
    expires_at = now + timedelta(seconds=settings.analysis_lease_seconds)

    result: CursorResult[Any] = await session.execute(  # type: ignore[assignment]
        update(Project)
        .where(
            Project.id == project.id,
            (
                Project.analysis_status.not_in(ACTIVE_STATUSES)
                | Project.analysis_lease_until.is_(None)
                | (Project.analysis_lease_until < now)
            ),
        )
        # The comparison belongs in SQL, not in the ORM's Python evaluator.
        # SQLite hands back naive datetimes, so evaluating `lease_until < now`
        # in process raises on the tz-aware side - and the whole point of a
        # conditional UPDATE is that the database decides, not this process.
        # `refresh` below brings the in-memory object back in line.
        .execution_options(synchronize_session=False)
        .values(
            analysis_status=status,
            analysis_run_id=run_id,
            analysis_lease_until=expires_at,
            analysis_error_json=None,
        )
    )

    if result.rowcount == 0:
        raise AnalysisInProgressError(
            "An analysis is already running for this project. "
            "Wait for it to finish, then try again."
        )

    # Commit again before the slow part, for two further reasons. It makes the
    # claim visible to a concurrent request - which is the whole point - and it
    # closes SQLite's write transaction, which would otherwise be held across
    # four PVGIS calls and an FX lookup while every other writer queued behind
    # it. One that exceeded `busy_timeout` would fail with "database is locked"
    # on an unrelated request.
    await session.commit()
    await session.refresh(project)

    logger.debug("claimed analysis for %s as %s until %s", project.id, run_id, expires_at)
    return AnalysisClaim(project_id=project.id, run_id=run_id, expires_at=expires_at)


async def complete_analysis(
    session: AsyncSession,
    claim: AnalysisClaim,
    *,
    snapshot: dict[str, Any],
    status: str = "complete",
    current_step: str | None = None,
) -> None:
    """Store the result, but only if this run still owns the project."""
    values: dict[str, Any] = {
        "analysis_json": snapshot,
        "analysis_status": status,
        "analysis_error_json": None,
        "analysis_run_id": None,
        "analysis_lease_until": None,
    }
    if current_step is not None:
        values["current_step"] = current_step
    await _fenced_write(session, claim, values)


async def fail_analysis(
    session: AsyncSession,
    claim: AnalysisClaim,
    *,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> None:
    """Record why this batch produced nothing, if it still owns the project.

    `failed` is used only where there is no usable analysis at all. A *recompute*
    that fails leaves the previous figures in place, which is what `stale`
    already says, so the caller passes that status instead.

    The reason is stored in its own column. Putting it inside `analysis_json`
    would make a failure indistinguishable from a result to every reader that
    keys off that column's presence - `validate_ready` among them.
    """
    await _fenced_write(
        session,
        claim,
        {
            "analysis_status": "failed",
            "analysis_json": None,
            "analysis_error_json": {"code": code, "message": message, "details": details or {}},
            "analysis_run_id": None,
            "analysis_lease_until": None,
        },
    )


async def release_stale_claim(session: AsyncSession, claim: AnalysisClaim, *, status: str) -> None:
    """Hand the project back after a recompute failure, if still the owner."""
    await _fenced_write(
        session,
        claim,
        {"analysis_status": status, "analysis_run_id": None, "analysis_lease_until": None},
    )


async def _fenced_write(
    session: AsyncSession, claim: AnalysisClaim, values: dict[str, Any]
) -> None:
    """Write only while this run still holds the claim.

    The `analysis_run_id` predicate is the fence. Without it the lease would be
    actively harmful: it would release the project to a second batch after the
    timeout, and then let the first batch - still alive, still holding a
    snapshot it computed from inputs that may since have changed - overwrite the
    second batch's fresher result.
    """
    result: CursorResult[Any] = await session.execute(  # type: ignore[assignment]
        update(Project)
        .where(Project.id == claim.project_id, Project.analysis_run_id == claim.run_id)
        .execution_options(synchronize_session=False)
        .values(**values)
    )
    if result.rowcount == 0:
        await session.rollback()
        logger.warning(
            "analysis run %s no longer owns project %s; refusing to write",
            claim.run_id,
            claim.project_id,
        )
        raise AnalysisSupersededError(
            "This analysis was superseded by a newer one and its result was discarded."
        )
    await session.commit()
