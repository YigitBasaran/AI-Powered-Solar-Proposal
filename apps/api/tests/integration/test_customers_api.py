"""Customer endpoints, end to end.

The database is session-scoped and shared, so every test here mints its own
addresses and searches by a token unique to itself. Tests that assert on a
*list* filter down to their own rows rather than counting the table, which
would make them depend on execution order.

The load-bearing assertions are uniqueness (the thing that makes "select or
create" unambiguous) and the fact that a duplicate names the row it collided
with, so the UI can offer to use it.
"""

from __future__ import annotations

import uuid

import pytest


def unique_email(prefix: str = "customer") -> str:
    return f"{prefix}.{uuid.uuid4().hex[:12]}@example.com"


def create(client, **overrides) -> dict:
    body = {
        "firstName": "Anna",
        "lastName": "Schmidt",
        "email": unique_email(),
        **overrides,
    }
    response = client.post("/api/v1/customers", json=body)
    assert response.status_code == 201, response.text
    return response.json()["customer"]


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


def test_creating_a_customer_returns_it(client) -> None:
    email = unique_email()
    response = client.post(
        "/api/v1/customers",
        json={"firstName": "Anna", "lastName": "Schmidt", "email": email},
    )
    assert response.status_code == 201, response.text

    customer = response.json()["customer"]
    assert customer["customerId"]
    assert customer["displayName"] == "Anna Schmidt"
    assert customer["email"] == email
    assert customer["phone"] is None
    assert customer["archivedAt"] is None


def test_the_stored_email_is_normalised(client) -> None:
    raw = unique_email("Mixed.Case")
    customer = create(client, email=f"  {raw.upper()}  ")
    assert customer["email"] == raw.lower()


def test_optional_fields_are_stored_and_blank_ones_become_null(client) -> None:
    customer = create(
        client,
        phone="  +27 21 555 0100 ",
        companyName="",
        address="12 Galway Road, Cape Town",
    )
    assert customer["phone"] == "+27 21 555 0100"
    assert customer["companyName"] is None, "an empty string must not be stored as one"
    assert customer["address"] == "12 Galway Road, Cape Town"


def test_an_explicit_display_name_is_kept(client) -> None:
    customer = create(client, displayName="Schmidt Family Trust")
    assert customer["displayName"] == "Schmidt Family Trust"


