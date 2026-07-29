"""Normalisation: fold for matching, keep the original for the record."""

from __future__ import annotations

import pytest

from app.services.conversation.normalise import expand_contractions, normalise


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("what's", "what is"),
        ("How's", "how is"),
        ("where's", "where is"),
        ("who's", "who is"),
        ("that's", "that is"),
        ("there's", "there is"),
        ("it's", "it is"),
        ("can't", "cannot"),
        ("cant", "cannot"),
        ("won't", "will not"),
        ("doesn't", "does not"),
        ("don't", "do not"),
        ("didn't", "did not"),
        ("isn't", "is not"),
        ("aren't", "are not"),
        ("haven't", "have not"),
        ("couldn't", "could not"),
        ("shouldn't", "should not"),
        ("wouldn't", "would not"),
        ("i'm", "i am"),
        ("i've", "i have"),
        ("let's", "let us"),
    ],
)
def test_every_supported_contraction_expands(raw, expected) -> None:
    assert normalise(raw).text == expected


def test_the_typographic_apostrophe_is_folded_first() -> None:
    """A phone keyboard produces U+2019, not U+0027."""
    assert normalise("what’s the payback").text == "what is the payback"


def test_the_raw_message_is_never_altered() -> None:
    """`raw_location_input` and the transcript quote the customer, not us."""
    raw = "  What's   the PAYBACK?  "
    normalised = normalise(raw)
    assert normalised.raw == raw
    assert normalised.text == "what is the payback?"


def test_whitespace_and_case_are_folded_for_matching() -> None:
    assert normalise("  6   KWp\t\n").text == "6 kwp"


def test_a_minus_sign_becomes_a_hyphen() -> None:
    """U+2212 in a pasted latitude must not break coordinate parsing."""
    assert normalise("−34.0466, 18.4649").text == "-34.0466, 18.4649"


def test_tens_and_units_are_collapsed_so_they_cannot_read_as_hundreds() -> None:
    assert normalise("twenty four panels").text == "twentyfour panels"


@pytest.mark.parametrize("raw", ["", "   ", "\n\t"])
def test_blank_messages_are_recognised(raw) -> None:
    assert normalise(raw).is_blank is True


def test_expansion_is_word_bounded() -> None:
    """`cantilever` must not become `cannotilever`."""
    assert expand_contractions("cantilever") == "cantilever"
    assert expand_contractions("wonton") == "wonton"
