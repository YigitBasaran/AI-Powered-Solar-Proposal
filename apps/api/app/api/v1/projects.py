"""Project and chat endpoints.

Route functions stay thin: they translate HTTP to domain calls and back. All
workflow rules live in `services/workflow.py` and all analysis in
`services/analysis.py`, so business logic is never duplicated into a handler.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import (
    AnalysisInProgressError,
    AnalysisSupersededError,
    AppError,
    DeletionRefusedError,
    InvalidStepTransitionError,
    NotFoundError,
)
from app.db.session import commit_before_response, get_session
from app.domain.customers import mask_email
from app.domain.models import ProjectStep
from app.integrations.exchange_rates import ExchangeRateService, SqlExchangeRateCache
from app.models.tables import ChatMessage, Customer, Project, Proposal, _utcnow, iso_utc

# Imported as a module, not by name: the recompute functions are called
# through it so a failing recomputation can actually be exercised, and so the
# route always reaches the current definition rather than one bound at import.
from app.services import activity, proposal_email
from app.services import analysis as analysis_service
from app.services import customers as customer_service
from app.services import proposal as proposal_service
from app.services.analysis import run_analysis, serialise_analysis
from app.services.analysis_claim import (
    claim_analysis,
    complete_analysis,
    fail_analysis,
    release_stale_claim,
)
from app.services.conversation.actions import ActionKind
from app.services.conversation.answers import answer_and_polish
from app.services.conversation.context import RECENT_TURN_LIMIT, Turn, build_context
from app.services.conversation.router import route_message
from app.services.imagery import require_calibrated_imagery
from app.services.proposal import existing_proposal
from app.services.revisions import (
    find_or_create_revision,
    find_revision,
    full_chain,
    revision_notice,
)
from app.services.workflow import (
    ProjectState,
    StepOutcome,
    handle_message,
    initial_assistant_message,
    progress,
)

logger = logging.getLogger("solarvis.api")

router = APIRouter(prefix="/projects", tags=["projects"])

MAX_MESSAGE_LENGTH = 2000

#: The only columns a chat message may write.
#:
#: The previous route looped `setattr` over whatever the state machine returned.
#: That was safe only for as long as nobody added a key, and it made "a question
#: never mutates state" a convention rather than something a test could check.
ASSIGNABLE: frozenset[str] = frozenset(
    {
        "raw_location_input",
        "resolved_latitude",
        "resolved_longitude",
        "monthly_consumption_kwh",
        "selected_system_size_kwp",
        "electricity_tariff_eur_per_kwh",
        "analysis_json",
        "analysis_status",
    }
)


class CreateProjectRequest(BaseModel):
    """Optional, and the whole body may be omitted.

    The chat-first entry point creates a project before anyone has been named,
    and every existing client posts no body at all. Both keep working.
    """

    customerId: str | None = None
    name: str | None = None


class AssignCustomerRequest(BaseModel):
    customerId: str


class RenameProjectRequest(BaseModel):
    """The label only. Nothing here can move a figure."""

    name: str | None = None


class CreateProjectResponse(BaseModel):
    projectId: str
    currentStep: str
    assistantMessage: str
    progress: list[dict[str, Any]]
    customer: dict[str, Any] | None = None


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_LENGTH)


class ChatResponse(BaseModel):
    projectId: str
    currentStep: str
    assistantMessage: str
    accepted: bool
    #: Derived from `interpretation.effectiveProvider`, so the flat field can
    #: never contradict the object beside it.
    parserSource: str
    progress: list[dict[str, Any]]
    readyForAnalysis: bool
    analysisStatus: str = "pending"
    interpretation: dict[str, Any] | None = None
    #: Set when this reply came from a revision the change forked.
    revisionOfProjectId: str | None = None
    #: Which inputs this message recalculated, so the client knows to re-read
    #: the analysis. Sending the whole snapshot on every chat reply would be a
    #: large payload for the one message in fifty that changes it.
    recalculated: list[str] | None = None


class ProjectResponse(BaseModel):
    projectId: str
    currentStep: str
    rawLocationInput: str | None
    resolvedLatitude: float | None
    resolvedLongitude: float | None
    monthlyConsumptionKwh: float | None
    annualConsumptionKwh: float | None
    selectedSystemSizeKwp: float | None
    requestedPanelCount: int | None
    #: The customer's own electricity price, or null for the configured case rate.
    #:
    #: Published because it is an input *they* set and may want to check. It was
    #: stored, used in every financial figure, and invisible to every client -
    #: so nobody could confirm the price their payback had been quoted at.
    electricityTariffEurPerKwh: float | None = None
    analysisStatus: str
    #: `{code, message, details}` when the last analysis failed, else null.
    #:
    #: Carried on the project rather than inside `analysis`, because a failed
    #: analysis has no `analysis` to carry it - which is the whole distinction
    #: between `failed` and `stale`.
    analysisError: dict[str, Any] | None = None
    progress: list[dict[str, Any]]
    messages: list[dict[str, Any]]
    analysis: dict[str, Any] | None
    revisionOfProjectId: str | None = None
    revisionProjectId: str | None = None
    hasProposal: bool = False
    #: An optional human label for telling two projects for one customer apart.
    name: str | None = None
    #: The full internal customer record, or null when none is linked. Legacy
    #: projects predate customers entirely and stay null - no placeholder is
    #: invented for them, because a fabricated record is indistinguishable from
    #: a real one the moment it is written.
    customer: dict[str, Any] | None = None


async def _pending_confirmation(session: AsyncSession, project: Project) -> str | None:
    """What the *immediately preceding* assistant message offered, if anything.

    Kept in `payload_json` rather than a new column, and deliberately read from
    the last message only: an offer made five turns ago is not something a
    later "yes" can be assumed to answer.
    """
    last = (
        await session.execute(
            select(ChatMessage)
            .where(ChatMessage.project_id == project.id, ChatMessage.role == "assistant")
            .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if last is None or not isinstance(last.payload_json, dict):
        return None
    resolution = last.payload_json.get("resolution")
    if not isinstance(resolution, dict):
        return None
    pending = resolution.get("pendingConfirmation")
    return pending if isinstance(pending, str) else None


async def _recent_turns(session: AsyncSession, project: Project) -> list[Turn]:
    """The last few turns, oldest first.

    Bounded deliberately. An unbounded transcript crowds out the instructions
    and, because attention favours the tail, lets a turn from twenty messages
    ago outrank the current one. The compact state summary is what carries the
    facts; this is only here so a pronoun has something to point at.
    """
    rows = (
        await session.execute(
            select(ChatMessage)
            .where(ChatMessage.project_id == project.id)
            .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
            .limit(RECENT_TURN_LIMIT)
        )
    ).scalars().all()
    return [Turn(role=row.role, content=row.content) for row in reversed(rows)]


async def _project_state(session: AsyncSession, project: Project) -> ProjectState:
    """The read-model the state machine and the answer service both work from."""
    proposal = (
        await session.execute(
            select(Proposal).where(Proposal.project_id == project.id).limit(1)
        )
    ).scalar_one_or_none()

    return ProjectState(
        current_step=ProjectStep(project.current_step),
        raw_location_input=project.raw_location_input,
        monthly_consumption_kwh=project.monthly_consumption_kwh,
        selected_system_size_kwp=project.selected_system_size_kwp,
        analysis_status=project.analysis_status,
        analysis=project.analysis_json,
        has_finalised_proposal=proposal is not None,
        proposal_snapshot=proposal.proposal_data_json if proposal is not None else None,
        proposal_share_token=proposal.share_token if proposal is not None else None,
        # Masked, because the state machine composes it into an assistant
        # message that is stored verbatim in the transcript. The operator
        # confirming a send sees the full address in the preview panel, which
        # is the one surface that needs it.
        proposal_recipient_masked=(
            mask_email((proposal.customer_snapshot_json or {}).get("email"))
            if proposal is not None
            else None
        ),
        pending_confirmation=await _pending_confirmation(session, project),
        revision_of_project_id=getattr(project, "revision_of_project_id", None),
    )


def _apply(project: Project, updates: dict[str, object]) -> None:
    """Write the state machine's updates, and nothing else."""
    for key, value in updates.items():
        if key not in ASSIGNABLE:
            raise InvalidStepTransitionError(f"{key} is not a chat-assignable field.")
        setattr(project, key, value)


