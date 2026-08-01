"""Sending a finalised proposal to its customer.

These routes take the **internal proposal id**, never the public share token.
The token is the thing customers hold; a send endpoint reachable with it would
let anyone forwarded a proposal link cause mail to be sent from this system.

Nothing here creates or modifies a proposal. Sending is a separate act from
issuing, so a failed send leaves a perfectly valid proposal and a working
public link - which is exactly the fallback the UI offers when it fails.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import (
    DeliveryNotFoundError,
    NotFoundError,
    SendConfirmationRequiredError,
)
from app.db.session import commit_before_response, get_session
from app.models.tables import Proposal, ProposalDelivery
from app.services import proposal_email

logger = logging.getLogger("solarvis.api")

router = APIRouter(prefix="/proposals", tags=["deliveries"])


class SendProposalRequest(BaseModel):
    """`confirm` is required and must be literally `true`.

    Not a query flag, not a default, not inferred from the request having been
    made at all. Sending is irreversible and lands in someone else's inbox, so
    the intent is carried explicitly in the body - which means a preview
    request, a status poll or a retried GET can never satisfy it.
    """

    confirm: bool = False
    #: Distinguishes a deliberate resend from a duplicate of one intent. Absent,
    #: every retry of the same send computes the same idempotency key and the
    #: database collapses them into one row.
    resendNonce: str | None = None


async def _load_proposal(session: AsyncSession, proposal_id: str) -> Proposal:
    proposal = (
        await session.execute(select(Proposal).where(Proposal.id == proposal_id))
    ).scalar_one_or_none()
    if proposal is None:
        raise NotFoundError(f"Proposal {proposal_id} does not exist.")
    return proposal


@router.get("/{proposal_id}/email-preview")
async def email_preview(
    proposal_id: str,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Exactly what would be sent. **Sends nothing, and writes nothing.**

    Rendered by the same `compose` the send route uses, from the same stored
    values - so what the operator approves is the message that goes out, rather
    than a preview that merely resembles it.
    """
    proposal = await _load_proposal(session, proposal_id)
    return {"preview": proposal_email.preview(proposal, settings)}


@router.get("/{proposal_id}/deliveries")
async def list_deliveries(
    proposal_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    proposal = await _load_proposal(session, proposal_id)
    rows = await proposal_email.history(session, proposal)
    return {"deliveries": [proposal_email.serialise(row) for row in rows]}


@router.post("/{proposal_id}/send")
async def send_proposal(
    proposal_id: str,
    payload: SendProposalRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    proposal = await _load_proposal(session, proposal_id)

    if payload.confirm is not True:
        raise SendConfirmationRequiredError(
            "This proposal will be emailed to the customer. Confirm to send it."
        )

    delivery = await proposal_email.send(
        session,
        proposal,
        settings=settings,
        nonce=payload.resendNonce or "",
        # Passed in rather than called inside the service: the commit *before*
        # the provider call is what makes an ambiguous timeout recoverable, and
        # the route is what owns the transaction boundary.
        commit=commit_before_response,
    )
    await commit_before_response(session)

    return {"delivery": proposal_email.serialise(delivery)}


@router.post("/{proposal_id}/deliveries/{delivery_id}/retry")
async def retry_delivery(
    proposal_id: str,
    delivery_id: str,
    payload: SendProposalRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Retry a failed delivery. Never creates a proposal, never re-finalises.

    Confirmation is required again. A retry is still a send, and the reason the
    first one failed may be that it was going to the wrong person.
    """
    proposal = await _load_proposal(session, proposal_id)

    delivery = (
        await session.execute(
            select(ProposalDelivery).where(
                ProposalDelivery.id == delivery_id,
                ProposalDelivery.proposal_id == proposal.id,
            )
        )
    ).scalar_one_or_none()
    if delivery is None:
        raise DeliveryNotFoundError(f"Delivery {delivery_id} does not exist for this proposal.")

    if payload.confirm is not True:
        raise SendConfirmationRequiredError(
            "Retrying will email this proposal to the customer. Confirm to send it."
        )

    retried = await proposal_email.send(
        session,
        proposal,
        settings=settings,
        nonce=payload.resendNonce or "",
        commit=commit_before_response,
    )
    await commit_before_response(session)

    return {"delivery": proposal_email.serialise(retried)}
