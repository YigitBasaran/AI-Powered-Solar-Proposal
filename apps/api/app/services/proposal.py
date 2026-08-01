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
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import NotFoundError, ProposalIncompleteError
from app.domain.models import DataSource
from app.integrations.pvgis import classify_endpoint
from app.models.tables import Customer, Project, Proposal, ProposalView, _utcnow, iso_utc
from app.services.conversation.invalidation import detect_staleness
from app.services.revisions import revision_number_for

logger = logging.getLogger("solarvis.proposal")

SHARE_TOKEN_BYTES = 24  # 192 bits of entropy: unguessable, and short enough to paste


def generate_share_token() -> str:
    return secrets.token_urlsafe(SHARE_TOKEN_BYTES)


#: User agents that are not a person looking at a proposal.
#:
#: Link unfurlers are the ones that actually matter here: pasting a share link
#: into a chat app fetches it immediately, and without this the operator sees
#: "viewed" before the customer has opened anything at all - the single most
#: misleading thing this feature could report.
CRAWLER_AGENTS = re.compile(
    r"bot|crawler|spider|slurp|preview|fetch|curl|wget|headless|monitor|"
    r"whatsapp|telegram|slackbot|discord|facebookexternalhit|twitterbot|linkedinbot",
    re.IGNORECASE,
)


def hash_ip(raw: str | None, *, salt: str = "") -> str | None:
    """Store a hash, never the address itself.

    Salted, because an unsalted digest of an IP address is not anonymisation:
    the entire IPv4 space is four billion values, so a rainbow table over it is
    trivial to build and the hash identifies the address exactly. The salt is
    per-deployment, so a leaked column cannot be resolved without it.

    An empty salt reproduces the previous unsalted behaviour, which is what
    existing rows contain.
    """
    if not raw:
        return None
    return hashlib.sha256(f"{salt}{raw}".encode()).hexdigest()[:32]


def is_crawler(user_agent: str | None) -> bool:
    return bool(user_agent and CRAWLER_AGENTS.search(user_agent))


@dataclass(frozen=True)
class ViewOutcome:
    """What `record_view` decided, and why.

    `counted` is the interesting field: a view that was suppressed as a repeat
    or as a crawler still returns the current total, so the customer's page
    behaves identically, but nothing downstream treats it as a fresh opening.
    """

    counted: bool
    view_count: int
    view_id: str | None
    reason: str | None = None


def build_reference(share_token: str, revision_number: int) -> str:
    """The human reference, e.g. `SOL-A1B2C3-R2`.

    For quoting in an email subject or over the phone. Deliberately *not* an
    identifier: it is derived from a prefix of the token, so it is not
    guaranteed unique and nothing resolves anything by it. The token stays the
    only way to reach a proposal.
    """
    return f"SOL-{share_token[:6].upper()}-R{revision_number}"


def customer_snapshot(customer: Customer | None) -> dict[str, Any] | None:
    """Freeze who this proposal is for, at the moment it is issued.

    A snapshot rather than a join, for exactly the reason the figures are one.
    A corrected surname or a new address six months later must not restate a
    document that has already been sent.
    """
    if customer is None:
        return None
    return {
        "customerId": customer.id,
        "displayName": customer.display_name,
        "firstName": customer.first_name,
        "lastName": customer.last_name,
        "email": customer.email,
        "phone": customer.phone,
        "companyName": customer.company_name,
        "address": customer.address,
        "capturedAt": datetime.now(UTC).isoformat(),
    }


