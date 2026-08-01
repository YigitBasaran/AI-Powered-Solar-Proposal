"""Customer identity: normalisation and validation.

Pure functions over strings. No database, no session, no settings - which is
what lets the awkward cases be tested directly rather than through a route.

Two decisions are made here and relied on everywhere downstream.

**Email is stored already-normalised.** Lower-cased and trimmed on the way in,
so the unique index on the column *is* the case-insensitive uniqueness rule.
The alternative - a functional index over `lower(email)` - behaves differently
on SQLite and PostgreSQL and leaves the stored value in whatever case it
arrived, so two rows that collide look different when you read them.

**"Absent" has exactly one representation.** Optional fields normalise to
`None`, never to `""`. Without that rule a customer with no phone number is
sometimes null and sometimes empty depending on which form submitted them, and
every consumer needs to test for both.

`app.core.errors` is imported for `ValidationError` only. It is a leaf module -
pure data, no I/O - so this stays a pure-domain unit.
"""

from __future__ import annotations

import re

from app.core.errors import ValidationError

MAX_EMAIL_LENGTH = 320  # RFC 3696 erratum: 64-char local part + @ + 255-char domain
MAX_NAME_LENGTH = 120
MAX_DISPLAY_NAME_LENGTH = 255
MAX_PHONE_LENGTH = 64
MAX_COMPANY_LENGTH = 255

#: Deliberately stricter than RFC 5322, and that is the trade-off.
#:
#: A fully compliant parser accepts quoted local parts, comments, and domain
#: literals like `user@[192.168.0.1]` - forms that are legal, essentially never
#: typed by a salesperson, and awkward to place safely into a header. This
#: pattern requires an unquoted dot-atom local part and a dotted domain with an
#: alphabetic TLD, which is what real customer addresses look like.
#:
#: `\Z` rather than `$` is load-bearing: Python's `$` also matches immediately
#: before a trailing newline, so `"a@b.com\n"` would satisfy an anchored `$`
#: pattern - and a newline in an address is a header-injection primitive.
EMAIL_PATTERN = re.compile(
    r"\A[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
    r"(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
    r"@"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,63}\Z"
)

#: Anything that could terminate or forge a header line. Checked separately from
#: the pattern above so the failure says what it is.
CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")

_WHITESPACE = re.compile(r"\s+")


def collapse(raw: str) -> str:
    """Trim, and reduce internal runs of whitespace to single spaces."""
    return _WHITESPACE.sub(" ", raw).strip()


def optional_text(raw: str | None, *, field: str, limit: int) -> str | None:
    """Normalise an optional field, mapping blank to `None`."""
    if raw is None:
        return None
    value = collapse(raw)
    if not value:
        return None
    if CONTROL_CHARACTERS.search(value):
        raise ValidationError(f"{field} contains control characters.")
    if len(value) > limit:
        raise ValidationError(f"{field} is longer than {limit} characters.")
    return value


def required_name(raw: str | None, *, field: str) -> str:
    value = collapse(raw or "")
    if not value:
        raise ValidationError(f"{field} is required.")
    if CONTROL_CHARACTERS.search(value):
        raise ValidationError(f"{field} contains control characters.")
    if len(value) > MAX_NAME_LENGTH:
        raise ValidationError(f"{field} is longer than {MAX_NAME_LENGTH} characters.")
    return value


def normalise_email(raw: str | None) -> str:
    """Trim, lower-case and validate. The stored form is the returned form."""
    value = (raw or "").strip()
    if not value:
        raise ValidationError("An email address is required.")
    if CONTROL_CHARACTERS.search(value):
        # Said plainly rather than as "invalid address": this is the shape of an
        # injection attempt, and an operator reading the log should see that.
        raise ValidationError("An email address cannot contain line breaks or control characters.")
    if len(value) > MAX_EMAIL_LENGTH:
        raise ValidationError(f"An email address cannot exceed {MAX_EMAIL_LENGTH} characters.")

    value = value.lower()
    if not EMAIL_PATTERN.match(value):
        raise ValidationError(f"'{raw}' is not a valid email address.")
    return value


def display_name_for(first_name: str, last_name: str, supplied: str | None = None) -> str:
    """The one name shown to the customer, and the only one shown publicly."""
    explicit = optional_text(supplied, field="Display name", limit=MAX_DISPLAY_NAME_LENGTH)
    if explicit:
        return explicit
    return f"{first_name} {last_name}".strip()[:MAX_DISPLAY_NAME_LENGTH]


def mask_email(email: str | None) -> str | None:
    """`anna@example.com` -> `a***@example.com`.

    For logs, activity metadata and any confirmation shown outside the compose
    step. The domain is kept because it is what a salesperson uses to recognise
    the right recipient; the local part is what identifies the person.

    A one-character local part masks to `***@domain` rather than leaking itself.
    """
    if not email:
        return None
    local, _, domain = email.partition("@")
    if not domain:
        return "***"
    if len(local) <= 1:
        return f"***@{domain}"
    return f"{local[0]}***@{domain}"


__all__ = [
    "MAX_COMPANY_LENGTH",
    "MAX_DISPLAY_NAME_LENGTH",
    "MAX_EMAIL_LENGTH",
    "MAX_NAME_LENGTH",
    "MAX_PHONE_LENGTH",
    "collapse",
    "display_name_for",
    "mask_email",
    "normalise_email",
    "optional_text",
    "required_name",
]
