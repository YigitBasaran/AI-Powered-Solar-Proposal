"""Customer records: create, find, search, amend, archive.

The interesting parts are uniqueness and search.

**Uniqueness is the database's job, not this module's.** A select-then-insert
would let two concurrent requests both find nothing and both insert, so the
insert is wrapped in a savepoint and an `IntegrityError` is translated into the
duplicate error - carrying the id of the row that actually won, so the caller
can offer to use it. Same shape as `find_or_create_revision`, same reason.

**Search is a deliberately dumb substring match.** No fuzzy matching, no
trigram similarity, no ranking. This list feeds a screen where a salesperson
picks a recipient for an email, and a near-miss that ranks a wrong person first
is worse than no match at all - so the only matching rule is "the text you typed
appears in the record".
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import CustomerEmailExistsError, CustomerNotFoundError
from app.domain.customers import (
    MAX_COMPANY_LENGTH,
    MAX_PHONE_LENGTH,
    display_name_for,
    mask_email,
    normalise_email,
    optional_text,
    required_name,
)
from app.models.tables import Customer, Project, Proposal, _utcnow, as_utc, iso_utc

logger = logging.getLogger("solarvis.customers")

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200

#: Escaped before being placed in a LIKE pattern, so a customer searching for
#: "50%" does not match every row.
_LIKE_ESCAPE = "\\"


def _like_term(raw: str) -> str:
    escaped = raw.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
    for wildcard in ("%", "_"):
        escaped = escaped.replace(wildcard, _LIKE_ESCAPE + wildcard)
    return f"%{escaped.lower()}%"


async def by_email(session: AsyncSession, email: str) -> Customer | None:
    """Look up by the normalised address. `email` must already be normalised."""
    return (
        await session.execute(select(Customer).where(Customer.email == email))
    ).scalar_one_or_none()


async def get_customer(session: AsyncSession, customer_id: str) -> Customer:
    customer = (
        await session.execute(select(Customer).where(Customer.id == customer_id))
    ).scalar_one_or_none()
    if customer is None:
        raise CustomerNotFoundError(f"Customer {customer_id} does not exist.")
    return customer


async def create_customer(
    session: AsyncSession,
    *,
    first_name: str | None,
    last_name: str | None,
    email: str | None,
    phone: str | None = None,
    company_name: str | None = None,
    address: str | None = None,
    display_name: str | None = None,
) -> Customer:
    """Validate, normalise and insert. Raises on a duplicate address."""
    first = required_name(first_name, field="First name")
    last = required_name(last_name, field="Last name")
    normalised = normalise_email(email)

    customer = Customer(
        first_name=first,
        last_name=last,
        display_name=display_name_for(first, last, display_name),
        email=normalised,
        phone=optional_text(phone, field="Phone", limit=MAX_PHONE_LENGTH),
        company_name=optional_text(company_name, field="Company", limit=MAX_COMPANY_LENGTH),
        address=optional_text(address, field="Address", limit=2000),
    )

    try:
        async with session.begin_nested():
            session.add(customer)
            await session.flush()
    except IntegrityError:
        # The unique index refused it. Whoever holds the address now is the
        # authority, not whatever a prior SELECT would have said. The savepoint
        # has already unwound, so the surrounding transaction is still usable.
        existing = await by_email(session, normalised)
        logger.info("duplicate customer email %s", mask_email(normalised))
        raise CustomerEmailExistsError(
            f"A customer with the email {normalised} already exists.",
            details={"customerId": existing.id} if existing else {},
        ) from None

    logger.info("created customer %s (%s)", customer.id, mask_email(customer.email))
    return customer


async def update_customer(
    session: AsyncSession,
    customer: Customer,
    *,
    fields: dict[str, Any],
) -> Customer:
    """Apply a partial update. Only keys present in `fields` are touched."""
    if "first_name" in fields or "last_name" in fields:
        first = (
            required_name(fields["first_name"], field="First name")
            if "first_name" in fields
            else customer.first_name
        )
        last = (
            required_name(fields["last_name"], field="Last name")
            if "last_name" in fields
            else customer.last_name
        )
        # The derived display name follows the parts it was derived from,
        # unless the caller is setting it explicitly in the same request.
        was_derived = customer.display_name == f"{customer.first_name} {customer.last_name}"
        customer.first_name = first
        customer.last_name = last
        if was_derived and "display_name" not in fields:
            customer.display_name = display_name_for(first, last)

    if "display_name" in fields:
        customer.display_name = display_name_for(
            customer.first_name, customer.last_name, fields["display_name"]
        )

    if "email" in fields:
        normalised = normalise_email(fields["email"])
        if normalised != customer.email:
            clash = await by_email(session, normalised)
            if clash is not None:
                raise CustomerEmailExistsError(
                    f"A customer with the email {normalised} already exists.",
                    details={"customerId": clash.id},
                )
            customer.email = normalised

    if "phone" in fields:
        customer.phone = optional_text(fields["phone"], field="Phone", limit=MAX_PHONE_LENGTH)
    if "company_name" in fields:
        customer.company_name = optional_text(
            fields["company_name"], field="Company", limit=MAX_COMPANY_LENGTH
        )
    if "address" in fields:
        customer.address = optional_text(fields["address"], field="Address", limit=2000)

    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError:
        # A concurrent write took the address between the check above and here.
        existing = await by_email(session, customer.email)
        raise CustomerEmailExistsError(
            "A customer with that email address already exists.",
            details={"customerId": existing.id} if existing else {},
        ) from None

    return customer


async def archive_customer(session: AsyncSession, customer: Customer) -> Customer:
    """Retire a customer without removing the record.

    Deliberately not a delete. Finalised proposals carry a frozen snapshot that
    names this person; removing the row would leave those documents attributed
    to nothing, and they are the one thing in this system that must stay
    readable exactly as issued.
    """
    if customer.archived_at is None:
        customer.archived_at = _utcnow()
        await session.flush()
    return customer


def _search_filter(statement: Select[Any], query: str) -> Select[Any]:
    term = _like_term(query)
    return statement.where(
        or_(
            func.lower(Customer.display_name).like(term, escape=_LIKE_ESCAPE),
            func.lower(Customer.first_name).like(term, escape=_LIKE_ESCAPE),
            func.lower(Customer.last_name).like(term, escape=_LIKE_ESCAPE),
            func.lower(Customer.email).like(term, escape=_LIKE_ESCAPE),
            func.lower(func.coalesce(Customer.company_name, "")).like(term, escape=_LIKE_ESCAPE),
        )
    )


def _stored_form(value: datetime) -> datetime:
    """The naive-UTC form SQLite actually compares against.

    A cursor is a WHERE clause, so both sides have to be in the same
    representation. The column holds naive UTC (SQLite has no timezone type),
    while a row still in the identity map after a write is aware - so a cursor
    minted from one and compared against the other can silently skip or repeat
    a row. Normalised in both directions here rather than hoped about.
    """
    aware = as_utc(value)
    assert aware is not None
    return aware.replace(tzinfo=None)


def encode_cursor(customer: Customer) -> str:
    return f"{_stored_form(customer.created_at).isoformat()}|{customer.id}"


def _decode_cursor(cursor: str) -> tuple[datetime, str] | None:
    created, _, identifier = cursor.partition("|")
    try:
        return _stored_form(datetime.fromisoformat(created)), identifier
    except ValueError:
        # An unparseable cursor is a client bug, not a server error. Starting
        # from the top is the honest recovery: it returns real data rather than
        # an empty page that reads as "no customers".
        logger.info("ignoring malformed customer cursor")
        return None


async def list_customers(
    session: AsyncSession,
    *,
    query: str | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
    include_archived: bool = False,
) -> tuple[list[Customer], str | None]:
    """Newest first, keyset-paginated on `(created_at, id)`.

    Keyset rather than OFFSET so a customer created while the operator is
    paging cannot shift the window and hide a row. Kept alongside the numbered
    pagination below, which needs OFFSET because it has to jump to page 7.
    """
    size = max(1, min(int(limit), MAX_PAGE_SIZE))

    statement = select(Customer)
    if not include_archived:
        statement = statement.where(Customer.archived_at.is_(None))
    if query and query.strip():
        statement = _search_filter(statement, query.strip())
    if cursor:
        decoded = _decode_cursor(cursor)
        if decoded is not None:
            created_at, identifier = decoded
            statement = statement.where(
                (Customer.created_at < created_at)
                | ((Customer.created_at == created_at) & (Customer.id < identifier))
            )

    statement = statement.order_by(Customer.created_at.desc(), Customer.id.desc()).limit(size + 1)
    rows = list((await session.execute(statement)).scalars().all())

    next_cursor = encode_cursor(rows[size - 1]) if len(rows) > size else None
    return rows[:size], next_cursor


async def page_of_customers(
    session: AsyncSession,
    *,
    query: str | None = None,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    include_archived: bool = False,
) -> tuple[list[Customer], int]:
    """One numbered page, and the total number of matching rows.

    OFFSET rather than a keyset, deliberately. A numbered pager has to answer
    "take me to page 7", which a cursor cannot do without walking every page in
    between. The cost is the usual one - a row inserted mid-paging shifts the
    window - and at this scale it is the right trade for being able to show
    "page 3 of 9" at all.

    The count is a second query rather than a window function, because SQLite
    and PostgreSQL disagree about the latter often enough not to be worth it
    for two-figure row counts.
    """
    size = max(1, min(int(page_size), MAX_PAGE_SIZE))
    number = max(1, int(page))

    filters = select(Customer)
    if not include_archived:
        filters = filters.where(Customer.archived_at.is_(None))
    if query and query.strip():
        filters = _search_filter(filters, query.strip())

    total = (
        await session.execute(select(func.count()).select_from(filters.subquery()))
    ).scalar_one()

    rows = (
        await session.execute(
            filters.order_by(Customer.created_at.desc(), Customer.id.desc())
            .offset((number - 1) * size)
            .limit(size)
        )
    ).scalars()

    return list(rows), int(total)


async def projects_for(session: AsyncSession, customer_id: str) -> list[dict[str, Any]]:
    """This customer's projects, newest first, each with its proposal if it has one.

    Every project in a revision chain is listed rather than only the newest.
    A revision is a distinct project with its own proposal and its own share
    link, and the older ones are documents the customer was actually sent -
    hiding them would make the one screen that answers "what have we sent this
    person" answer it wrongly.
    """
    rows = (
        await session.execute(
            select(Project, Proposal)
            .outerjoin(Proposal, Proposal.project_id == Project.id)
            .where(Project.customer_id == customer_id)
            .order_by(Project.created_at.desc(), Project.id.desc())
        )
    ).all()

    return [
        {
            "projectId": project.id,
            "name": project.name,
            "currentStep": project.current_step,
            "analysisStatus": project.analysis_status,
            "isRevision": project.revision_of_project_id is not None,
            "revisionOfProjectId": project.revision_of_project_id,
            "hasProposal": proposal is not None,
            "proposalId": proposal.id if proposal else None,
            "shareToken": proposal.share_token if proposal else None,
            "reference": proposal.reference if proposal else None,
            "revisionNumber": (proposal.revision_number or 1) if proposal else None,
            "systemSizeKwp": proposal.feasible_system_size_kwp if proposal else None,
            "finalisedAt": iso_utc(proposal.created_at) if proposal else None,
            "createdAt": iso_utc(project.created_at),
            "updatedAt": iso_utc(project.updated_at),
        }
        for project, proposal in rows
    ]


async def project_counts(session: AsyncSession, customer_ids: list[str]) -> dict[str, int]:
    """How many projects each of these customers has.

    One grouped query rather than one per row: a list of fifty customers should
    not cost fifty round trips to render a count beside each name.
    """
    if not customer_ids:
        return {}
    rows = (
        await session.execute(
            select(Project.customer_id, func.count())
            .where(Project.customer_id.in_(customer_ids))
            .group_by(Project.customer_id)
        )
    ).all()
    return {customer_id: int(count) for customer_id, count in rows if customer_id}


def serialise(customer: Customer) -> dict[str, Any]:
    """The internal projection. Never served on a public route - see
    `proposal.public_payload`, which exposes the display name and nothing else.
    """
    return {
        "customerId": customer.id,
        "firstName": customer.first_name,
        "lastName": customer.last_name,
        "displayName": customer.display_name,
        "email": customer.email,
        "phone": customer.phone,
        "companyName": customer.company_name,
        "address": customer.address,
        "archivedAt": iso_utc(customer.archived_at),
        "createdAt": iso_utc(customer.created_at),
        "updatedAt": iso_utc(customer.updated_at),
    }


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "archive_customer",
    "by_email",
    "create_customer",
    "get_customer",
    "list_customers",
    "project_counts",
    "projects_for",
    "serialise",
    "update_customer",
]
