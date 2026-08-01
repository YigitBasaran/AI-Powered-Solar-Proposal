"""The activity metadata allow-list.

An audit table is where personal data goes to be forgotten about. It is not
treated as a customer record, it is rarely reviewed, and it grows one
convenient extra key at a time - so the rule has to be structural rather than
remembered: a key not named for its event type does not get stored, and neither
does a value that is not a scalar.

The nested-value rule matters more than it looks. A whole provider response, a
whole customer record or a whole rendered email is one careless `metadata={...}`
away from being persisted, and each of those arrives as a dict.
"""

from __future__ import annotations

import pytest

from app.services.activity import EVENT_METADATA, sanitise


def test_an_unknown_event_type_is_refused() -> None:
    """Typos must not create new event types by accident."""
    with pytest.raises(ValueError, match="not a known activity event type"):
        sanitise("proposal.exploded", {"anything": 1})


def test_keys_outside_the_allow_list_are_dropped() -> None:
    kept = sanitise(
        "proposal.email_sent",
        {"recipientMasked": "a***@example.com", "provider": "smtp", "subject": "Your proposal"},
    )
    assert kept == {"recipientMasked": "a***@example.com", "provider": "smtp"}


def test_a_nested_value_is_dropped_even_when_its_key_is_allowed() -> None:
    """How a whole provider response ends up in an audit row."""
    kept = sanitise("proposal.email_failed", {"errorCode": {"smtp": {"code": 550}}})
    assert kept is None


def test_a_list_value_is_dropped() -> None:
    kept = sanitise("customer.updated", {"changedFields": ["email", "phone"]})
    assert kept is None, "a list is how a whole record arrives; join it into a string instead"


def test_a_scalar_value_survives() -> None:
    assert sanitise("customer.updated", {"changedFields": "email, phone"}) == {
        "changedFields": "email, phone"
    }


def test_none_values_are_omitted_rather_than_stored() -> None:
    assert sanitise("project.created", {"projectName": None}) is None


def test_empty_metadata_is_null_not_an_empty_dict() -> None:
    assert sanitise("proposal.pdf_downloaded", {}) is None
    assert sanitise("proposal.pdf_downloaded", None) is None


@pytest.mark.parametrize("event_type", sorted(EVENT_METADATA))
def test_no_event_type_may_carry_a_dangerous_key(event_type: str) -> None:
    """The names that must never appear, whatever anyone adds later.

    A recipient is stored masked or not at all; a body, a subject, a raw
    address, an IP and a credential are never stored. Asserted across every
    event type so a new one cannot quietly introduce them.
    """
    forbidden = {
        "email",
        "recipient",
        "to",
        "body",
        "textBody",
        "htmlBody",
        "subject",
        "password",
        "credentials",
        "ip",
        "ipAddress",
        "ipHash",
        "userAgent",
        "response",
        "providerResponse",
    }
    assert not (EVENT_METADATA[event_type] & forbidden), (
        f"{event_type} allows a key that must never be stored"
    )


def test_a_masked_recipient_is_the_only_recipient_field_anywhere() -> None:
    for event_type, allowed in EVENT_METADATA.items():
        for key in allowed:
            if "recipient" in key.lower():
                assert key == "recipientMasked", f"{event_type}.{key} is not masked"
