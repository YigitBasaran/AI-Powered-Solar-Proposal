"""Editing a project that has already been finalised.

A finalised proposal is immutable, and `finalise_proposal` returns the existing
one for a project that already has it. So the obvious implementation of "change
my system size" on a finalised project - just write the new value - produces a
project whose figures say one thing and whose issued document says another,
with no error anywhere and a share link still serving the old numbers.

The answer is a **revision**: a new editable project carrying the parent's
inputs and its snapshot, with the change applied and only the dependent
sections recomputed. The parent, its proposal and its share link are never
touched. Finalising the revision issues a *new* token, because
`existing_proposal` looks up by project id.

Two things are deliberately structural rather than procedural.

**A revision never inherits a finalised state.** `current_step` is set to
`proposal` - editable - and never `completed`, and the `proposals` relationship
is not copied. Inheriting either would give the revision a document it never
issued.

**At most one revision per parent, enforced by the database.**
`revision_of_project_id` is UNIQUE, so a retried or concurrent delivery of the
same change cannot fork two drafts. The loser of the insert race catches
`IntegrityError` and re-selects the winner's row - the standard upsert, with
the index as the authority rather than application timing.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import ProjectStep
from app.models.tables import Project

logger = logging.getLogger("solarvis.revisions")

#: Copied to a revision on creation. Everything else - the proposal
#: relationship above all - is deliberately absent.
CARRIED_FORWARD = (
    "raw_location_input",
    "resolved_latitude",
    "resolved_longitude",
    "monthly_consumption_kwh",
    "selected_system_size_kwp",
    "analysis_json",
)


async def find_revision(session: AsyncSession, parent: Project) -> Project | None:
    return (
        await session.execute(
            select(Project).where(Project.revision_of_project_id == parent.id)
        )
    ).scalar_one_or_none()


def _new_revision(parent: Project) -> Project:
    revision = Project(
        # Editable, never `completed`. The revision has no proposal of its own
        # yet, so presenting it as finished would be a lie about a document
        # that does not exist.
        current_step=ProjectStep.PROPOSAL.value,
        # The snapshot carried over describes the parent's inputs, and the
        # change is about to be applied. Until the recompute lands, that is
        # exactly what "recalculating" means.
        analysis_status="recalculating",
        revision_of_project_id=parent.id,
    )
    for field in CARRIED_FORWARD:
        setattr(revision, field, getattr(parent, field))
    return revision


async def find_or_create_revision(session: AsyncSession, parent: Project) -> Project:
    """The parent's editable revision, creating it only if there is none.

    Select-then-insert, with the unique index as the tie-breaker. A savepoint
    isolates the insert so a losing race does not poison the surrounding
    transaction - the caller is mid-request and still has an assistant message
    and a chat log to write.
    """
    existing = await find_revision(session, parent)
    if existing is not None:
        return existing

    revision = _new_revision(parent)
    try:
        async with session.begin_nested():
            session.add(revision)
            await session.flush()
    except IntegrityError:
        # Another request created it between the select and the insert. The
        # database refused the second row, which is the whole point.
        logger.info("revision race on project %s; re-selecting the winner", parent.id)
        winner = await find_revision(session, parent)
        if winner is None:  # pragma: no cover - the constraint guarantees one
            raise
        return winner

    logger.info("forked revision %s from finalised project %s", revision.id, parent.id)
    return revision


def revision_notice(parent: Project, share_token: str | None, web_base_url: str) -> str:
    """Why the conversation just moved, in terms a customer can act on."""
    link = f"{web_base_url.rstrip('/')}/proposal/{share_token}" if share_token else "your proposal"
    return (
        f"Your proposal at {link} was already issued and can't change — that link "
        f"has to keep showing exactly the figures you sent.\n\n"
        f"I've started a revision with your change and I'm recalculating now. "
        f"Finalising the revision will give you a new link; the old one stays "
        f"exactly as it is."
    )


__all__ = [
    "CARRIED_FORWARD",
    "find_or_create_revision",
    "find_revision",
    "revision_notice",
]
