"""Projecting a `ConversationAction` back onto the older `ParsedChatMessage`.

`ParsedChatMessage` predates the router and describes only what a message
*supplied*, not what the person was doing. It is kept as a projection rather
than deleted so the parser suite - eighty cases covering every phrasing the
brief demonstrates - keeps testing the real classifier instead of a
reimplementation of it. Delete this file and those tests start describing
nothing.

The mapping is lossy on purpose: an action's *kind* has no home in the old
enum, so a question becomes one of the three `ASK_*` members chosen by topic,
and everything the old model cannot express becomes `UNKNOWN`.
"""

from __future__ import annotations

from app.domain.models import ChatIntent, ParsedChatMessage
from app.services.conversation.actions import (
    ActionKind,
    ConversationAction,
    ExtractionStatus,
    Topic,
)

#: Which `ASK_*` member a question's topic projects onto. The old enum had
#: three buckets, so several topics share one.
_QUESTION_INTENT: dict[Topic, ChatIntent] = {
    Topic.FX: ChatIntent.ASK_FINANCIAL_QUESTION,
    Topic.FINANCE: ChatIntent.ASK_FINANCIAL_QUESTION,
    Topic.PROPOSAL: ChatIntent.ASK_FINANCIAL_QUESTION,
    Topic.YIELD: ChatIntent.ASK_ENERGY_QUESTION,
    Topic.CONSUMPTION: ChatIntent.ASK_ENERGY_QUESTION,
    Topic.ROOF: ChatIntent.ASK_ROOF_QUESTION,
    Topic.LAYOUT: ChatIntent.ASK_ROOF_QUESTION,
    Topic.SYSTEM_SIZE: ChatIntent.ASK_ROOF_QUESTION,
}

_VALUE_INTENT: dict[Topic, ChatIntent] = {
    Topic.LOCATION: ChatIntent.PROVIDE_LOCATION,
    Topic.CONSUMPTION: ChatIntent.PROVIDE_CONSUMPTION,
    Topic.SYSTEM_SIZE: ChatIntent.SELECT_SYSTEM_SIZE,
}

_UNKNOWN = ParsedChatMessage(intent=ChatIntent.UNKNOWN, confidence=0.0)


def to_parsed_chat_message(action: ConversationAction) -> ParsedChatMessage:
    """The `ParsedChatMessage` view of an action."""
    if action.kind is ActionKind.PROVIDE_VALUE:
        if action.extraction is not ExtractionStatus.VALID:
            # A figure that was read and rejected supplies no value. The
            # *reason* survives on the action, which is what shapes the reply.
            return _UNKNOWN
        intent = _VALUE_INTENT.get(action.topic)
        if intent is None:
            return _UNKNOWN
        # A written place name is recognised but unverified; a coordinate is
        # exact. The old contract expressed that difference as confidence.
        exact = action.topic is not Topic.LOCATION or action.values.latitude is not None
        return ParsedChatMessage(
            intent=intent,
            confidence=1.0 if exact else 0.6,
            **action.values.model_dump(),
        )

    if action.is_question:
        intent = _QUESTION_INTENT.get(action.topic, ChatIntent.UNKNOWN)
        if intent is ChatIntent.UNKNOWN:
            return _UNKNOWN
        return ParsedChatMessage(intent=intent, confidence=0.7)

    if action.kind is ActionKind.CONFIRM:
        return ParsedChatMessage(intent=ChatIntent.CONFIRM, confidence=0.9)

    return _UNKNOWN


__all__ = ["to_parsed_chat_message"]
