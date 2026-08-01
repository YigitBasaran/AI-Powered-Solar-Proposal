"""Customer endpoints.

Thin, like the other routers: validation lives in `domain/customers.py` and
every rule in `services/customers.py`, so a handler here only translates HTTP.

A note on what is *not* here. There is no authentication anywhere in this
application, so these routes are exactly as open as `GET /projects/{id}` has
always been - protected by unguessable identifiers and nothing else. That is a
deliberate, documented limitation rather than an oversight; see
`docs/known-limitations.md`. Adding customer records does raise the stakes of
it, which is why the public proposal projection exposes a display name and
nothing else.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import commit_before_response, get_session
from app.models.tables import Project
from app.services import activity
from app.services import customers as customer_service

logger = logging.getLogger("solarvis.api")

router = APIRouter(prefix="/customers", tags=["customers"])


class CreateCustomerRequest(BaseModel):
    firstName: str = Field(min_length=1, max_length=200)
    lastName: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=400)
    phone: str | None = None
    companyName: str | None = None
    address: str | None = None
    displayName: str | None = None


class UpdateCustomerRequest(BaseModel):
    """Every field optional. Only keys actually present are applied.

    `exclude_unset` is what makes that work: a field left out is untouched,
    while a field sent as `null` clears it. Without the distinction there is no
    way to remove a phone number without also clearing everything else.
    """

    firstName: str | None = None
    lastName: str | None = None
    email: str | None = None
    phone: str | None = None
    companyName: str | None = None
    address: str | None = None
    displayName: str | None = None


#: Request field -> model column. Also the whitelist: a key absent from here
#: cannot be written by a request, whatever the body contains.
_UPDATABLE = {
    "firstName": "first_name",
    "lastName": "last_name",
    "email": "email",
    "phone": "phone",
    "companyName": "company_name",
    "address": "address",
    "displayName": "display_name",
}


@router.post("", status_code=201)
async def create_customer(
    payload: CreateCustomerRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    customer = await customer_service.create_customer(
        session,
        first_name=payload.firstName,
        last_name=payload.lastName,
        email=payload.email,
        phone=payload.phone,
        company_name=payload.companyName,
        address=payload.address,
        display_name=payload.displayName,
    )
    await activity.record(
        session,
        event_type=activity.CUSTOMER_CREATED,
        actor="user",
        customer_id=customer.id,
        metadata={"displayName": customer.display_name},
    )
    await commit_before_response(session)
    return {"customer": customer_service.serialise(customer)}


@router.get("")
async def list_customers(
    q: str | None = Query(default=None, max_length=200),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=customer_service.DEFAULT_PAGE_SIZE, ge=1, le=200),
    includeArchived: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """One numbered page, with the totals a pager needs to render itself.

    `total` and `totalPages` are returned rather than left for the client to
    infer: a pager that only knows "there might be more" cannot show "3 of 9",
    and one that counts by fetching every page defeats the point of paging.
    """
    rows, total = await customer_service.page_of_customers(
        session, query=q, page=page, page_size=pageSize, include_archived=includeArchived
    )
    # One grouped query for the whole page, not one per row.
    counts = await customer_service.project_counts(session, [row.id for row in rows])

    return {
        "customers": [
            {**customer_service.serialise(row), "projectCount": counts.get(row.id, 0)}
            for row in rows
        ],
        "page": page,
        "pageSize": pageSize,
        "total": total,
        "totalPages": max(1, -(-total // pageSize)),
    }


@router.get("/{customer_id}")
async def get_customer(
    customer_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """The customer, their projects, and what has happened to them.

    The projects are the point. Without them this screen could create work but
    never show any, so a project became unreachable the moment you navigated
    away from the workspace that made it - there is no project list anywhere
    else, and the id is not something anyone keeps.
    """
    customer = await customer_service.get_customer(session, customer_id)
    events = await activity.list_for_customer(session, customer_id)
    return {
        "customer": customer_service.serialise(customer),
        "projects": await customer_service.projects_for(session, customer_id),
        "activity": [activity.serialise(event) for event in events],
    }


@router.patch("/{customer_id}")
async def update_customer(
    customer_id: str,
    payload: UpdateCustomerRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    customer = await customer_service.get_customer(session, customer_id)

    sent = payload.model_dump(exclude_unset=True)
    fields = {_UPDATABLE[key]: value for key, value in sent.items() if key in _UPDATABLE}

    await customer_service.update_customer(session, customer, fields=fields)

    # Which fields moved, never what they moved to. "The email address changed"
    # is what an operator needs from a timeline; the address itself belongs on
    # the record, not in an audit row.
    await activity.record_best_effort(
        session,
        event_type=activity.CUSTOMER_UPDATED,
        actor="user",
        customer_id=customer.id,
        metadata={"changedFields": ", ".join(sorted(fields))} if fields else None,
    )
    await commit_before_response(session)
    return {"customer": customer_service.serialise(customer)}


@router.post("/{customer_id}/archive")
async def archive_customer(
    customer_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Retire a customer without removing them.

    The usual answer to "delete this customer": their issued proposals keep
    naming them, their projects are untouched, and they simply stop appearing
    in the picker.
    """
    customer = await customer_service.get_customer(session, customer_id)
    await customer_service.archive_customer(session, customer)
    await commit_before_response(session)
    return {"customer": customer_service.serialise(customer)}


@router.post("/{customer_id}/unarchive")
async def unarchive_customer(
    customer_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    customer = await customer_service.get_customer(session, customer_id)
    customer.archived_at = None
    await session.flush()
    await commit_before_response(session)
    return {"customer": customer_service.serialise(customer)}


@router.delete("/{customer_id}")
async def delete_customer(
    customer_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Remove a customer **and everything built for them**.

    This cascades: their projects go, and `proposals`, `chat_messages`,
    `proposal_views` and `proposal_deliveries` follow by ON DELETE CASCADE. The
    FK from `projects` is SET NULL, so the projects are deleted explicitly here
    rather than left behind as orphans belonging to nobody.

    **What that costs, stated plainly:** an issued proposal's share link stops
    resolving. Somebody may be holding that link, and this is the one operation
    in the application that can invalidate a document already sent. The caller
    is told exactly how much of that they are about to destroy - the counts are
    returned so the confirmation can name them - and `archive` remains the
    non-destructive alternative that keeps every document intact.
    """
    customer = await customer_service.get_customer(session, customer_id)
    projects = await customer_service.projects_for(session, customer_id)
    issued = [p for p in projects if p["hasProposal"]]

    for summary in projects:
        project = await session.get(Project, summary["projectId"])
        if project is not None:
            await session.delete(project)

    await session.delete(customer)
    await commit_before_response(session)

    logger.info(
        "deleted customer %s with %d project(s), %d issued proposal(s)",
        customer_id,
        len(projects),
        len(issued),
    )
    return {
        "deleted": True,
        "deletedProjects": len(projects),
        "deletedProposals": len(issued),
    }
