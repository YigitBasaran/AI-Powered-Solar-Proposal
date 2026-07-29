"""Composes the reply to a question. Never mutates anything.

The source hierarchy is enforced by construction rather than by instruction:

1. a finalised proposal snapshot,
2. the current analysis - **only while provably fresh**,
3. case configuration and fixed assumptions,
4. curated help knowledge,
5. an LLM paraphrase of the above,
6. an explicit "I don't know".

Items 1-4 are chosen inside :mod:`facts` and :mod:`knowledge`; this module
turns whichever one applies into text. Item 5 is the interesting one. A prompt
saying "only use the supplied facts" is a request, not a guarantee, so the
model is never given the snapshot at all: it receives the closed fact bundle
and the *already-composed deterministic answer*, and is asked to reword. Then
every number it wrote is checked against the values it was given, reusing the
same whitelist that guards the executive summary. An LLM answer is therefore a
view of a higher source by construction, and a failed check costs nothing -
the deterministic text was already written.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import Any

from app.core.config import LlmProvider, Settings, get_settings
from app.core.errors import LlmUnavailableError
from app.services.conversation.actions import (
    ActionKind,
    Answer,
    AnswerSource,
    AnswerState,
    ConversationAction,
    Topic,
)
from app.services.conversation.facts import FactBundle, build_facts
from app.services.conversation.knowledge import HelpEntry, find_entry
from app.services.summary import ALWAYS_ALLOWED, unsupported_numbers

logger = logging.getLogger("solarvis.conversation.answers")

MAX_WORDS = 130

#: What a topic needs before it can be answered from computed figures. Used to
#: say *which* input is still missing rather than "not yet".
NEEDS: dict[Topic, tuple[str, ...]] = {
    Topic.LAYOUT: ("a system size",),
    Topic.YIELD: ("a system size",),
    Topic.FINANCE: ("your monthly consumption", "a system size"),
    Topic.FX: ("a system size",),
    Topic.CONSUMPTION: ("your monthly consumption",),
    Topic.SYSTEM_SIZE: ("a system size",),
}


# ---------------------------------------------------------------------------
# Formatting one topic from a fact bundle
# ---------------------------------------------------------------------------


def _num(value: Any, places: int = 0) -> str:
    try:
        return f"{float(value):,.{places}f}"
    except (TypeError, ValueError):
        return str(value)


def _roof_text(v: dict[str, Any]) -> str:
    facets = v.get("facets") or []
    edges = v.get("edgeCounts") or {}
    parts = [
        f"The roof has {v.get('facetCount', len(facets))} facets at a "
        f"{_num(v.get('pitchDeg'), 0)}-degree pitch, "
        f"{_num(v.get('totalSlopedAreaM2'), 1)} m2 of sloped area in total "
        f"({_num(v.get('totalProjectedAreaM2'), 1)} m2 seen from above)."
    ]
    if facets:
        listed = ", ".join(
            f"{f['label']} facing {_num(f['compassAzimuthDeg'], 0)} degrees at "
            f"{_num(f['slopedAreaM2'], 1)} m2"
            for f in facets
        )
        parts.append(f"By facet: {listed}.")
    if edges:
        parts.append(
            "Edges: "
            + ", ".join(f"{count} {kind}" for kind, count in edges.items() if count)
            + "."
        )
    return " ".join(parts)


def _layout_text(v: dict[str, Any]) -> str:
    parts = [
        f"{v.get('placedPanelCount')} panels are placed, giving a feasible "
        f"{_num(v.get('feasibleSystemSizeKwp'), 1)} kWp against the "
        f"{_num(v.get('requestedSystemSizeKwp'), 1)} kWp requested "
        f"({v.get('requestedPanelCount')} panels)."
    ]
    facets = v.get("facets") or []
    if facets:
        parts.append(
            "Per facet: "
            + ", ".join(
                f"{f['facetId']} holds {f['panelCount']} in {f['orientation']}" for f in facets
            )
            + "."
        )
    if v.get("capacityWarning"):
        parts.append(str(v["capacityWarning"]))
    return " ".join(parts)


def _energy_text(v: dict[str, Any]) -> str:
    parts = [
        f"Modelled production is {_num(v.get('totalAnnualProductionKwh'))} kWh a year "
        f"from {_num(v.get('installedPowerKwp'), 1)} kWp installed, "
        f"from {v.get('radiationDatabase') or 'PVGIS'} ({v.get('dataSource')})."
    ]
    facets = v.get("facets") or []
    if facets:
        parts.append(
            "By facet: "
            + ", ".join(
                f"{f['facetId']} {_num(f['annualProductionKwh'])} kWh "
                f"({_num(f['specificYieldKwhPerKwp'])} kWh per kWp)"
                for f in facets
            )
            + "."
        )
    return " ".join(parts)


def _fx_text(v: dict[str, Any]) -> str:
    return (
        f"The {v.get('baseCurrency')}/{v.get('quoteCurrency')} rate is {v.get('rate')}, "
        f"the {v.get('dataProvider')} reference rate for {v.get('rateDate')}, read via "
        f"{v.get('sourceApi')} ({v.get('retrievalSource')})."
    )


def _finance_text(v: dict[str, Any]) -> str:
    payback = v.get("simplePaybackYears")
    parts = [
        f"Annual consumption is {_num(v.get('annualConsumptionKwh'))} kWh against "
        f"{_num(v.get('annualProductionKwh'))} kWh produced, so "
        f"{_num(v.get('coveredEnergyKwh'))} kWh is covered "
        f"({_num(v.get('coveragePercent'), 1)}%), worth EUR "
        f"{_num(v.get('annualSavingsEur'))} a year.",
        f"The capital cost of {v.get('originalCapex', {}).get('currency')} "
        f"{_num(v.get('originalCapex', {}).get('amount'))} converts to EUR "
        f"{_num(v.get('convertedCapex', {}).get('amount'), 2)}.",
    ]
    if payback:
        parts.append(
            f"Simple payback is {_num(payback, 1)} years, with a net benefit of EUR "
            f"{_num(v.get('twentyYearNetBenefitEur'))} over the analysis period."
        )
    else:
        parts.append("The system does not pay back within the analysis period.")
    return " ".join(parts)


def _case_text(topic: Topic, v: dict[str, Any]) -> str:
    if topic is Topic.LOCATION:
        return (
            f"The analysis runs at {_num(v.get('resolvedLatitude'), 6)}, "
            f"{_num(v.get('resolvedLongitude'), 6)} - Galway Road, Cape Town."
        )
    if topic is Topic.SYSTEM_SIZE:
        counts = v.get("panelCounts") or {}
        return "Available sizes: " + ", ".join(
            f"{size} kWp ({counts.get(size)} panels)" for size in counts
        )
    if topic is Topic.CONSUMPTION:
        return (
            f"Electricity is priced at EUR {v.get('electricityPriceEurPerKwh')} per kWh "
            f"for this case."
        )
    return ""


_FORMATTERS = {
    Topic.ROOF: _roof_text,
    Topic.LAYOUT: _layout_text,
    Topic.YIELD: _energy_text,
    Topic.FX: _fx_text,
    Topic.FINANCE: _finance_text,
}


def describe(facts: FactBundle) -> str:
    """The computed figures for a topic, or "" when there are none to give."""
    if not facts.has_values:
        return ""
    formatter = _FORMATTERS.get(facts.topic)
    if formatter is not None:
        return formatter(facts.values)
    # System size answers from the layout section, which has its own shape.
    if facts.topic is Topic.SYSTEM_SIZE and "placedPanelCount" in facts.values:
        return _layout_text(facts.values)
    if facts.topic is Topic.CONSUMPTION and "annualConsumptionKwh" in facts.values:
        return _finance_text(facts.values)
    return _case_text(facts.topic, facts.values)


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


def _needed(topic: Topic) -> str:
    needs = NEEDS.get(topic)
    if not needs:
        return "the analysis to finish"
    if len(needs) == 1:
        return needs[0]
    return f"{', '.join(needs[:-1])} and {needs[-1]}"


def _stale_names(inputs: frozenset[str]) -> str:
    labels = {
        "monthly_consumption_kwh": "your monthly consumption",
        "selected_system_size_kwp": "the system size",
    }
    named = [labels[i] for i in sorted(inputs) if i in labels]
    if not named:
        return "an input you changed"
    if len(named) == 1:
        return named[0]
    return f"{' and '.join(named)}"


def compose(
    *,
    action: ConversationAction,
    facts: FactBundle,
    entry: HelpEntry | None,
    settings: Settings,
) -> Answer:
    """The deterministic answer. Always produced, even when a model is available.

    It is the fallback *and* the reference: the paraphrase gate below judges
    the model's output against the numbers this text was built from.
    """
    topic = facts.topic
    help_text = entry.render(settings) if entry is not None else ""
    help_key = entry.key if entry is not None else None

    if facts.state is AnswerState.RECALCULATING:
        text = (
            f"I'm recalculating that now - you changed {_stale_names(facts.stale_inputs)}, "
            f"and I would rather not quote the previous figures as if they still "
            f"applied. It will be a moment."
        )
        if help_text:
            text = f"{text}\n\n{help_text}"
        return Answer(
            text=text,
            state=AnswerState.RECALCULATING,
            source=AnswerSource.HELP_KNOWLEDGE if help_text else AnswerSource.NONE,
            topic=topic,
            help_topic=help_key,
        )

    computed = describe(facts) if facts.state is AnswerState.ANSWERABLE_NOW else ""

    if computed:
        # Facts first, method second: the question was about the number.
        text = f"{computed}\n\n{help_text}" if help_text else computed
        return Answer(
            text=text,
            state=AnswerState.ANSWERABLE_NOW,
            source=facts.source,
            topic=topic,
            help_topic=help_key,
        )

    wants_a_figure = action.kind is ActionKind.ASK_QUESTION

    if facts.state is AnswerState.NOT_CALCULATED_YET and wants_a_figure:
        # The disjointness rule: not-calculated-yet *is* the methodology text,
        # plus what is still missing.
        #
        # Only for a question that wanted the figure. "Which options do we
        # have?" is answered in full by the options themselves; appending "I
        # still need a system size" to it reads as a refusal to answer a
        # question that was just answered.
        missing = f"I don't have that yet - I still need {_needed(topic)}."
        text = f"{help_text}\n\n{missing}" if help_text else missing
        return Answer(
            text=text,
            state=AnswerState.NOT_CALCULATED_YET,
            source=AnswerSource.HELP_KNOWLEDGE if help_text else AnswerSource.NONE,
            topic=topic,
            help_topic=help_key,
        )

    if help_text:
        return Answer(
            text=help_text,
            state=entry.state if entry is not None else AnswerState.ANSWERABLE_AS_METHODOLOGY,
            source=AnswerSource.HELP_KNOWLEDGE,
            topic=topic,
            help_topic=help_key,
        )

    return Answer(
        text=(
            "I don't have an answer for that one. I can explain any part of this "
            "workflow - the roof measurements, how panels are placed, where the "
            "production figures come from, the exchange rate, or how payback is "
            "worked out."
        ),
        state=AnswerState.UNSUPPORTED,
        source=AnswerSource.NONE,
        topic=topic,
    )


# ---------------------------------------------------------------------------
# The paraphrase gate
# ---------------------------------------------------------------------------

PARAPHRASE_SYSTEM = (
    "You reword one answer for a solar feasibility assistant. You are given the "
    "customer's question and the assistant's answer. Rewrite the answer so it "
    "reads naturally and addresses the question directly.\n"
    "Rules: keep every number exactly as written, invent no number, add no fact "
    "that is not in the answer, drop nothing material, stay under 120 words, and "
    "reply with the rewritten answer only."
)


def _paraphrase_allowed(facts: FactBundle, deterministic: str) -> set[Decimal]:
    """Numbers the paraphrase may contain: the facts, plus its own source text.

    Same construction as the executive-summary whitelist - the exact value and
    the rounded forms a writer would naturally reach for - but built from the
    fact bundle rather than a snapshot, because the bundle is all the model was
    given. Anything in the deterministic text is admissible by definition: it
    is the text the model was asked to reword.
    """
    allowed: set[Decimal] = set(ALWAYS_ALLOWED)

    def _add(value: Any) -> None:
        try:
            number = Decimal(str(value).replace(",", "").replace("%", ""))
        except (InvalidOperation, ValueError, TypeError):
            return
        allowed.add(number)
        for places in ("1", "0.1", "0.01"):
            allowed.add(number.quantize(Decimal(places)))

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            for child in node.values():
                _walk(child)
        elif isinstance(node, list):
            for child in node:
                _walk(child)
        else:
            _add(node)

    _walk(facts.values)
    for token in unsupported_numbers(deterministic, set()):
        _add(token)
    return allowed


async def paraphrase(
    *,
    question: str,
    answer: Answer,
    facts: FactBundle,
    settings: Settings,
) -> tuple[Answer, str | None]:
    """Return `(answer, rejection_reason)`; the deterministic text on any doubt.

    Five gates, the same shape as the executive summary: provider, availability,
    empty, word limit, number whitelist.
    """
    if settings.llm_provider is not LlmProvider.OLLAMA:
        return answer, "not_configured"

    from app.integrations.ollama import OllamaClient

    prompt = f"Question: {question}\n\nAnswer to reword:\n{answer.text}"
    try:
        text = await OllamaClient(settings).prose(PARAPHRASE_SYSTEM, prompt)
    except LlmUnavailableError as exc:
        logger.info("answers: model unavailable (%s); using deterministic text", exc)
        return answer, "unreachable"

    text = " ".join(text.split())
    if not text:
        return answer, "empty_response"
    if len(text.split()) > MAX_WORDS:
        logger.info("answers: paraphrase exceeded the word limit")
        return answer, "too_long"

    offenders = unsupported_numbers(text, _paraphrase_allowed(facts, answer.text))
    if offenders:
        logger.warning("answers: paraphrase contained unsupported numbers %s", offenders)
        return answer, "unsupported_numbers"

    return answer.model_copy(update={"text": text, "source": AnswerSource.LLM_PARAPHRASE}), None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def answer_question(
    *,
    action: ConversationAction,
    project: Any,
    settings: Settings | None = None,
) -> Answer:
    """The deterministic answer to one question. Reads state, changes none."""
    settings = settings or get_settings()
    facts = build_facts(project=project, settings=settings, topic=action.topic)
    entry = find_entry(action.question or "", action.topic)
    return compose(action=action, facts=facts, entry=entry, settings=settings)


def facts_for(action: ConversationAction, project: Any, settings: Settings) -> FactBundle:
    return build_facts(project=project, settings=settings, topic=action.topic)


__all__ = [
    "MAX_WORDS",
    "PARAPHRASE_SYSTEM",
    "Answer",
    "answer_question",
    "compose",
    "describe",
    "facts_for",
    "paraphrase",
]
