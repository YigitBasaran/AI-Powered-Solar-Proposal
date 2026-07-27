"""Proposal finalisation, the public share route, PDF and view tracking."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import EmailMode, Settings, get_settings
from app.core.errors import NotFoundError, ValidationError
from app.db.session import get_session
from app.models.tables import Project
from app.services import proposal as proposal_service
from app.services.pdf import build_context, render_html, render_pdf

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
) -> dict:
    project = (
        await session.execute(select(Project).where(Project.id == project_id))
    ).scalar_one_or_none()
    if project is None:
        raise NotFoundError(f"Project {project_id} does not exist.")

    proposal = await proposal_service.finalise_proposal(session, project, settings=settings)

    return {
        "proposalId": proposal.id,
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
) -> dict:
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

    return {"stored": True, "bytes": len(content)}


# ---------------------------------------------------------------------------
# Public, read-only
# ---------------------------------------------------------------------------


@router.get("/proposals/{share_token}")
async def get_proposal(
    share_token: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
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
) -> dict:
    token = _validate_token(share_token)
    proposal = await proposal_service.load_by_token(session, token)

    count = await proposal_service.record_view(
        session,
        proposal,
        user_agent=request.headers.get("user-agent"),
        referrer=request.headers.get("referer"),
        client_ip=request.client.host if request.client else None,
    )

    # Notification must never be able to break the customer's page view.
    try:
        if settings.email_mode is EmailMode.CONSOLE:
            logger.info(
                "[Proposal Viewed]\n  Proposal: %s\n  Opened at: now\n  View count: %d",
                proposal.share_token[:12],
                count,
            )
        else:  # pragma: no cover - SMTP path is configuration-dependent
            logger.info("SMTP notification requested for %s", proposal.share_token[:12])
    except Exception:
        logger.exception("proposal view notification failed; continuing")

    return {"recorded": True, "viewCount": count}


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

    context = build_context(
        proposal.proposal_data_json,
        share_token=proposal.share_token,
        created_at=proposal.created_at.strftime("%d %B %Y"),
        settings=settings,
        layout_image_bytes=layout_bytes,
    )
    pdf_bytes = await render_pdf(render_html(context))

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'attachment; filename="solarvis-proposal-{proposal.share_token[:8]}.pdf"'
            )
        },
    )
