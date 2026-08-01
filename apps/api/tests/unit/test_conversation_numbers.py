"""Reading a quantity out of English words, and refusing to guess one."""

from __future__ import annotations

import pytest

from app.services.conversation.normalise import normalise
from app.services.conversation.numbers import parse_word_number


def _value(text: str) -> float | None:
    return parse_word_number(normalise(text).text).value


def _reason(text: str) -> str | None:
    return parse_word_number(normalise(text).text).reason


# ---------------------------------------------------------------------------
# The forms the spec requires
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("eleven hundred", 1100),
        ("one thousand one hundred", 1100),
        ("one thousand one hundred and fifty", 1150),
        ("approximately one thousand one hundred per month", 1100),
        ("about eleven fifty", 1150),
        ("around twelve hundred monthly", 1200),
        ("nine hundred and fifty kwh", 950),
        ("twelve hundred kwh each month", 1200),
        ("eleven hundred units a month", 1100),
        ("a thousand", 1000),
        ("two thousand four hundred", 2400),
        ("thirty five hundred", 3500),
    ],
)
def test_written_numbers_are_read(text, expected) -> None:
    assert _value(text) == expected


def test_an_approximator_does_not_move_the_value() -> None:
    """ "around 1150" is 1150. Softening the phrasing does not soften the number."""
    assert _value("approximately one thousand one hundred") == 1100
    assert _value("roughly eleven hundred") == 1100
    assert _value("about eleven hundred") == 1100


# ---------------------------------------------------------------------------
# The refusals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "a little over a thousand",
        "quite high",
        "a lot",
        "no idea",
        "not sure",
        "the usual",
        "same as last year",
        "loads",
        "pretty high",
    ],
)
def test_genuinely_vague_quantities_are_refused(text) -> None:
    assert _value(text) is None


def test_a_directional_makes_a_parseable_magnitude_unusable() -> None:
    """ "a little over a thousand" contains 1000 and still is not an answer."""
    assert _value("a little over a thousand") is None
    assert _reason("a little over a thousand") == "vague_quantity"
    assert _value("just under eleven hundred") is None
    assert _value("more than two thousand") is None


def test_a_bare_small_word_number_is_not_a_monthly_consumption() -> None:
    assert _value("around one") is None
    assert _reason("around one") == "implausible_bare_word"


def test_two_competing_figures_are_refused_rather_than_picked_between() -> None:
    assert _value("eleven hundred or twelve hundred") is None
    assert _reason("eleven hundred or twelve hundred") == "multiple_figures"


# ---------------------------------------------------------------------------
# Regressions the design had to be shaped around
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("text", "expected"), [("twenty four", 24), ("thirty five", 35)])
def test_tens_plus_units_is_addition_not_the_colloquial_hundreds_form(text, expected) -> None:
    """ "twenty four" is 24. "eleven fifty" is 1150. Both must hold at once."""
    assert _value(text) == expected


@pytest.mark.parametrize("text", ["fifteen panels", "nine panels", "twentyfour modules"])
def test_a_count_of_panels_is_not_a_consumption_figure(text) -> None:
    assert _value(text) is None


def test_a_monthly_suffix_is_not_a_count_of_months() -> None:
    """`months?` without a word boundary swallowed the `month` in `monthly`."""
    assert _value("twelve hundred monthly") == 1200


def test_word_scanning_is_in_text_order() -> None:
    """The old `_word_number` returned the first match in *dict* order."""
    assert _value("fifteen hundred") == 1500
    assert _value("nineteen hundred") == 1900
