"""Customer normalisation and validation.

Tested directly rather than through a route, because the awkward cases are the
point: an address with a newline in it, a name that is only whitespace, an
optional field that arrives as an empty string.

Two rules carry weight downstream and are asserted here rather than assumed.
Email is stored already-normalised, which is what makes the plain unique index
a case-insensitive constraint. And "absent" is always `None`, never `""`, so no
consumer has to test for both.
"""

from __future__ import annotations

import pytest

from app.core.errors import ValidationError
from app.domain.customers import (
    MAX_EMAIL_LENGTH,
    MAX_NAME_LENGTH,
    display_name_for,
    mask_email,
    normalise_email,
    optional_text,
    required_name,
)

# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("anna@example.com", "anna@example.com"),
        ("  Anna@Example.COM  ", "anna@example.com"),
        ("ANNA.SCHMIDT@sub.example.co.uk", "anna.schmidt@sub.example.co.uk"),
        ("a+tag@example.com", "a+tag@example.com"),
        ("first.last@my-company.de", "first.last@my-company.de"),
    ],
)
def test_valid_addresses_are_trimmed_and_lower_cased(raw: str, expected: str) -> None:
    assert normalise_email(raw) == expected


def test_the_stored_form_is_the_comparison_form() -> None:
    """Why the plain unique index is a case-insensitive rule.

    Two spellings of one address must normalise to the same string, because the
    database compares the stored bytes and nothing else.
    """
    assert normalise_email("Anna@Example.com") == normalise_email("anna@EXAMPLE.COM")


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        None,
        "not-an-address",
        "@example.com",
        "anna@",
        "anna@localhost",  # no dot: a real customer address always has one
        "anna@@example.com",
        "anna example@example.com",
        "anna@example",
        "anna@.com",
        "anna@example..com",
        ".anna@example.com",
        "anna.@example.com",
        "<anna@example.com>",
        "Anna <anna@example.com>",
        "anna@example.c",  # single-character TLD
        "anna@exam ple.com",
    ],
)
def test_malformed_addresses_are_refused(raw: str | None) -> None:
    with pytest.raises(ValidationError):
        normalise_email(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "anna@example.com\nBcc: attacker@evil.test",
        "anna@example.com\r\nBcc: attacker@evil.test",
        "anna\t@example.com",
        "anna@example.com\x00",
        "anna\n@example.com",
    ],
)
def test_an_address_carrying_a_line_break_is_refused(raw: str) -> None:
    """A newline *inside* an address is a header-injection primitive.

    Refused here rather than escaped later, so no caller has to remember.
    """
    with pytest.raises(ValidationError):
        normalise_email(raw)


@pytest.mark.parametrize(
    "raw", ["anna@example.com\r", "anna@example.com\r\n", "\nanna@example.com"]
)
def test_a_surrounding_line_break_is_trimmed_rather_than_refused(raw: str) -> None:
    """A trailing CR is a paste artefact, not an attack.

    The distinction that matters is *embedded* versus *surrounding*. Injection
    needs content after the break - `\\r\\nBcc: ...` - and that is refused above.
    A bare trailing newline leaves nothing behind once trimmed, so it is treated
    like any other surrounding whitespace and the stored value is clean.

    Ordering is what makes this safe: the trim happens first, then the control
    character check, then the pattern. Anything a trim cannot remove is refused.
    """
    assert normalise_email(raw) == "anna@example.com"


def test_an_over_long_address_is_refused() -> None:
    too_long = "a" * (MAX_EMAIL_LENGTH - 11) + "@example.com"
    assert len(too_long) > MAX_EMAIL_LENGTH
    with pytest.raises(ValidationError):
        normalise_email(too_long)


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------


def test_a_name_is_trimmed_and_internally_collapsed() -> None:
    assert required_name("  Anna   Maria  ", field="First name") == "Anna Maria"


@pytest.mark.parametrize("raw", ["", "   ", "\t\n", None])
def test_a_blank_name_is_refused(raw: str | None) -> None:
    with pytest.raises(ValidationError):
        required_name(raw, field="First name")


def test_an_over_long_name_is_refused() -> None:
    with pytest.raises(ValidationError):
        required_name("x" * (MAX_NAME_LENGTH + 1), field="Last name")


def test_names_outside_the_latin_alphabet_are_accepted() -> None:
    """Nothing here restricts a name to ASCII, and nothing should."""
    assert required_name("Şirin", field="First name") == "Şirin"
    assert required_name("O'Brien-Müller", field="Last name") == "O'Brien-Müller"


# ---------------------------------------------------------------------------
# Optional fields: absent has one spelling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", [None, "", "   ", "\n"])
def test_a_blank_optional_field_becomes_none_not_empty_string(raw: str | None) -> None:
    assert optional_text(raw, field="Phone", limit=64) is None


def test_an_optional_field_is_collapsed_like_a_name() -> None:
    assert optional_text("  +27  21 555 0100 ", field="Phone", limit=64) == "+27 21 555 0100"


def test_an_over_long_optional_field_is_refused() -> None:
    with pytest.raises(ValidationError):
        optional_text("x" * 65, field="Phone", limit=64)


# ---------------------------------------------------------------------------
# Display name
# ---------------------------------------------------------------------------


def test_the_display_name_is_derived_when_not_supplied() -> None:
    assert display_name_for("Anna", "Schmidt") == "Anna Schmidt"


def test_an_explicit_display_name_wins() -> None:
    assert display_name_for("Anna", "Schmidt", "Schmidt Family Trust") == "Schmidt Family Trust"


def test_a_blank_display_name_falls_back_to_the_derived_one() -> None:
    assert display_name_for("Anna", "Schmidt", "   ") == "Anna Schmidt"


# ---------------------------------------------------------------------------
# Masking
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("anna@example.com", "a***@example.com"),
        ("a@example.com", "***@example.com"),
        ("anna.schmidt@sub.example.co.uk", "a***@sub.example.co.uk"),
        (None, None),
    ],
)
def test_masking_keeps_the_domain_and_hides_the_person(raw: str | None, expected) -> None:
    """The domain is what a salesperson recognises; the local part is who it is.

    Used in logs and in activity metadata, so a stored event never carries a
    full address around.
    """
    assert mask_email(raw) == expected


def test_masking_never_leaks_the_whole_local_part() -> None:
    for address in ("ab@example.com", "abc@example.com", "a@x.io"):
        masked = mask_email(address)
        local = address.split("@")[0]
        assert masked is not None
        assert local not in masked