@pytest.mark.parametrize(
    "body",
    [
        {"firstName": "", "lastName": "Schmidt", "email": "a@example.com"},
        {"firstName": "Anna", "lastName": "   ", "email": "a@example.com"},
        {"firstName": "Anna", "lastName": "Schmidt", "email": "not-an-address"},
        {"firstName": "Anna", "lastName": "Schmidt", "email": "anna@localhost"},
    ],
)
def test_an_invalid_customer_is_refused(client, body: dict) -> None:
    response = client.post("/api/v1/customers", json=body)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_an_address_with_an_embedded_header_is_refused(client) -> None:
    """The route must not be the place this is first noticed."""
    response = client.post(
        "/api/v1/customers",
        json={
            "firstName": "Anna",
            "lastName": "Schmidt",
            "email": "anna@example.com\r\nBcc: attacker@evil.test",
        },
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Uniqueness
# ---------------------------------------------------------------------------


def test_a_duplicate_email_is_refused_and_names_the_existing_customer(client) -> None:
    email = unique_email()
    first = create(client, email=email)

    response = client.post(
        "/api/v1/customers",
        json={"firstName": "Someone", "lastName": "Else", "email": email},
    )
    assert response.status_code == 409, response.text

    error = response.json()["error"]
    assert error["code"] == "CUSTOMER_EMAIL_EXISTS"
    assert error["details"]["customerId"] == first["customerId"], (
        "a duplicate that does not say which record it collided with leaves the "
        "operator to go and search for it by hand"
    )


def test_uniqueness_ignores_case_and_surrounding_space(client) -> None:
    email = unique_email()
    create(client, email=email)

    response = client.post(
        "/api/v1/customers",
        json={"firstName": "Anna", "lastName": "Schmidt", "email": f"  {email.upper()} "},
    )
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def test_a_customer_can_be_read_back(client) -> None:
    created = create(client)
    fetched = client.get(f"/api/v1/customers/{created['customerId']}")
    assert fetched.status_code == 200
    assert fetched.json()["customer"] == created


def test_an_unknown_customer_is_not_found(client) -> None:
    response = client.get(f"/api/v1/customers/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CUSTOMER_NOT_FOUND"


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def test_search_matches_name_email_and_company(client) -> None:
    token = uuid.uuid4().hex[:10]
    create(client, lastName=f"Zeta{token}")
    create(client, email=unique_email(f"finder{token}"))
    create(client, companyName=f"Helios {token} BV")

    for query in (f"zeta{token}", f"finder{token}", token):
        found = client.get("/api/v1/customers", params={"q": query}).json()["customers"]
        assert found, f"search for {query!r} found nothing"


def test_search_is_case_insensitive(client) -> None:
    token = uuid.uuid4().hex[:10]
    create(client, lastName=f"Zeta{token}")

    lower = client.get("/api/v1/customers", params={"q": f"zeta{token}"}).json()["customers"]
    upper = client.get("/api/v1/customers", params={"q": f"ZETA{token}"}).json()["customers"]
    assert [c["customerId"] for c in lower] == [c["customerId"] for c in upper]


def test_a_wildcard_in_the_query_is_matched_literally(client) -> None:
    """`%` is a LIKE wildcard, and an unescaped one matches every customer.

    Which would silently turn a search that finds nothing into a search that
    appears to find everyone - the worst possible failure on a screen whose job
    is picking who receives an email.
    """
    create(client)
    found = client.get("/api/v1/customers", params={"q": "%"}).json()["customers"]
    assert found == []


def test_search_returns_nothing_for_an_unmatched_term(client) -> None:
    found = client.get(
        "/api/v1/customers", params={"q": f"nobody-{uuid.uuid4().hex}"}
    ).json()["customers"]
    assert found == []


def test_the_list_is_newest_first(client) -> None:
    token = uuid.uuid4().hex[:10]
    older = create(client, lastName=f"Order{token}")
    newer = create(client, lastName=f"Order{token}")

    listed = client.get("/api/v1/customers", params={"q": f"order{token}"}).json()["customers"]
    assert [c["customerId"] for c in listed] == [newer["customerId"], older["customerId"]]


def test_paging_walks_the_whole_result_set_without_repeating(client) -> None:
    token = uuid.uuid4().hex[:10]
    expected = {create(client, lastName=f"Page{token}")["customerId"] for _ in range(5)}

    seen: list[str] = []
    for page in range(1, 5):
        body = client.get(
            "/api/v1/customers", params={"q": f"page{token}", "page": page, "pageSize": 2}
        ).json()
        seen.extend(c["customerId"] for c in body["customers"])
        if page >= body["totalPages"]:
            break

    assert len(seen) == len(set(seen)), "a customer appeared on two pages"
    assert set(seen) == expected


def test_the_page_reports_the_totals_a_pager_needs(client) -> None:
    """A client that only knows "there might be more" cannot say "3 of 9"."""
    token = uuid.uuid4().hex[:10]
    for _ in range(5):
        create(client, lastName=f"Total{token}")

    body = client.get(
        "/api/v1/customers", params={"q": f"total{token}", "page": 2, "pageSize": 2}
    ).json()

    assert body["total"] == 5
    assert body["totalPages"] == 3
    assert body["page"] == 2
    assert body["pageSize"] == 2
    assert len(body["customers"]) == 2


def test_the_last_page_is_short_rather_than_padded(client) -> None:
    token = uuid.uuid4().hex[:10]
    for _ in range(5):
        create(client, lastName=f"Short{token}")

    body = client.get(
        "/api/v1/customers", params={"q": f"short{token}", "page": 3, "pageSize": 2}
    ).json()
    assert len(body["customers"]) == 1


def test_a_page_beyond_the_end_is_empty_rather_than_an_error(client) -> None:
    token = uuid.uuid4().hex[:10]
    create(client, lastName=f"Beyond{token}")

    body = client.get(
        "/api/v1/customers", params={"q": f"beyond{token}", "page": 99, "pageSize": 25}
    ).json()
    assert body["customers"] == []
    assert body["total"] == 1


# ---------------------------------------------------------------------------
# Amending
# ---------------------------------------------------------------------------


def test_a_partial_update_touches_only_what_was_sent(client) -> None:
    customer = create(client, phone="+27 21 555 0100", companyName="Helios BV")

    updated = client.patch(
        f"/api/v1/customers/{customer['customerId']}", json={"phone": "+27 21 555 0199"}
    )
    assert updated.status_code == 200, updated.text

    body = updated.json()["customer"]
    assert body["phone"] == "+27 21 555 0199"
    assert body["companyName"] == "Helios BV", "an untouched field was overwritten"
    assert body["email"] == customer["email"]


def test_an_explicit_null_clears_a_field(client) -> None:
    """The distinction `exclude_unset` exists for: absent keeps, null clears."""
    customer = create(client, phone="+27 21 555 0100")

    body = client.patch(
        f"/api/v1/customers/{customer['customerId']}", json={"phone": None}
    ).json()["customer"]
    assert body["phone"] is None


def test_renaming_updates_a_derived_display_name(client) -> None:
    customer = create(client)
    assert customer["displayName"] == "Anna Schmidt"

    body = client.patch(
        f"/api/v1/customers/{customer['customerId']}", json={"lastName": "Meyer"}
    ).json()["customer"]
    assert body["displayName"] == "Anna Meyer"


def test_renaming_leaves_an_explicit_display_name_alone(client) -> None:
    customer = create(client, displayName="Schmidt Family Trust")

    body = client.patch(
        f"/api/v1/customers/{customer['customerId']}", json={"lastName": "Meyer"}
    ).json()["customer"]
    assert body["displayName"] == "Schmidt Family Trust", "a chosen name was overwritten"


def test_changing_an_email_to_one_already_taken_is_refused(client) -> None:
    taken = create(client)
    other = create(client)

    response = client.patch(
        f"/api/v1/customers/{other['customerId']}", json={"email": taken["email"]}
    )
    assert response.status_code == 409
    assert response.json()["error"]["details"]["customerId"] == taken["customerId"]


def test_updating_an_email_to_its_own_value_is_allowed(client) -> None:
    customer = create(client)
    response = client.patch(
        f"/api/v1/customers/{customer['customerId']}",
        json={"email": customer["email"].upper()},
    )
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Archiving
# ---------------------------------------------------------------------------


def test_archiving_hides_a_customer_from_the_default_list(client) -> None:
    token = uuid.uuid4().hex[:10]
    customer = create(client, lastName=f"Gone{token}")

    archived = client.post(f"/api/v1/customers/{customer['customerId']}/archive")
    assert archived.status_code == 200
    assert archived.json()["customer"]["archivedAt"]

    default = client.get("/api/v1/customers", params={"q": f"gone{token}"}).json()["customers"]
    assert default == []

    included = client.get(
        "/api/v1/customers", params={"q": f"gone{token}", "includeArchived": True}
    ).json()["customers"]
    assert [c["customerId"] for c in included] == [customer["customerId"]]


def test_an_archived_customer_is_still_readable_by_id(client) -> None:
    """The record survives, because issued proposals name this person."""
    customer = create(client)
    client.post(f"/api/v1/customers/{customer['customerId']}/archive")

    fetched = client.get(f"/api/v1/customers/{customer['customerId']}")
    assert fetched.status_code == 200
    assert fetched.json()["customer"]["archivedAt"]
