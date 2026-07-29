"""What is the user trying to do?

An explicit priority pipeline, deterministic all the way down. The order is the
design:

    0. normalise
    1. blank                       -> unknown
    2. QUESTION DETECTOR           -> ask_question | request_options | request_explanation
    3. reset / cancel              -> reset | cancel
    4. change / navigate           -> change_previous_value | navigate
    5. STEP EXTRACTOR (tri-state)  -> provide_value
    6. confirmation                -> confirm
    7. the model, if configured    -> whatever it says, re-validated
    8. deterministic fallback      -> off_topic | unknown

Two placements are worth defending.

**Questions come before reset and navigate.** "How do I start over?" is a
question about the mechanism, not an invocation of it. Answering it is strictly
better than silently entering a destructive flow, and the reset detector then
only ever sees an unquestioned imperative.

**Questions come before the extractors.** The alternative - extract first,
classify second - is how "how large is the roof?" came to select a 9.6 kWp
system and "why do you need my location?" came to be stored as an address.

The router never mutates anything. It reports; the state machine decides.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.config import LlmProvider, Settings, get_settings
from app.domain.models import ProjectStep
from app.services.conversation.actions import (
    ActionKind,
    ConversationAction,
    ExtractionStatus,
    Topic,
)
from app.services.conversation.extractors import (
    Extraction,
    extract_consumption,
    extract_location,
    extract_system_size,
)
from app.services.conversation.normalise import Normalised, normalise
from app.services.conversation.questions import classify_topic, is_question, question_kind
from app.services.conversation.telemetry import FallbackReason, Interpretation, rules_only

#: Wiping the answers so far is destructive, so the wording has to be explicit.
_RESET = re.compile(
    r"\b(start over|start again|start from scratch|begin again|reset|restart|"
    r"scrap (?:that|this|it)|clear everything|wipe (?:it|this|everything))\b"
)

_CANCEL = re.compile(r"\b(cancel|never ?mind|forget (?:it|that)|leave it|stop)\b")

#: A correction to something already given.
_CHANGE = re.compile(
    r"\b(actually|instead|correction|scratch that|i meant|i made a mistake|"
    r"change (?:it|the|my|to)|make it|update (?:it|the|my)|switch to|"
    r"use .{0,24}instead|let us (?:go|use|make) .{0,20}instead)\b"
)

_NAVIGATE = re.compile(
    r"\b(go back|back to|previous step|take me back|return to (?:the )?(?:location|consumption|"
    r"usage|system|size))\b"
)

#: Confirmation only when it is essentially the whole message. Otherwise
#: "yes please, and tell me the payback" is a confirmation with a question
#: stapled to it, and the question is the part that matters.
_CONFIRM = re.compile(
    r"\b(yes|yeah|yep|yup|sure|ok|okay|correct|confirm|confirmed|go ahead|proceed|"
    r"sounds good|that is right|thats right|that s right|continue|do it|please do)\b"
)
_MAX_CONFIRM_TOKENS = 5

_FILLER_PREFIX = re.compile(r"^(?:(?:ok|okay|so|well|please|thanks|hi|hello)[\s,]+)+")

#: Requests the application will not service, however they are phrased.
_UNSUPPORTED = re.compile(
    r"\b(set (?:the )?(?:exchange rate|rate|production|payback|savings?|price)|"
    r"ignore (?:the )?(?:rules|workflow|previous|all previous)|"
    r"override|force (?:the )?(?:rate|price|production)|"
    r"pretend|disregard (?:the )?(?:rules|instructions))\b"
)

#: Steps that expect a value, and the extractor that reads it.
_EXTRACTORS = {
    ProjectStep.LOCATION: (Topic.LOCATION, extract_location),
    ProjectStep.CONSUMPTION: (Topic.CONSUMPTION, extract_consumption),
    ProjectStep.SYSTEM_SIZE: (Topic.SYSTEM_SIZE, extract_system_size),
}


@dataclass(frozen=True)
class RoutedMessage:
    """The router's verdict, plus how it was reached."""

    action: ConversationAction
    interpretation: Interpretation
    message: Normalised


def _confirm_tokens(text: str) -> int:
    return len(_FILLER_PREFIX.sub("", text).split())


def _change_target(message: Normalised) -> tuple[Topic, Extraction]:
    """Which value a correction is about, and what it changes it to."""
    topic = classify_topic(message)
    if topic is Topic.SYSTEM_SIZE:
        return topic, extract_system_size(message)
    if topic is Topic.CONSUMPTION:
        return topic, extract_consumption(message)
    if topic is Topic.LOCATION:
        return topic, extract_location(message)

    # Unlabelled. A size signal wins: "change it to 6" is far more likely to be
    # a system size than six kilowatt-hours a month.
    size = extract_system_size(message)
    if size.status is ExtractionStatus.VALID:
        return Topic.SYSTEM_SIZE, size
    consumption = extract_consumption(message)
    if consumption.status is ExtractionStatus.VALID:
        return Topic.CONSUMPTION, consumption
    return topic, size if size.read_a_quantity else consumption