def public_customer(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    """The only customer data an unauthenticated caller ever sees.

    An **allow-list**, not a redaction: the projection names the one field it
    publishes, so a column added to the snapshot later cannot leak by default.
    The proposal page is reachable by anyone holding the link, and the email
    address is the one field on that record that is worth harvesting.
    """
    if not snapshot:
        return None
    name = snapshot.get("displayName")
    return {"displayName": name} if isinstance(name, str) and name else None


def _require(snapshot: dict[str, Any], *path: str) -> Any:
    node: Any = snapshot
    for key in path:
        if not isinstance(node, dict) or key not in node:
            raise ProposalIncompleteError(
                f"The analysis is missing {'.'.join(path)}; run the analysis first."
            )
        node = node[key]
    return node


def _require_trusted_provenance(energy: dict[str, Any], settings: Settings) -> None:
    """A proposal states a live observation, or it is not issued.

    Two ways this refuses. A snapshot from before PVGIS became mandatory has no
    `energy.pvgis` block at all - absent provenance is untrusted provenance, not
    an exemption, so such a project needs a fresh analysis before it can be
    finalised. (Proposals *already* issued are unaffected: they render from
    their own frozen snapshot and never come back through here.)

    And a snapshot produced against a replay endpoint says so. Labelling it is
    not enough - the label would sit in a document that reads as a live
    observation to everyone but its author - so the document is refused. The
    only escape is `ALLOW_REPLAY_PROPOSALS`, which the settings themselves
    refuse outside a test environment.
    """
    block = energy.get("pvgis")
    if not isinstance(block, dict) or not block.get("probes"):
        raise ProposalIncompleteError(
            "This analysis predates live PVGIS provenance, so its production figures "
            "cannot be attributed to a retrieval. Re-run the analysis before finalising."
        )

    if settings.allow_replay_proposals:
        return

    trust = classify_endpoint(str(block.get("endpoint") or ""))
    if str(block.get("source")) != DataSource.LIVE.value or not trust.is_trusted:
        raise ProposalIncompleteError(
            "These production figures came from a replayed capture, not a live PVGIS "
            f"retrieval ({trust.reason or 'endpoint not trusted'}). A proposal must state "
            "a live observation."
        )


def validate_ready(project: Project, settings: Settings | None = None) -> dict[str, Any]:
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
    if project.analysis_status == "failed":
        # Defence in depth, per this module's own policy: a failed analysis has
        # no snapshot, so the guard above already refused. It is stated anyway
        # because the two conditions are independent, and the day they diverge
        # is the day this matters.
        reason = (project.analysis_error_json or {}).get("message")
        raise ProposalIncompleteError(
            f"The last analysis failed and produced no figures{f': {reason}' if reason else ''}. "
            "Re-run the analysis before finalising."
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

    _require_trusted_provenance(energy, settings or get_settings())

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
    # Derived from the project chain, then frozen. There is one revision
    # mechanism in this application - a forked project - and this reads it
    # rather than introducing a counter that could disagree with it.
    revision_number = await revision_number_for(session, project)
    customer = (
        await session.get(Customer, project.customer_id)
        if project.customer_id is not None
        else None
    )

    proposal = Proposal(
        project_id=project.id,
        share_token=token,
        revision_number=revision_number,
        reference=build_reference(token, revision_number),
        customer_snapshot_json=customer_snapshot(customer),
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


async def _view_count(session: AsyncSession, proposal: Proposal) -> int:
    count = (
        await session.execute(
            select(func.count())
            .select_from(ProposalView)
            .where(ProposalView.proposal_id == proposal.id)
        )
    ).scalar_one()
    return int(count)


async def record_view(
    session: AsyncSession,
    proposal: Proposal,
    *,
    user_agent: str | None,
    referrer: str | None,
    client_ip: str | None,
    settings: Settings | None = None,
) -> ViewOutcome:
    """Count a customer opening their proposal - once per visit, not per render.

    Two things are deliberately *not* counted.

    A **repeat within the dedup window** from the same reader. The page fetches
    on every mount, so scrolling back, refreshing, or reopening a tab all hit
    this route; counting each one turns one person reading a proposal into
    "viewed 7 times", which is the number an operator uses to decide whether to
    follow up.

    A **crawler**. Link unfurlers matter most: pasting the share link into a
    chat fetches it at once, so without this the proposal reads as viewed
    before the customer has seen anything.

    Both are suppressed by *not inserting a row*, so the count, the first-viewed
    and last-viewed timestamps all stay consistent with each other - there is
    one source of truth and no flag to keep in step with it.
    """
    settings = settings or get_settings()

    if is_crawler(user_agent):
        logger.info("proposal %s fetched by a crawler; not counted", proposal.share_token[:8])
        return ViewOutcome(
            counted=False,
            view_count=await _view_count(session, proposal),
            view_id=None,
            reason="crawler",
        )

    ip_hash = hash_ip(client_ip, salt=settings.view_hash_salt)
    agent = (user_agent or "")[:512] or None

    # `is_()` rather than `==` for the null cases: in SQL, `NULL = NULL` is not
    # true, so a reader behind a proxy that strips the address would never match
    # itself and every refresh would count again.
    cutoff = _utcnow() - timedelta(minutes=settings.proposal_view_dedup_minutes)
    same_reader = (
        ProposalView.ip_hash.is_(None) if ip_hash is None else ProposalView.ip_hash == ip_hash,
        ProposalView.user_agent.is_(None) if agent is None else ProposalView.user_agent == agent,
    )
    recent = (
        await session.execute(
            select(ProposalView.id)
            .where(
                ProposalView.proposal_id == proposal.id,
                *same_reader,
                # Compared naive, because that is what SQLite stores.
                ProposalView.opened_at >= cutoff.replace(tzinfo=None),
            )
            .limit(1)
        )
    ).scalar_one_or_none()

    if recent is not None:
        return ViewOutcome(
            counted=False,
            view_count=await _view_count(session, proposal),
            view_id=None,
            reason="duplicate",
        )

    view = ProposalView(
        proposal_id=proposal.id,
        user_agent=agent,
        referrer=(referrer or "")[:512] or None,
        ip_hash=ip_hash,
    )
    session.add(view)
    await session.flush()

    return ViewOutcome(
        counted=True, view_count=await _view_count(session, proposal), view_id=view.id
    )


async def view_stats(session: AsyncSession, proposal: Proposal) -> dict[str, Any]:
    """Count, first opened and last opened - all derived, nothing denormalised.

    `firstOpenedAt` is min(opened_at) rather than a stored column, so it cannot
    drift from the rows it summarises. It answers a different question to the
    last one: "have they looked at all" versus "are they still looking".
    """
    rows = (
        await session.execute(
            select(
                func.count(),
                func.min(ProposalView.opened_at),
                func.max(ProposalView.opened_at),
            )
            .select_from(ProposalView)
            .where(ProposalView.proposal_id == proposal.id)
        )
    ).one()
    count, first, last = rows
    return {
        "viewCount": int(count or 0),
        "firstOpenedAt": iso_utc(first),
        "lastOpenedAt": iso_utc(last),
    }


def public_payload(proposal: Proposal, stats: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read-only projection served to the share page and the PDF renderer.

    Both consume exactly this, which is why they cannot disagree: there is one
    set of stored values and no recomputation on either path.
    """
    return {
        "shareToken": proposal.share_token,
        "createdAt": iso_utc(proposal.created_at),
        "capacityWarning": proposal.capacity_warning,
        "aiSummary": proposal.ai_summary,
        "layoutSnapshotUrl": (
            f"/api/v1/proposals/{proposal.share_token}/layout-snapshot"
            if proposal.layout_snapshot_path
            else None
        ),
        **proposal.proposal_data_json,
        # After the snapshot spread, deliberately. These keys are the
        # projection's own guarantees, and a snapshot that happened to carry a
        # key of the same name must not be able to override them - least of all
        # `customer`, which is the allow-list that keeps the email address off
        # an unauthenticated route.
        "revisionNumber": proposal.revision_number or 1,
        "reference": proposal.reference,
        "customer": public_customer(proposal.customer_snapshot_json),
        **({"views": stats} if stats else {}),
    }


__all__ = [
    "Proposal",
    "build_reference",
    "customer_snapshot",
    "existing_proposal",
    "finalise_proposal",
    "generate_share_token",
    "load_by_token",
    "public_customer",
    "public_payload",
    "record_view",
    "validate_ready",
    "view_stats",
]
