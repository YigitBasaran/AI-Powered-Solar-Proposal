"""Proposal finalisation, the public share route, PDF and view tracking."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import NotFoundError, ValidationError
from app.db.session import commit_before_response, get_session
from app.models.tables import Project
from app.services import activity, proposal_email
from app.services import proposal as proposal_service
from app.services.pdf import build_context, render_html, render_pdf
from app.services.summary import generate_summary

logger = logging.getLogger("solarvis.proposals")

router = APIRouter(tags=["proposals"])

MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


def _validate_token(token: str) -> str:
    if not SAFE_TOKEN.match(token):
        raise NotFoundError("That proposal link is not valid.")
    return token


def _storage_dir(settings: Settings) -> Path:
    path = Path(__file__).resolve().parents[3] / "storage" / "layouts"
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# Finalisation
# ---------------------------------------------------------------------------


@router.post("/projects/{project_id}/finalize")
async def finalize(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    project = (
        await session.execute(select(Project).where(Project.id == project_id))
    ).scalar_one_or_none()
    if project is None:
        raise NotFoundError(f"Project {project_id} does not exist.")

    # An already-finalised project returns its existing proposal, before any
    # work is done. Regenerating the summary would spend an LLM call on a
    # document that is not going to change.
    existing = await proposal_service.existing_proposal(session, project)
    if existing is not None:
        return _finalize_payload(existing, settings, summary_source="existing")

    # Readiness first: generating a summary from a missing analysis would raise
    # an unhelpful 500 where the caller deserves a clear 409.
    snapshot = proposal_service.validate_ready(project)

    # Prose is generated from the analysis that is about to be frozen, and is
    # validated against those exact values before it is stored.
    summary, summary_source = await generate_summary(snapshot, settings)

    proposal = await proposal_service.finalise_proposal(
        session, project, settings=settings, ai_summary=summary
    )

    # Atomic with the finalisation. Losing "this proposal was issued" while
    # keeping the proposal would misrepresent the record in the one place an
    # operator goes to find out what was sent and when.
    await activity.record(
        session,
        event_type=activity.PROPOSAL_FINALISED,
        actor="user",
        project_id=project.id,
        customer_id=project.customer_id,
        proposal_id=proposal.id,
        metadata={
            "revisionNumber": proposal.revision_number,
            "reference": proposal.reference,
            "systemSizeKwp": proposal.feasible_system_size_kwp,
            "annualProductionKwh": proposal.annual_production_kwh,
        },
    )

    # The share link is handed out in this response; the row it points at has
    # to exist before the customer can follow it.
    await commit_before_response(session)

    return _finalize_payload(proposal, settings, summary_source=summary_source)


def _finalize_payload(
    proposal: proposal_service.Proposal, settings: Settings, *, summary_source: str
) -> dict[str, Any]:
    return {
        "proposalId": proposal.id,
        "summarySource": summary_source,
        "shareToken": proposal.share_token,
        "shareUrl": f"{settings.web_base_url}/proposal/{proposal.share_token}",
        "pdfUrl": f"/api/v1/proposals/{proposal.share_token}/pdf",
        "capacityWarning": proposal.capacity_warning,
    }


@router.post("/projects/{project_id}/layout-snapshot")
async def upload_layout_snapshot(
    project_id: str,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Store the exported Konva stage for the most recent proposal."""
    content = await file.read()
    if len(content) > MAX_SNAPSHOT_BYTES:
        raise ValidationError("Layout snapshot is too large.")
    if not content.startswith(PNG_MAGIC):
        raise ValidationError("Layout snapshot must be a PNG image.")

    proposal = (
        await session.execute(
            select(proposal_service.Proposal)
            .where(proposal_service.Proposal.project_id == project_id)
            .order_by(proposal_service.Proposal.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if proposal is None:
        raise NotFoundError("No proposal exists for this project yet.")

    # Filename is derived from the token, never from client-supplied text.
    destination = _storage_dir(settings) / f"{proposal.share_token}.png"
    destination.write_bytes(content)
    proposal.layout_snapshot_path = str(destination)
    await session.flush()
    await commit_before_response(session)

    return {"stored": True, "bytes": len(content)}


# ---------------------------------------------------------------------------
# Public, read-only
# ---------------------------------------------------------------------------


@router.get("/proposals/{share_token}")
async def get_proposal(
    share_token: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    token = _validate_token(share_token)
    proposal = await proposal_service.load_by_token(session, token)
    stats = await proposal_service.view_stats(session, proposal)
    return proposal_service.public_payload(proposal, stats)


@router.post("/proposals/{share_token}/view")
async def record_proposal_view(
    share_token: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    token = _validate_token(share_token)
    proposal = await proposal_service.load_by_token(session, token)

    outcome = await proposal_service.record_view(
        session,
        proposal,
        user_agent=request.headers.get("user-agent"),
        referrer=request.headers.get("referer"),
        client_ip=request.client.host if request.client else None,
        settings=settings,
    )

    if outcome.counted:
        # Best-effort, and the actor is the customer - the one participant who
        # is not the operator. Carries the count and nothing else: no user
        # agent, no referrer, and above all no address, hashed or otherwise.
        await activity.record_best_effort(
            session,
            event_type=activity.PROPOSAL_VIEWED,
            actor="customer",
            project_id=proposal.project_id,
            proposal_id=proposal.id,
            metadata={"viewCount": outcome.view_count},
        )

    # The returned count must be readable by the next request that asks.
    await commit_before_response(session)

    # The brief's bonus: notify on open. Only for a counted view, so a refresh
    # or a link unfurler cannot page the salesperson - and wrapped, because the
    # customer is looking at the page right now and nothing they see may depend
    # on an outbound email succeeding.
    if outcome.counted and outcome.view_id:
        try:
            await proposal_email.notify_opened(
                session,
                proposal,
                view_id=outcome.view_id,
                view_count=outcome.view_count,
                settings=settings,
            )
            await commit_before_response(session)
        except Exception:
            logger.exception("proposal view notification failed; continuing")

    return {
        "recorded": True,
        "viewCount": outcome.view_count,
        # Stated rather than implied, so a client can tell "we counted you" from
        # "you were already counted a minute ago" without guessing from a total
        # that did not move.
        "counted": outcome.counted,
    }


@router.get("/proposals/{share_token}/layout-snapshot")
async def get_layout_snapshot(
    share_token: str,
    session: AsyncSession = Depends(get_session),
) -> Response:
    token = _validate_token(share_token)
    proposal = await proposal_service.load_by_token(session, token)
    if not proposal.layout_snapshot_path:
        raise NotFoundError("This proposal has no layout snapshot.")
    path = Path(proposal.layout_snapshot_path)
    if not path.is_file():
        raise NotFoundError("This proposal has no layout snapshot.")
    return Response(content=path.read_bytes(), media_type="image/png")


@router.get("/proposals/{share_token}/pdf")
async def get_proposal_pdf(
    share_token: str,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    token = _validate_token(share_token)
    proposal = await proposal_service.load_by_token(session, token)

    layout_bytes: bytes | None = None
    if proposal.layout_snapshot_path:
        path = Path(proposal.layout_snapshot_path)
        if path.is_file():
            layout_bytes = path.read_bytes()

    # The same projection the share page is served, which is what keeps the two
    # documents in agreement. The stored snapshot alone is *not* equivalent:
    # `aiSummary` lives on the proposal row rather than inside the JSON blob, so
    # rendering straight from `proposal_data_json` silently drops the executive
    # summary from every served PDF.
    context = build_context(
        proposal_service.public_payload(proposal),
        share_token=proposal.share_token,
        created_at=proposal.created_at.strftime("%d %B %Y"),
        settings=settings,
        layout_image_bytes=layout_bytes,
    )
    pdf_bytes = await render_pdf(render_html(context))

    # Recorded as its own event, deliberately *not* as a page view. A download
    # is a different act from opening the proposal, and folding it into the view
    # count would inflate the one number an operator uses to judge whether the
    # customer has actually looked at it.
    await activity.record_best_effort(
        session,
        event_type=activity.PROPOSAL_PDF_DOWNLOADED,
        actor="customer",
        project_id=proposal.project_id,
        proposal_id=proposal.id,
    )
    await commit_before_response(session)

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'attachment; filename="solarvis-proposal-{proposal.share_token[:8]}.pdf"'
            )
        },
    )
