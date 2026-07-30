"""Project and chat endpoints.

Route functions stay thin: they translate HTTP to domain calls and back. All
workflow rules live in `services/workflow.py` and all analysis in
`services/analysis.py`, so business logic is never duplicated into a handler.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import (
    AnalysisInProgressError,
    AnalysisSupersededError,
    AppError,
    InvalidStepTransitionError,
    NotFoundError,
)
from app.db.session import commit_before_response, get_session
from app.domain.models import ProjectStep
from app.integrations.exchange_rates import ExchangeRateService, SqlExchangeRateCache
from app.models.tables import ChatMessage, Project, Proposal

# Imported as a module, not by name: the recompute functions are called
# through it so a failing recomputation can actually be exercised, and so the
# route always reaches the current definition rather than one bound at import.
from app.services import analysis as analysis_service
from app.services.analysis import run_analysis, serialise_analysis
from app.services.analysis_claim import (
    claim_analysis,
    complete_analysis,
    fail_analysis,
    release_stale_claim,
)
from app.services.conversation.actions import ActionKind
from app.services.conversation.answers import answer_question
from app.services.conversation.context import RECENT_TURN_LIMIT, Turn, build_context
from app.services.conversation.router import route_message
from app.services.imagery import require_calibrated_imagery
from app.services.proposal import existing_proposal
from app.services.revisions import find_or_create_revision, find_revision, revision_notice
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
        "analysis_json",
        "analysis_status",
    }
)


class CreateProjectResponse(BaseModel):
    projectId: str
    currentStep: str
    assistantMessage: str
    progress: list[dict[str, Any]]


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
        if outcome.changed_inputs == frozenset({"monthly_consumption_kwh"}):
            updated = analysis_service.recompute_for_consumption(
                snapshot=snapshot,
                monthly_consumption_kwh=float(project.monthly_consumption_kwh or 0.0),
                settings=settings,
            )
        else:
            updated = await analysis_service.recompute_for_system_size(
                snapshot=snapshot,
                system_size_kwp=float(project.selected_system_size_kwp or 0.0),
                monthly_consumption_kwh=float(project.monthly_consumption_kwh or 0.0),
                settings=settings,
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
) -> ProjectResponse:
    step = ProjectStep(project.current_step)
    monthly = project.monthly_consumption_kwh
    size = project.selected_system_size_kwp
    return ProjectResponse(
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
                "createdAt": m.created_at.isoformat(),
            }
            for m in project.messages
        ],
        analysis=project.analysis_json,
    )


@router.post("", response_model=CreateProjectResponse, status_code=201)
async def create_project(
    session: AsyncSession = Depends(get_session),
) -> CreateProjectResponse:
    project = Project(current_step=ProjectStep.LOCATION.value)
    session.add(project)
    await session.flush()

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

    # The raw message is preserved verbatim, on whichever project owns the turn.
    session.add(
        ChatMessage(project_id=project.id, role="user", content=payload.message, step=step.value)
    )

    # Re-read only when the turn forked a revision, because then `project` is a
    # different row to the one `prior_state` describes.
    state = prior_state if project is parent else await _project_state(session, project)
    answer = (
        answer_question(action=action, project=state, settings=settings)
        if action.is_question or action.kind is ActionKind.UNSUPPORTED_REQUEST
        else None
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

    message = notice + outcome.assistant_message
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
        raise
    except Exception as error:  # pragma: no cover - defensive
        logger.exception("analysis failed for project %s", project.id)
        await fail_analysis(session, claim, code="ANALYSIS_FAILED", message=str(error))
        raise

    snapshot = serialise_analysis(result)
    await complete_analysis(
        session, claim, snapshot=snapshot, current_step=ProjectStep.PROPOSAL.value
    )
    await session.refresh(project)

    return {
        "projectId": project.id,
        "currentStep": project.current_step,
        "capacityWarning": result.capacity_warning,
        "analysis": snapshot,
        "progress": progress(ProjectStep.PROPOSAL),
    }
