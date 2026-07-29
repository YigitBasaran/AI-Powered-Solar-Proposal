"""Tri-state extraction: nothing here, here-and-unusable, or here."""

from __future__ import annotations

import pytest

from app.services.conversation.actions import ExtractionStatus
from app.services.conversation.extractors import (
    extract_consumption,
    extract_location,
    extract_system_size,
)
from app.services.conversation.normalise import normalise

VALID = ExtractionStatus.VALID
INVALID = ExtractionStatus.INVALID
AMBIGUOUS = ExtractionStatus.AMBIGUOUS
ABSENT = ExtractionStatus.ABSENT


# ---------------------------------------------------------------------------
# Consumption - the trap this design exists for
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "status", "value"),
    [
        ("1150", VALID, 1150.0),
        ("1,150 kWh", VALID, 1150.0),
        ("around 1150 per month", VALID, 1150.0),
        ("13800 kWh per year", VALID, 1150.0),
        ("approximately one thousand one hundred per month", VALID, 1100.0),
        ("about eleven fifty", VALID, 1150.0),
        ("I pay 0.30 per kWh and use 1150 kWh per month", VALID, 1150.0),
        # Read, and rejected. NOT absent - the customer answered.
        ("-500 kWh", INVALID, None),
        ("0", INVALID, None),
        ("-500 kWh, or maybe 1150", INVALID, None),
        # A quantity was expressed but never pinned down.
        ("a little over a thousand", AMBIGUOUS, None),
        ("quite high", AMBIGUOUS, None),
        ("around one", AMBIGUOUS, None),
        ("just under 1200", AMBIGUOUS, None),
        # Nothing that resembles an answer at all.
        ("banana", ABSENT, None),
        ("asdfghjkl", ABSENT, None),
        ("", ABSENT, None),
    ],
)
def test_consumption_extraction(text, status, value) -> None:
    extraction = extract_consumption(normalise(text))
    assert extraction.status is status
    assert extraction.values.monthly_consumption_kwh == value


def test_an_unusable_figure_is_distinguishable_from_an_unreadable_message() -> None:
    """The whole point of the tri-state.

    Collapsing these two is how "-500 kWh" fell through to the question
    classifier and matched on a bare `kwh`.
    """
    unusable = extract_consumption(normalise("-500 kWh"))
    unreadable = extract_consumption(normalise("banana"))

    assert unusable.read_a_quantity is True
    assert unusable.reason == "not_positive"
    assert unreadable.read_a_quantity is False


# ---------------------------------------------------------------------------
# Location - reports what was read, never whether it is the right place
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "status", "has_coords"),
    [
        ("-34.04658242871865, 18.46491476666948", VALID, True),
        ("lat -34.04658 lon 18.46491", VALID, True),
        ("34.04658242871865, 18.46491476666948", VALID, True),
        ("London", VALID, False),  # a place, but not one anything here can verify
        ("10 Downing Street, London", VALID, False),
        ("41.0082, 28.9784", VALID, True),
        ("999.9, 18.4", ABSENT, False),
        ("120.0, 18.4", ABSENT, False),
        ("???", ABSENT, False),
        ("1150", ABSENT, False),
    ],
)
def test_location_extraction(text, status, has_coords) -> None:
    extraction = extract_location(normalise(text))
    assert extraction.status is status
    assert (extraction.values.latitude is not None) is has_coords


def test_the_extractor_does_not_decide_whether_it_is_the_right_property() -> None:
    """Istanbul reads perfectly well as a coordinate. Policy lives elsewhere."""
    istanbul = extract_location(normalise("41.0082, 28.9784"))
    assert istanbul.status is VALID
    assert istanbul.values.latitude == pytest.approx(41.0082)


# ---------------------------------------------------------------------------
# System size
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "status", "value"),
    [
        ("6 kWp", VALID, 6.0),
        ("the middle option", VALID, 6.0),
        ("smallest", VALID, 3.6),
        ("largest option", VALID, 9.6),
        ("15 panels", VALID, 6.0),
        ("fifteen panels", VALID, 6.0),
        ("twenty four panels", VALID, 9.6),
        ("5 kWp", INVALID, None),
        ("7 panels", INVALID, None),
        ("100 panels", INVALID, None),
        ("banana", ABSENT, None),
    ],
)
def test_system_size_extraction(text, status, value) -> None:
    extraction = extract_system_size(normalise(text))
    assert extraction.status is status
    assert extraction.values.system_size_kwp == value


def test_a_bare_adjective_is_not_a_selection() -> None:
    """`large` and `full` were in the size vocabulary, so "how large is the
    roof?" selected 9.6 kWp. `largest` still does what it should."""
    assert extract_system_size(normalise("large")).status is ABSENT
    assert extract_system_size(normalise("the full roof")).status is ABSENT
    assert extract_system_size(normalise("largest")).values.system_size_kwp == 9.6


def test_last_is_a_time_word_unless_it_names_a_choice() -> None:
    """Found while writing the live probes: bare `last` was in the vocabulary,
    so "about the same as we used last winter" selected 9.6 kWp."""
    for time_phrase in ("last winter", "same as last month", "what we paid last year"):
        assert extract_system_size(normalise(time_phrase)).status is ABSENT, time_phrase

    for choice in ("the last one", "last option", "the last size"):
        assert extract_system_size(normalise(choice)).values.system_size_kwp == 9.6, choice


def test_a_panel_count_is_read_in_text_order() -> None:
    """The old word scan returned the first match in *dict* order, so
    "fifteen or nine panels" selected 3.6 kWp."""
    assert extract_system_size(normalise("fifteen or nine panels")).values.system_size_kwp == 3.6
    assert extract_system_size(normalise("nine or fifteen panels")).values.system_size_kwp == 6.0
