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
    # Who it is for, above all. A revision that dropped the customer would be a
    # proposal addressed to nobody, and the failure would surface only at the
    # point of sending - after the work of re-analysing and re-finalising.
    "customer_id",
    "name",
    "raw_location_input",
    "resolved_latitude",
    "resolved_longitude",
    "monthly_consumption_kwh",
    "selected_system_size_kwp",
    # Absent until 2026-07-31, and its absence silently re-priced every revision
    # at the configured case rate. See `test_a_revision_keeps_the_customers_tariff`.
    "electricity_tariff_eur_per_kwh",
    "analysis_json",
)


async def find_revision(session: AsyncSession, parent: Project) -> Project | None:
    return (
        await session.execute(select(Project).where(Project.revision_of_project_id == parent.id))
    ).scalar_one_or_none()


def _new_revision(parent: Project, *, recomputes: bool) -> Project:
    revision = Project(
        # Editable, never `completed`. The revision has no proposal of its own
        # yet, so presenting it as finished would be a lie about a document
        # that does not exist.
        current_step=ProjectStep.PROPOSAL.value,
        # The snapshot carried over describes the parent's inputs, and the
        # change is about to be applied. Until the recompute lands, that is
        # exactly what "recalculating" means.
        #
        # Unless the change reaches nothing the analysis depends on. Moving a
        # proposal to a different recipient leaves the roof, the production and
        # every financial figure identical, so there is nothing to recompute -
        # and marking such a revision `recalculating` would strand it, because
        # `validate_ready` refuses to finalise that status and no recomputation
        # is ever going to arrive to clear it.
        analysis_status="recalculating" if recomputes else parent.analysis_status,
        revision_of_project_id=parent.id,
    )
    for field in CARRIED_FORWARD:
        setattr(revision, field, getattr(parent, field))
    return revision


async def find_or_create_revision(
    session: AsyncSession, parent: Project, *, recomputes: bool = True
) -> Project:
    """The parent's editable revision, creating it only if there is none.

    Select-then-insert, with the unique index as the tie-breaker. A savepoint
    isolates the insert so a losing race does not poison the surrounding
    transaction - the caller is mid-request and still has an assistant message
    and a chat log to write.

    `recomputes=False` says the change this fork carries has no analysis
    dependency, so the revision inherits the parent's analysis state instead of
    being marked as awaiting a recomputation that will never come.
    """
    existing = await find_revision(session, parent)
    if existing is not None:
        return existing

    revision = _new_revision(parent, recomputes=recomputes)
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


async def lineage(session: AsyncSession, project: Project) -> list[Project]:
    """The chain from the root project down to `project`, root first.

    Revision numbering is *derived from this*, not stored on the project. There
    is exactly one revision mechanism in this application - a forked project -
    and adding a counter beside it would be a second one that could disagree.

    The `seen` guard is not paranoia about a cycle that the UNIQUE constraint
    already makes hard to create; it is what stops a corrupted chain from
    hanging a request instead of returning a slightly wrong number.
    """
    chain = [project]
    seen = {project.id}
    current = project

    while current.revision_of_project_id is not None:
        parent = await session.get(Project, current.revision_of_project_id)
        if parent is None or parent.id in seen:
            break
        chain.append(parent)
        seen.add(parent.id)
        current = parent

    chain.reverse()
    return chain


async def full_chain(session: AsyncSession, project: Project) -> list[Project]:
    """Every project in this lineage, root first - including later revisions.

    Walks up to the root and back down, so the whole history is reachable from
    any member of it. That is what lets the revision list look the same whether
    the operator opened the original project or the newest one.
    """
    chain = await lineage(session, project)
    seen = {row.id for row in chain}
    current = chain[-1]

    while True:
        child = await find_revision(session, current)
        if child is None or child.id in seen:
            break
        chain.append(child)
        seen.add(child.id)
        current = child

    return chain


async def revision_number_for(session: AsyncSession, project: Project) -> int:
    """1 for a root project, 2 for its revision, and so on."""
    return len(await lineage(session, project))


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
    "full_chain",
    "lineage",
    "revision_notice",
    "revision_number_for",
]