async def _recalculate(
    session: AsyncSession, project: Project, outcome: StepOutcome, settings: Settings
) -> list[str] | None:
    """Recompute exactly what the change reached, and nothing more.

    Only ever the dependent sections: a consumption change never re-runs PVGIS
    and never re-reads the exchange rate, because doing so would move the rate
    the customer was quoted out from under them mid-conversation.
    """
    if not outcome.changed_inputs or not project.analysis_json:
        return None
    if project.analysis_status not in {"complete", "stale", "recalculating"}:
        return None

    snapshot = dict(project.analysis_json)
    # The same claim as `run-analysis`, because a size change that cannot reuse
    # its stored probes is also an analysis batch. Committed before the slow
    # part for the same reasons: it makes the claim visible, and it releases
    # SQLite's write lock. A process that dies mid-recompute leaves the project
    # honestly marked as recalculating until its lease expires.
    try:
        claim = await claim_analysis(
            session, project, status="recalculating", settings=settings
        )
    except AnalysisInProgressError:
        # A chat turn is not worth failing over a busy analysis; the change is
        # already stored, and the snapshot is correctly left describing the
        # older inputs until something recomputes it.
        logger.info("skipping recalculation for %s: an analysis holds the claim", project.id)
        project.analysis_status = "stale"
        return None

    try:
        if outcome.changed_inputs <= {"monthly_consumption_kwh", "electricity_tariff_eur_per_kwh"}:
            updated = analysis_service.recompute_for_consumption(
                snapshot=snapshot,
                monthly_consumption_kwh=float(project.monthly_consumption_kwh or 0.0),
                settings=settings,
                tariff_eur_per_kwh=project.electricity_tariff_eur_per_kwh,
            )
        else:
            updated = await analysis_service.recompute_for_system_size(
                snapshot=snapshot,
                system_size_kwp=float(project.selected_system_size_kwp or 0.0),
                monthly_consumption_kwh=float(project.monthly_consumption_kwh or 0.0),
                settings=settings,
                tariff_eur_per_kwh=project.electricity_tariff_eur_per_kwh,
            )
    except Exception:
        # The snapshot now describes inputs the project no longer has. Leaving
        # the status at "complete" would present the old figures as though they
        # described the new ones, which is the failure this whole layer exists
        # to prevent.
        #
        # `stale`, not `failed`: the previous figures are still here. `failed`
        # is reserved for having no usable analysis at all.
        logger.exception("recalculation failed for project %s", project.id)
        with contextlib.suppress(AnalysisSupersededError):
            await release_stale_claim(session, claim, status="stale")
        await session.refresh(project)
        return None

    try:
        await complete_analysis(session, claim, snapshot=updated)
    except AnalysisSupersededError:
        # A newer batch owns the project. Its result stands; this one is
        # discarded rather than written over the top of it.
        logger.info("recalculation for %s was superseded", project.id)
        await session.refresh(project)
        return None
    await session.refresh(project)
    return sorted(outcome.changed_inputs)


