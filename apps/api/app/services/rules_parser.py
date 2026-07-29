"""The `ParsedChatMessage` view of the deterministic conversation router.

This module used to *be* the parser. It is now a compatibility seam, kept for
one reason worth stating plainly: eighty test cases in
`tests/unit/test_rules_parser.py` pin every phrasing the brief demonstrates,
and pointing them at the new router proves the rewrite preserved behaviour. If
they were re-pointed at new function names instead, the rewrite would have
deleted its own regression net at exactly the moment it needed one.

What changed underneath:

* a **question is recognised before any extractor runs**, so "how large is the
  roof?" no longer selects a 9.6 kWp system and "why do you need my location?"
  is no longer stored as an address;
* extraction is **tri-state**, so "-500 kWh" is a figure that was read and
  rejected rather than a question about energy;
* consumption reads **written numbers** as well as digits.

The real implementation lives in `services/conversation/`.
"""

from __future__ import annotations

from app.domain.models import ChatIntent, ParsedChatMessage, ProjectStep
from app.services.conversation.actions import ActionKind, ConversationAction
from app.services.conversation.compat import to_parsed_chat_message
from app.services.conversation.extractors import PANEL_COUNT_TO_SIZE
from app.services.conversation.normalise import normalise
from app.services.conversation.questions import classify_topic
from app.services.conversation.router import classify

#: Re-exported because tests and older callers import them from here.
ALLOWED_SIZES = (3.6, 6.0, 9.6)

__all__ = [
    "ALLOWED_SIZES",
    "PANEL_COUNT_TO_SIZE",
    "classify_question",
    "parse_message",
]


def classify_question(text: str) -> ChatIntent:
    """Which of the three legacy `ASK_*` buckets a question falls into."""
    message = normalise(text)
    topic = classify_topic(message)
    return to_parsed_chat_message(
        ConversationAction(kind=ActionKind.ASK_QUESTION, topic=topic)
    ).intent


def parse_message(text: str, *, step: str) -> ParsedChatMessage:
    """Parse `text` in the context of `step`, deterministically.

    Returns `UNKNOWN` with zero confidence when nothing can be read safely,
    which is the caller's signal to try the language model.
    """
    try:
        project_step = ProjectStep(step)
    except ValueError:
        project_step = ProjectStep.COMPLETED

    action = classify(normalise(text), step=project_step)
    if action is None:
        # The deterministic pipeline declined; the model has not been asked.
        return ParsedChatMessage(intent=ChatIntent.UNKNOWN, confidence=0.0)
    return to_parsed_chat_message(action)
