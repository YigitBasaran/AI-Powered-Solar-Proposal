"""Telling a question from an answer, without a language model.

The named regression tests at the bottom each correspond to a defect found in
the shipped parser. They are the reason the detector runs *before* the value
extractors rather than after them.
"""

from __future__ import annotations

import pytest

from app.services.conversation.actions import ActionKind, Topic
from app.services.conversation.normalise import normalise
from app.services.conversation.questions import classify_topic, is_question, question_kind


def _q(text: str) -> bool:
    return is_question(normalise(text))


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        # Q1 - leading interrogative or auxiliary
        "what is kWp?",
        "why do you need my location",
        "how are the measurements calculated",
        "which options do we have?",
        "can I give annual consumption",
        "is the exchange rate live",
        "does this include shading",
        "should I pick the biggest one",
        # Q1 - imperative ask
        "explain the payback calculation",
        "tell me about PVGIS",
        "help",
        # Q2 - embedded interrogative
        "I get that, but what is the payback",
        "sorry, how many panels fit on the north facet",
        # Q3 - phrasal
        "I don't understand kWp",
        "what does kWp mean?",
        "how come there are only three sizes",
        "where does the exchange rate come from",
        "what happens if the FX service is unavailable",
        "what are my options",
        # Q4 - terminal question mark with a real word
        "shading?",
        "north facing?",
    ],
)
def test_questions_are_recognised(text) -> None:
    assert _q(text) is True


# ---------------------------------------------------------------------------
# Not questions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "1150",
        "1,150 kWh",
        "6 kWp",
        "middle",
        "the largest option",
        "15 panels",
        "-34.04658, 18.46491",
        "approximately one thousand one hundred per month",
        "yes",
        "banana",
        "asdfghjkl",
        "London",
        "10 Downing Street, London",
        "not a number at all",
        "-500 kWh",
        "0",
    ],
)
def test_answers_are_not_mistaken_for_questions(text) -> None:
    assert _q(text) is False


@pytest.mark.parametrize("text", ["???", "1150?", "6 kWp?", "9.6?", "!!!"])
def test_a_question_mark_alone_does_not_make_a_question(text) -> None:
    """Otherwise `1150?` stops being a consumption figure, and `???` becomes a
    question the workflow has to answer instead of re-prompting."""
    assert _q(text) is False


# ---------------------------------------------------------------------------
# Shape and topic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("which options do we have?", ActionKind.REQUEST_OPTIONS),
        ("what are my options", ActionKind.REQUEST_OPTIONS),
        ("why is north-facing better here?", ActionKind.REQUEST_EXPLANATION),
        ("how is payback calculated?", ActionKind.REQUEST_EXPLANATION),
        ("is this a site survey?", ActionKind.ASK_QUESTION),
    ],
)
def test_question_shape(text, kind) -> None:
    assert question_kind(normalise(text)) is kind


@pytest.mark.parametrize(
    ("text", "topic"),
    [
        ("do you store my address?", Topic.PRIVACY),
        ("why do you need my location?", Topic.LOCATION),
        ("what does kWp mean?", Topic.SYSTEM_SIZE),
        ("what is roof pitch?", Topic.ROOF),
        ("why are there no panels on one side?", Topic.LAYOUT),
        ("does this include cloudy months?", Topic.YIELD),
        ("is the exchange rate live?", Topic.FX),
        ("how is annual savings calculated?", Topic.FINANCE),
        ("can I download the PDF again?", Topic.PROPOSAL),
        ("which unit should I use, kWh?", Topic.CONSUMPTION),
    ],
)
def test_topic_classification(text, topic) -> None:
    assert classify_topic(normalise(text)) is topic


# ---------------------------------------------------------------------------
# Named regressions - each of these shipped
# ---------------------------------------------------------------------------


def test_a_location_question_is_not_stored_as_a_location() -> None:
    """`parse_location` accepted any text with three letters, so this was
    recorded as the customer's address and advanced the workflow."""
    assert _q("why do you need my location?") is True


def test_a_roof_question_does_not_select_a_system_size() -> None:
    """`large` was in the size vocabulary, so this selected 9.6 kWp."""
    message = normalise("how large is the roof?")
    assert is_question(message) is True
    assert classify_topic(message) is Topic.ROOF


def test_a_polite_prefix_does_not_turn_a_question_into_a_confirmation() -> None:
    """`ok` matched CONFIRM_WORDS, which outranked question classification."""
    assert _q("ok, what's the payback?") is True
    assert _q("yes please, and tell me the payback") is True


def test_an_unusable_figure_is_not_a_question_about_energy() -> None:
    """A bare `kwh` in the yield pattern made `-500 kWh` an energy question.

    It has to reach the extractor instead, which reports a figure that was read
    and rejected - a different message from one that could not be read at all.
    """
    assert _q("-500 kWh") is False
    assert _q("0") is False