async def _send_confirmed_proposal(
    session: AsyncSession, project: Project, settings: Settings
) -> str:
    """Send, and report what actually happened.

    Every outcome is reported in the customer's own words, and none of them is
    invented: the reply is composed from the delivery record the send produced.
    A failure says so and offers the link, because a failed email leaves a
    perfectly good proposal that can simply be copied and pasted.

    Console mode is reported as "recorded" rather than "sent" - the provider
    travels on the record, so the wording cannot drift from what happened.
    """
    proposal = await existing_proposal(session, project)
    if proposal is None:  # pragma: no cover - the offer requires a proposal
        return "There is no finalised proposal to send."

    try:
        delivery = await proposal_email.send(
            session, proposal, settings=settings, commit=commit_before_response
        )
    except AppError as error:
        link = f"{settings.web_base_url.rstrip('/')}/proposal/{proposal.share_token}"
        return (
            f"I could not send it: {error.message}\n\n"
            f"The proposal itself is fine and the link still works, so you can send "
            f"it yourself if you prefer:\n{link}"
        )

    recipient = proposal_email.serialise(delivery)["recipientMasked"]
    if delivery.provider == "console":
        return (
            f"Recorded the email to {recipient} in console mode — nothing was actually "
            f"sent. Configure SMTP to deliver it for real."
        )
    return f"Sent. {recipient} now has a link to the proposal."


async def _load(session: AsyncSession, project_id: str) -> Project:
    project = (
        await session.execute(select(Project).where(Project.id == project_id))
    ).scalar_one_or_none()
    if project is None:
        raise NotFoundError(f"Project {project_id} does not exist.")
    return project


