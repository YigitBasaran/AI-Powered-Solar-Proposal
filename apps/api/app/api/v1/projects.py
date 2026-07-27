"""Project and chat endpoints.

Route functions stay thin: they translate HTTP to domain calls and back. All
workflow rules live in `services/workflow.py` and all analysis in
`services/analysis.py`, so business logic is never duplicated into a handler.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import NotFoundError
from app.db.session import get_session
from app.domain.models import ProjectStep
from app.integrations.exchange_rates import ExchangeRateService, SqlExchangeRateCache
from app.models.tables import ChatMessage, Project
from app.services.analysis import run_analysis, serialise_analysis
from app.services.chat import parse_user_message
from app.services.workflow import handle_message, initial_assistant_message, progress

logger = logging.getLogger("solarvis.api")

router = APIRouter(prefix="/projects", tags=["projects"])

MAX_MESSAGE_LENGTH = 2000


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
    parserSource: str
    progress: list[dict[str, Any]]
    readyForAnalysis: bool


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
    progress: list[dict[str, Any]]
    messages: list[dict[str, Any]]
    analysis: dict[str, Any] | None


async def _load(session: AsyncSession, project_id: str) -> Project:
    project = (
        await session.execute(select(Project).where(Project.id == project_id))
    ).scalar_one_or_none()
    if project is None:
        raise NotFoundError(f"Project {project_id} does not exist.")
    return project


def _to_response(project: Project, settings: Settings) -> ProjectResponse:
    step = ProjectStep(project.current_step)
    monthly = project.monthly_consumption_kwh
    size = project.selected_system_size_kwp
    return ProjectResponse(
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
    return _to_response(project, settings)


@router.post("/{project_id}/chat", response_model=ChatResponse)
async def chat(
    project_id: str,
    payload: ChatRequest = Body(...),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ChatResponse:
    project = await _load(session, project_id)
    step = ProjectStep(project.current_step)

    # The raw message is preserved verbatim before anything interprets it.
    session.add(
        ChatMessage(project_id=project.id, role="user", content=payload.message, step=step.value)
    )

    parsed, parser_source = await parse_user_message(payload.message, step=step, settings=settings)
    outcome = handle_message(
        current_step=step, parsed=parsed, raw_text=payload.message, settings=settings
    )

    for key, value in outcome.updates.items():
        setattr(project, key, value)
    project.current_step = outcome.next_step.value

    message = outcome.assistant_message or (
        "I can answer questions about the roof, production or financials once the analysis has run."
    )
    session.add(
        ChatMessage(
            project_id=project.id,
            role="assistant",
            content=message,
            step=project.current_step,
            parser_source=parser_source,
            payload_json=parsed.model_dump(mode="json"),
        )
    )
    await session.flush()

    return ChatResponse(
        projectId=project.id,
        currentStep=project.current_step,
        assistantMessage=message,
        accepted=outcome.accepted,
        parserSource=parser_source,
        progress=progress(outcome.next_step),
        readyForAnalysis=outcome.next_step is ProjectStep.ROOF_RECONSTRUCTION,
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
        raise NotFoundError("The project has no consumption or system size yet.")

    project.analysis_status = "running"
    await session.flush()

    result = await run_analysis(
        monthly_consumption_kwh=project.monthly_consumption_kwh,
        system_size_kwp=project.selected_system_size_kwp,
        settings=settings,
        fx_service=ExchangeRateService(settings, cache=SqlExchangeRateCache(session)),
    )

    snapshot = serialise_analysis(result)
    project.analysis_json = snapshot
    project.analysis_status = "complete"
    project.current_step = ProjectStep.PROPOSAL.value
    await session.flush()

    return {
        "projectId": project.id,
        "currentStep": project.current_step,
        "capacityWarning": result.capacity_warning,
        "analysis": snapshot,
        "progress": progress(ProjectStep.PROPOSAL),
    }