def _navigation_target(text: str) -> ProjectStep | None:
    if re.search(r"\b(location|address|coordinate)\b", text):
        return ProjectStep.LOCATION
    if re.search(r"\b(consumption|usage)\b", text):
        return ProjectStep.CONSUMPTION
    if re.search(r"\b(system|size)\b", text):
        return ProjectStep.SYSTEM_SIZE
    return None


def classify(message: Normalised, *, step: ProjectStep) -> ConversationAction | None:
    """The deterministic half of the pipeline. `None` means "ask the model"."""
    if message.is_blank:
        return ConversationAction(kind=ActionKind.UNKNOWN)

    text = message.text

    # 2. A question, before anything can consume it as a value.
    if is_question(message):
        return ConversationAction(
            kind=question_kind(message),
            topic=classify_topic(message, default=_topic_for(step)),
            question=message.raw.strip(),
        )

    # An instruction the application will not follow, whatever it is dressed as.
    if _UNSUPPORTED.search(text):
        return ConversationAction(
            kind=ActionKind.UNSUPPORTED_REQUEST,
            topic=classify_topic(message, default=_topic_for(step)),
            question=message.raw.strip(),
        )

    # 3. Destructive commands.
    if _RESET.search(text):
        return ConversationAction(kind=ActionKind.RESET, topic=Topic.GENERAL)
    if _CANCEL.search(text):
        return ConversationAction(kind=ActionKind.CANCEL, topic=Topic.GENERAL)

    # 4. Corrections and navigation.
    if _NAVIGATE.search(text):
        target = _navigation_target(text)
        return ConversationAction(
            kind=ActionKind.NAVIGATE,
            topic=_topic_for(target) if target else Topic.GENERAL,
            target_step=target,
        )
    if _CHANGE.search(text):
        topic, extraction = _change_target(message)
        return ConversationAction(
            kind=ActionKind.CHANGE_PREVIOUS_VALUE,
            topic=topic,
            values=extraction.values,
            extraction=extraction.status,
            reason=extraction.reason,
        )

    # 5. The value this step is waiting for.
    if step in _EXTRACTORS:
        topic, extractor = _EXTRACTORS[step]
        extraction = extractor(message)
        if extraction.read_a_quantity:
            return ConversationAction(
                kind=ActionKind.PROVIDE_VALUE,
                topic=topic,
                values=extraction.values,
                extraction=extraction.status,
                reason=extraction.reason,
            )

    # 6. A bare confirmation.
    if _CONFIRM.search(text) and _confirm_tokens(text) <= _MAX_CONFIRM_TOKENS:
        return ConversationAction(kind=ActionKind.CONFIRM, topic=_topic_for(step))

    return None


def _topic_for(step: ProjectStep | None) -> Topic:
    match step:
        case ProjectStep.LOCATION:
            return Topic.LOCATION
        case ProjectStep.CONSUMPTION:
            return Topic.CONSUMPTION
        case ProjectStep.SYSTEM_SIZE:
            return Topic.SYSTEM_SIZE
        case ProjectStep.ROOF_RECONSTRUCTION:
            return Topic.ROOF
        case ProjectStep.PANEL_LAYOUT:
            return Topic.LAYOUT
        case ProjectStep.ENERGY_YIELD:
            return Topic.YIELD
        case ProjectStep.EXCHANGE_RATE:
            return Topic.FX
        case ProjectStep.FINANCIAL_ANALYSIS:
            return Topic.FINANCE
        case ProjectStep.PROPOSAL | ProjectStep.COMPLETED:
            return Topic.PROPOSAL
        case _:
            return Topic.GENERAL


async def route_message(
    text: str, *, step: ProjectStep, settings: Settings | None = None
) -> RoutedMessage:
    """Classify one message, escalating to the model only when rules cannot."""
    settings = settings or get_settings()
    message = normalise(text)

    deterministic = classify(message, step=step)
    if deterministic is not None:
        return RoutedMessage(
            action=deterministic,
            interpretation=rules_only(
                settings.llm_provider.value, reason=FallbackReason.RULES_SUFFICIENT
            ),
            message=message,
        )

    fallback = ConversationAction(
        kind=ActionKind.UNKNOWN, topic=classify_topic(message, default=_topic_for(step))
    )

    if settings.llm_provider is not LlmProvider.OLLAMA:
        return RoutedMessage(
            action=fallback,
            interpretation=rules_only(
                settings.llm_provider.value, reason=FallbackReason.NOT_CONFIGURED
            ),
            message=message,
        )

    from app.services.conversation.llm import classify_with_model

    action, interpretation = await classify_with_model(
        message, step=step, settings=settings, fallback=fallback
    )
    return RoutedMessage(action=action, interpretation=interpretation, message=message)


__all__ = ["RoutedMessage", "classify", "route_message"]