def _to_response(
    project: Project,
    settings: Settings,
    *,
    has_proposal: bool = False,
    revision_project_id: str | None = None,
    customer: Customer | None = None,
) -> ProjectResponse:
    step = ProjectStep(project.current_step)
    monthly = project.monthly_consumption_kwh
    size = project.selected_system_size_kwp
    return ProjectResponse(
        name=project.name,
        customer=customer_service.serialise(customer) if customer is not None else None,
        revisionOfProjectId=project.revision_of_project_id,
        revisionProjectId=revision_project_id,
        hasProposal=has_proposal,
        projectId=project.id,
        currentStep=project.current_step,
        rawLocationInput=project.raw_location_input,
        resolvedLatitude=project.resolved_latitude,
        resolvedLongitude=project.resolved_longitude,
        monthlyConsumptionKwh=monthly,
        annualConsumptionKwh=monthly * 12.0 if monthly else None,
        selectedSystemSizeKwp=size,
        electricityTariffEurPerKwh=project.electricity_tariff_eur_per_kwh,
        requestedPanelCount=settings.required_panel_count(size) if size else None,
        analysisStatus=project.analysis_status,
        analysisError=project.analysis_error_json,
        progress=progress(step),
        messages=[
            {
                "role": m.role,
                "content": m.content,
                "step": m.step,
                "parserSource": m.parser_source,
                "createdAt": iso_utc(m.created_at),
            }
            for m in project.messages
        ],
        analysis=project.analysis_json,
    )


async def _customer_of(session: AsyncSession, project: Project) -> Customer | None:
    if project.customer_id is None:
        return None
    return await session.get(Customer, project.customer_id)


@router.post("", response_model=CreateProjectResponse, status_code=201)
async def create_project(
    payload: CreateProjectRequest | None = Body(default=None),
    session: AsyncSession = Depends(get_session),
) -> CreateProjectResponse:
    # `Body(default=None)` rather than a required model: the sample-output
    # script and both E2E launchers post this route with no body, and a
    # customerless project remains legal at the API. The *product* requires one
    # - the UI has no path that creates a project without picking a customer
    # first - but enforcing it here would break the quick-estimate harnesses
    # that predate customers entirely, and would make the case brief's own
    # single-property flow unrunnable.
    customer: Customer | None = None
    if payload is not None and payload.customerId:
        customer = await customer_service.get_customer(session, payload.customerId)

    # Named on creation when the caller did not name it, so it is identifiable
    # in a list on the day it is made rather than showing as "Draft project"
    # beside three others.
    name = (payload.name or "").strip() if payload is not None else ""
    if not name and customer is not None:
        name = f"{customer.display_name} — {_utcnow().strftime('%d %b %Y')}"

    project = Project(
        current_step=ProjectStep.LOCATION.value,
        customer_id=customer.id if customer is not None else None,
        name=name or None,
    )
    session.add(project)
    await session.flush()

    # Atomic with the creation: the timeline's first entry is not an
    # observation about the project, it is the fact that it exists.
    await activity.record(
        session,
        event_type=activity.PROJECT_CREATED,
        actor="user",
        project_id=project.id,
        customer_id=project.customer_id,
        metadata={
            "projectName": project.name,
            "customerName": customer.display_name if customer is not None else None,
        },
    )

    greeting = initial_assistant_message()
    session.add(
        ChatMessage(
            project_id=project.id,
            role="assistant",
            content=greeting,
            step=project.current_step,
        )
    )
    await session.flush()
    await commit_before_response(session)

    logger.info("project created %s", project.id)
    return CreateProjectResponse(
        projectId=project.id,
        currentStep=project.current_step,
        assistantMessage=greeting,
        progress=progress(ProjectStep.LOCATION),
        customer=customer_service.serialise(customer) if customer is not None else None,
    )


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ProjectResponse:
    project = await _load(session, project_id)
    await session.refresh(project, ["messages"])
    proposal = await existing_proposal(session, project)
    revision = await find_revision(session, project)
    return _to_response(
        project,
        settings,
        has_proposal=proposal is not None,
        revision_project_id=revision.id if revision is not None else None,
        customer=await _customer_of(session, project),
    )


