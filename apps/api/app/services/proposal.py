"""Proposal finalisation and retrieval.

A proposal is an **immutable snapshot**. At finalisation the complete analysis
- including the exchange rate, its date and its source - is serialised into one
JSON blob. The share page and the PDF renderer both read that blob and nothing
else.

That is what makes the numbers reproducible. Reopening a proposal months later,
after the market has moved, reproduces the figures that were quoted, because
nothing downstream ever re-reads a live rate.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import NotFoundError, ProposalIncompleteError
from app.models.tables import Project, Proposal, ProposalView
from app.services.conversation.invalidation import detect_staleness

logger = logging.getLogger("solarvis.proposal")

SHARE_TOKEN_BYTES = 24  # 192 bits of entropy: unguessable, and short enough to paste


def generate_share_token() -> str:
    return secrets.token_urlsafe(SHARE_TOKEN_BYTES)


def hash_ip(raw: str | None) -> str | None:
    """Store a hash, never the address itself."""
    if not raw:
        return None
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _require(snapshot: dict[str, Any], *path: str) -> Any:
    node: Any = snapshot
    for key in path:
        if not isinstance(node, dict) or key not in node:
            raise ProposalIncompleteError(
                f"The analysis is missing {'.'.join(path)}; run the analysis first."
            )
        node = node[key]
    return node


def validate_ready(project: Project) -> dict[str, Any]:
    """Refuse to treat a half-finished analysis as final.

    Extracted so the route can check readiness *before* doing anything else -
    generating an executive summary from a missing analysis would raise an
    unhelpful 500 where the caller deserves a clear 409.
    """
    snapshot = project.analysis_json
    if not snapshot:
        raise ProposalIncompleteError("This project has no completed analysis.")

    # Two independent guards, because either alone can be defeated.
    #
    # The status catches a recomputation that is in flight or that failed. The
    # signature catches a snapshot that is *shaped* like a finished analysis but
    # describes inputs the project no longer has - which is what a status flag
    # cannot see, and what would otherwise be frozen into an immutable document.
    if project.analysis_status in {"recalculating", "running"}:
        raise ProposalIncompleteError(
            "The analysis is still being recalculated. One moment, then try again."
        )
    if project.analysis_status == "stale":
        raise ProposalIncompleteError(
            "The analysis no longer matches this project's inputs and could not be "
            "recalculated. Re-run the analysis before finalising."
        )

    staleness = detect_staleness(
        snapshot=snapshot,
        monthly_consumption_kwh=project.monthly_consumption_kwh,
        selected_system_size_kwp=project.selected_system_size_kwp,
    )
    if staleness.is_stale:
        raise ProposalIncompleteError(
            "The analysis describes different inputs to the ones on this project "
            f"({', '.join(sorted(staleness.stale_inputs))}). Re-run the analysis first."
        )

    _require(snapshot, "layout")
    energy = _require(snapshot, "energy")
    financial = _require(snapshot, "financial")
    _require(snapshot, "exchangeRate")

    if not energy.get("facets"):
        raise ProposalIncompleteError("The analysis has no facet-level energy results.")
    if len(financial.get("cashFlow", [])) != 21:
        raise ProposalIncompleteError("The analysis has an incomplete cash flow.")
    return snapshot


async def existing_proposal(session: AsyncSession, project: Project) -> Proposal | None:
    """The proposal already issued for this project, if there is one."""
    result = await session.execute(
        select(Proposal)
        .where(Proposal.project_id == project.id)
        .order_by(Proposal.created_at.asc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def finalise_proposal(
    session: AsyncSession,
    project: Project,
    *,
    settings: Settings | None = None,
    ai_summary: str | None = None,
) -> Proposal:
    """Persist an immutable proposal from a completed analysis.

    Finalising a project that already has a proposal returns the existing one.
    A customer who double-clicks "Create proposal" must not end up holding two
    links to two documents: the view counts would split, and the two snapshots
    could later diverge - which is precisely the immutability guarantee this
    module exists to provide.
    """
    settings = settings or get_settings()

    already = await existing_proposal(session, project)
    if already is not None:
        logger.info(
            "project %s already has proposal %s; returning it unchanged",
            project.id,
            already.share_token[:8],
        )
        return already

    snapshot = validate_ready(project)
    layout = snapshot["layout"]
    energy = snapshot["energy"]
    financial = snapshot["financial"]
    fx = snapshot["exchangeRate"]

    token = generate_share_token()
    proposal = Proposal(
        project_id=project.id,
        share_token=token,
        requested_system_size_kwp=layout["requestedSystemSizeKwp"],
        feasible_system_size_kwp=layout["feasibleSystemSizeKwp"],
        requested_panel_count=layout["requestedPanelCount"],
        panel_count=layout["placedPanelCount"],
        annual_production_kwh=energy["totalAnnualProductionKwh"],
        annual_savings_eur=financial["annualSavingsEur"],
        original_capex_usd=financial["originalCapex"]["amount"],
        converted_capex_eur=financial["convertedCapex"]["amount"],
        exchange_rate=fx["rate"],
        exchange_rate_date=datetime.fromisoformat(fx["rateDate"]).date(),
        exchange_rate_source=fx["retrievalSource"],
        exchange_rate_provider=fx["dataProvider"],
        payback_years=financial["simplePaybackYears"],
        proposal_data_json={
            **snapshot,
            "meta": {
                "projectId": project.id,
                "rawLocationInput": project.raw_location_input,
                "location": {
                    "latitude": project.resolved_latitude,
                    "longitude": project.resolved_longitude,
                },
                "monthlyConsumptionKwh": project.monthly_consumption_kwh,
                "annualConsumptionKwh": (project.monthly_consumption_kwh or 0) * 12,
                "finalisedAt": datetime.now(UTC).isoformat(),
            },
        },
        ai_summary=ai_summary,
        capacity_warning=layout.get("capacityWarning"),
    )
    session.add(proposal)
    await session.flush()

    project.current_step = "completed"
    await session.flush()

    logger.info(
        "proposal finalised %s for project %s (%d panels, fx %s %s)",
        proposal.share_token[:8],
        project.id,
        proposal.panel_count,
        fx["rate"],
        fx["retrievalSource"],
    )
    return proposal


async def load_by_token(session: AsyncSession, token: str) -> Proposal:
    proposal = (
        await session.execute(select(Proposal).where(Proposal.share_token == token))
    ).scalar_one_or_none()
    if proposal is None:
        raise NotFoundError("That proposal link is not valid.")
    return proposal


async def record_view(
    session: AsyncSession,
    proposal: Proposal,
    *,
    user_agent: str | None,
    referrer: str | None,
    client_ip: str | None,
) -> int:
    session.add(
        ProposalView(
            proposal_id=proposal.id,
            user_agent=(user_agent or "")[:512] or None,
            referrer=(referrer or "")[:512] or None,
            ip_hash=hash_ip(client_ip),
        )
    )
    await session.flush()

    count = (
        await session.execute(
            select(func.count())
            .select_from(ProposalView)
            .where(ProposalView.proposal_id == proposal.id)
        )
    ).scalar_one()
    return int(count)


async def view_stats(session: AsyncSession, proposal: Proposal) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(func.count(), func.max(ProposalView.opened_at))
            .select_from(ProposalView)
            .where(ProposalView.proposal_id == proposal.id)
        )
    ).one()
    count, last = rows
    return {
        "viewCount": int(count or 0),
        "lastOpenedAt": last.isoformat() if last else None,
    }


def public_payload(proposal: Proposal, stats: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read-only projection served to the share page and the PDF renderer.

    Both consume exactly this, which is why they cannot disagree: there is one
    set of stored values and no recomputation on either path.
    """
    return {
        "shareToken": proposal.share_token,
        "createdAt": proposal.created_at.isoformat(),
        "capacityWarning": proposal.capacity_warning,
        "aiSummary": proposal.ai_summary,
        "layoutSnapshotUrl": (
            f"/api/v1/proposals/{proposal.share_token}/layout-snapshot"
            if proposal.layout_snapshot_path
            else None
        ),
        **proposal.proposal_data_json,
        **({"views": stats} if stats else {}),
    }


__all__ = [
    "Proposal",
    "existing_proposal",
    "finalise_proposal",
    "generate_share_token",
    "load_by_token",
    "public_payload",
    "record_view",
    "validate_ready",
    "view_stats",
]