@router.get("")
async def list_projects(
    q: str | None = Query(default=None, max_length=200),
    customerId: str | None = Query(default=None, max_length=36),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Every project, newest first, with its customer and proposal if it has one.

    This is the only route that can find a project with **no customer** - a
    legacy row, or a walk-in estimate taken before anyone was named. Those are
    reachable from nowhere else: the customer screens are organised by person,
    and nobody keeps a project id.

    `q` matches the project name, the customer's name and their email, so one
    search box serves both ways an operator remembers a job.

    `customerId` narrows it to one person. That exists so a customer's own
    screen can serve its project list from *this* route rather than from the
    unpaginated array on `GET /customers/{id}` - one query, one page size, one
    delete affordance, instead of two lists that drift apart.
    """
    statement = (
        select(Project, Customer, Proposal)
        .outerjoin(Customer, Customer.id == Project.customer_id)
        .outerjoin(Proposal, Proposal.project_id == Project.id)
    )

    if customerId:
        statement = statement.where(Project.customer_id == customerId)

    if q and q.strip():
        term = f"%{q.strip().lower()}%"
        statement = statement.where(
            func.lower(func.coalesce(Project.name, "")).like(term)
            | func.lower(func.coalesce(Customer.display_name, "")).like(term)
            | func.lower(func.coalesce(Customer.email, "")).like(term)
        )

    total = (
        await session.execute(select(func.count()).select_from(statement.subquery()))
    ).scalar_one()

    rows = (
        await session.execute(
            statement.order_by(Project.created_at.desc(), Project.id.desc())
            .offset((page - 1) * pageSize)
            .limit(pageSize)
        )
    ).all()

    return {
        "page": page,
        "pageSize": pageSize,
        "total": int(total),
        "totalPages": max(1, -(-int(total) // pageSize)),
        "projects": [
            {
                "projectId": project.id,
                "name": project.name,
                "currentStep": project.current_step,
                "analysisStatus": project.analysis_status,
                "isRevision": project.revision_of_project_id is not None,
                "customer": (
                    {"customerId": customer.id, "displayName": customer.display_name}
                    if customer is not None
                    else None
                ),
                "hasProposal": proposal is not None,
                "shareToken": proposal.share_token if proposal else None,
                "reference": proposal.reference if proposal else None,
                "revisionNumber": (proposal.revision_number or 1) if proposal else None,
                "systemSizeKwp": proposal.feasible_system_size_kwp if proposal else None,
                "createdAt": iso_utc(project.created_at),
            }
            for project, customer, proposal in rows
        ]
    }


@router.get("/{project_id}/activity")
async def project_activity(
    project_id: str,
    limit: int = Query(default=activity.DEFAULT_PAGE_SIZE, ge=1, le=activity.MAX_PAGE_SIZE),
    cursor: str | None = Query(default=None, max_length=200),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """The whole lineage's history, newest first.

    Spans every project in the revision chain, because a revision is a separate
    project row - a timeline scoped to the current one alone would start halfway
    through the story, missing the original analysis and the first proposal.
    """
    project = await _load(session, project_id)
    chain = await full_chain(session, project)

    events, next_cursor = await activity.list_for_project(
        session, [member.id for member in chain], limit=limit, cursor=cursor
    )
    return {
        "events": [activity.serialise(event) for event in events],
        "nextCursor": next_cursor,
    }


@router.get("/{project_id}/revisions")
async def list_revisions(
    project_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Every proposal in this project's lineage, oldest first.

    Reachable from any member of the chain, so it reads the same whether the
    operator opened the original project or the newest revision. A project that
    was never finalised appears with a null proposal - it is part of the
    history either way.

    "Superseded" is *derived* here rather than stored: a proposal is superseded
    when a later one exists in the same chain. Writing a flag onto the older row
    would be a mutation of an issued document, which is the one thing this whole
    subsystem exists to prevent.
    """
    project = await _load(session, project_id)
    chain = await full_chain(session, project)

    rows: list[dict[str, Any]] = []
    for index, member in enumerate(chain):
        proposal = await existing_proposal(session, member)
        rows.append(
            {
                "revisionNumber": index + 1,
                "projectId": member.id,
                "isCurrent": member.id == chain[-1].id,
                "proposalId": proposal.id if proposal else None,
                "shareToken": proposal.share_token if proposal else None,
                "reference": proposal.reference if proposal else None,
                "finalisedAt": iso_utc(proposal.created_at) if proposal else None,
                "systemSizeKwp": proposal.feasible_system_size_kwp if proposal else None,
                "annualProductionKwh": proposal.annual_production_kwh if proposal else None,
                "customer": (
                    proposal_service.public_customer(proposal.customer_snapshot_json)
                    if proposal
                    else None
                ),
            }
        )

    latest_finalised = max(
        (row["revisionNumber"] for row in rows if row["proposalId"]), default=None
    )
    for row in rows:
        row["isSuperseded"] = bool(
            row["proposalId"] and latest_finalised and row["revisionNumber"] < latest_finalised
        )

    return {"revisions": rows}


@router.patch("/{project_id}", response_model=ProjectResponse)
async def rename_project(
    project_id: str,
    payload: RenameProjectRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ProjectResponse:
    """Change the project's label.

    Only the label. Everything that affects a figure goes through the
    conversation and the analysis pipeline, where it is validated and - once a
    proposal exists - forks a revision. A rename touches no number, so it does
    not fork, and it is safe on a finalised project.
    """
    project = await _load(session, project_id)
    name = (payload.name or "").strip()
    project.name = name or None
    await session.flush()
    await commit_before_response(session)

    await session.refresh(project, ["messages"])
    return _to_response(
        project,
        settings,
        has_proposal=await existing_proposal(session, project) is not None,
        customer=await _customer_of(session, project),
    )


@router.delete("/{project_id}")
async def delete_project(
    project_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Delete a project - only while nothing has been issued from it.

    `proposals` and `chat_messages` reference this row ON DELETE CASCADE, so a
    delete takes the proposal with it and the share link a customer is holding
    stops resolving. A draft nobody has seen is safe to remove; an issued
    document is not, and no flag turns that into a soft failure.

    A project that has been *revised* is also refused: deleting it would strip
    the revision of its parent and break the chain the revision list walks.
    """
    project = await _load(session, project_id)

    if await existing_proposal(session, project) is not None:
        raise DeletionRefusedError(
            "This project has an issued proposal, and its share link still resolves for the "
            "customer. Deleting it would break that link.",
            details={"reason": "issued"},
        )

    revision = await find_revision(session, project)
    if revision is not None:
        raise DeletionRefusedError(
            "This project has a revision built on it. Delete the revision first, or the "
            "revision history would lose its origin.",
            details={"reason": "revised", "revisionProjectId": revision.id},
        )

    await session.delete(project)
    await commit_before_response(session)
    logger.info("deleted project %s", project_id)
    return {"deleted": True}


@router.post("/{project_id}/customer", response_model=ProjectResponse)
async def assign_customer(
    project_id: str,
    payload: AssignCustomerRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ProjectResponse:
    """Link a project to a customer, or move it to a different one.

    Before finalisation this is an ordinary write. **After** finalisation it
    forks a revision, through the same `find_or_create_revision` a chat edit
    uses - because who a proposal is addressed to is part of the document, and
    the issued one has to keep saying what it said.

    No second mechanism, and no special case: the response is the project that
    now carries the change, exactly as a chat correction returns the revision it
    moved to.
    """
    project = await _load(session, project_id)
    customer = await customer_service.get_customer(session, payload.customerId)

    if project.customer_id != customer.id and await existing_proposal(session, project):
        # `recomputes=False`: the roof, the production and every financial
        # figure are unchanged by moving the proposal to a different recipient.
        # The revision is immediately finalisable, which is what makes
        # "re-issue this to the right person" a single step.
        project = await find_or_create_revision(session, project, recomputes=False)
        logger.info("customer change on a finalised project forked revision %s", project.id)

    forked = project.id != project_id
    project.customer_id = customer.id
    await session.flush()

    await activity.record(
        session,
        event_type=activity.PROJECT_CUSTOMER_ASSIGNED,
        actor="user",
        project_id=project.id,
        customer_id=customer.id,
        metadata={"displayName": customer.display_name, "forkedRevision": forked},
    )
    await commit_before_response(session)

    await session.refresh(project, ["messages"])
    return _to_response(
        project,
        settings,
        has_proposal=await existing_proposal(session, project) is not None,
        revision_project_id=None,
        customer=customer,
    )


@router.post("/{project_id}/chat", response_model=ChatResponse)
async def chat(
    project_id: str,
    payload: ChatRequest = Body(...),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ChatResponse:
    project = await _load(session, project_id)
    step = ProjectStep(project.current_step)

    # State is assembled *before* routing, and that ordering is the fix.
    #
    # It used to be built afterwards, which meant the router - and therefore the
    # model - could not see a single thing the customer had already said. Every
    # live prompt rendered "Known so far: nothing yet", and "make it the bigger
    # one" had to be resolved against nothing at all.
    prior_state = await _project_state(session, project)
    context = build_context(
        prior_state,
        settings=settings,
        recent=await _recent_turns(session, project),
    )

    routed = await route_message(
        payload.message, step=step, settings=settings, context=context
    )
    action = routed.action

    # A change to a project whose proposal has been issued forks a revision,
    # and the conversation moves with it. Routing happens first because only
    # the action says whether this message wants to mutate anything; a question
    # about a finalised proposal must not fork anything at all.
    notice = ""
    parent = project
    if action.wants_mutation:
        proposal = await existing_proposal(session, project)
        if proposal is not None:
            project = await find_or_create_revision(session, project)
            notice = revision_notice(parent, proposal.share_token, settings.web_base_url) + "\n\n"
            step = ProjectStep(project.current_step)
            await activity.record_best_effort(
                session,
                event_type=activity.PROJECT_REVISED,
                actor="user",
                project_id=project.id,
                customer_id=project.customer_id,
                metadata={"reason": action.topic.value},
            )

    # The raw message is preserved verbatim, on whichever project owns the turn.
    session.add(
        ChatMessage(project_id=project.id, role="user", content=payload.message, step=step.value)
    )

    # Re-read only when the turn forked a revision, because then `project` is a
    # different row to the one `prior_state` describes.
    state = prior_state if project is parent else await _project_state(session, project)
    # The deterministic answer, reworded by the model where that is safe.
    #
    # The paraphrase path existed since the conversational rebuild and was never
    # called, so the model shaped nothing a customer actually read - which is
    # most of why it seemed to add so little. It cannot introduce a figure: the
    # answer is composed first from the fact bundle, and any number the model
    # writes that is not already in those facts sends the deterministic wording
    # through unchanged.
    answer = None
    if action.is_question or action.kind is ActionKind.UNSUPPORTED_REQUEST:
        answer, _paraphrase_rejection = await answer_and_polish(
            action=action, project=state, settings=settings
        )
    outcome = handle_message(
        project=state,
        action=action,
        raw_text=payload.message,
        answer=answer,
        settings=settings,
    )

    _apply(project, outcome.updates)
    project.current_step = outcome.next_step.value

    recalculated = await _recalculate(session, project, outcome, settings)

    # The state machine decided; the I/O happens here, because it cannot.
    #
    # `send_confirmed` is set only when the immediately preceding assistant
    # message offered a send, so this line is unreachable from a bare "yes"
    # that was answering anything else.
    send_message = ""
    if outcome.send_confirmed:
        send_message = await _send_confirmed_proposal(session, project, settings)

    message = notice + outcome.assistant_message + send_message
    session.add(
        ChatMessage(
            project_id=project.id,
            role="assistant",
            content=message,
            step=project.current_step,
            parser_source=routed.interpretation.parser_source,
            payload_json={
                "action": action.model_dump(mode="json"),
                "resolution": {
                    "answerState": answer.state.value if answer else None,
                    "answerSource": answer.source.value if answer else None,
                    "helpTopic": answer.help_topic if answer else None,
                    "stepBefore": step.value,
                    "stepAfter": project.current_step,
                    "accepted": outcome.accepted,
                    "mutated": sorted(outcome.updates),
                    "pendingConfirmation": outcome.pending_confirmation,
                    "recalculated": recalculated,
                },
                "interpretation": routed.interpretation.to_payload(),
            },
        )
    )
    await session.flush()
    # Durable before the caller is told it happened: the UI fires run-analysis
    # the instant this returns, and the dependency's commit lands too late.
    await commit_before_response(session)

    logger.info(
        "chat | project=%s step=%s->%s kind=%s topic=%s provider=%s/%s reason=%s mutated=%s",
        project.id,
        step.value,
        project.current_step,
        action.kind.value,
        action.topic.value,
        routed.interpretation.attempted_provider,
        routed.interpretation.effective_provider,
        routed.interpretation.fallback_reason,
        sorted(outcome.updates),
    )

    return ChatResponse(
        projectId=project.id,
        currentStep=project.current_step,
        assistantMessage=message,
        accepted=outcome.accepted,
        parserSource=routed.interpretation.parser_source,
        progress=progress(outcome.next_step),
        readyForAnalysis=outcome.next_step is ProjectStep.ROOF_RECONSTRUCTION,
        analysisStatus=project.analysis_status,
        interpretation=routed.interpretation.to_payload(),
        revisionOfProjectId=project.revision_of_project_id,
        recalculated=recalculated,
    )


@router.post("/{project_id}/run-analysis")
async def run_project_analysis(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Roof, layout, PVGIS, FX and financials in one deterministic pass."""
    project = await _load(session, project_id)

    if project.monthly_consumption_kwh is None or project.selected_system_size_kwp is None:
        # The project exists; it is the *step* that is wrong. 404 would tell the
        # client to stop retrying a resource that is actually there and will
        # become valid as soon as intake finishes. `finalize` already answers
        # 409 for the same class of problem.
        raise InvalidStepTransitionError(
            "The project has no consumption or system size yet."
        )

    # Refused here if a batch already holds the claim, before any PVGIS call is
    # made - two concurrent requests must cost four probes, not eight. The claim
    # is committed inside, which also releases SQLite's write lock before the
    # slow part: holding it across four PVGIS calls and an FX lookup queues
    # every other writer behind it, and one that exceeds `busy_timeout` fails
    # with "database is locked" on an unrelated request.
    claim = await claim_analysis(session, project, status="running", settings=settings)

    try:
        # Is the imagery the roof was traced on the imagery being served? A
        # misplaced outline still produces a plausible area, a plausible panel
        # count and a plausible payback, so this stops the analysis rather than
        # annotating it.
        #
        # Inside the claim, so a request refused as a duplicate costs no imagery
        # fetch, and inside the `try`, so a failure is recorded against the run
        # and the claim is released like any other.
        await require_calibrated_imagery(settings)

        result = await run_analysis(
            monthly_consumption_kwh=project.monthly_consumption_kwh,
            system_size_kwp=project.selected_system_size_kwp,
            settings=settings,
            tariff_eur_per_kwh=project.electricity_tariff_eur_per_kwh,
            fx_service=ExchangeRateService(settings, cache=SqlExchangeRateCache(session)),
        )
    except AppError as error:
        # Without this the project would sit at "running" for ever, which
        # `validate_ready` reads as "still being recalculated" - so a permanent
        # failure would present as a temporary one. Record why, then re-raise so
        # the structured 502 still reaches the client. Never a 200 with a
        # degraded body: that is the fixture fallback in a new costume.
        logger.warning("analysis failed for project %s: %s", project.id, error.message)
        await fail_analysis(
            session, claim, code=error.code, message=error.message, details=error.details
        )
        # The code only. The message can carry an upstream provider's text, and
        # an audit row is not the place for a third party's prose.
        await activity.record_best_effort(
            session,
            event_type=activity.ANALYSIS_FAILED,
            project_id=project.id,
            customer_id=project.customer_id,
            metadata={"errorCode": error.code},
        )
        raise
    except Exception as error:  # pragma: no cover - defensive
        logger.exception("analysis failed for project %s", project.id)
        await fail_analysis(session, claim, code="ANALYSIS_FAILED", message=str(error))
        raise

    snapshot = serialise_analysis(result)
    await complete_analysis(
        session, claim, snapshot=snapshot, current_step=ProjectStep.PROPOSAL.value
    )
    await activity.record_best_effort(
        session,
        event_type=activity.ANALYSIS_COMPLETED,
        actor="user",
        project_id=project.id,
        customer_id=project.customer_id,
        metadata={
            "systemSizeKwp": result.layout.feasible_system_size_kwp,
            "annualProductionKwh": round(result.yield_result.total_annual_production_kwh),
            "panelCount": result.layout.placed_panel_count,
        },
    )
    await session.refresh(project)

    return {
        "projectId": project.id,
        "currentStep": project.current_step,
        "capacityWarning": result.capacity_warning,
        "analysis": snapshot,
        "progress": progress(ProjectStep.PROPOSAL),
    }
