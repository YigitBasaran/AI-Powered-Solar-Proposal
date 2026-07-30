"""Is this message a question, and what is it about?

Deterministic, because the answer has to be right with no model present. The
detector runs *before* every value extractor, which is what stops "how large is
the roof?" from selecting a 9.6 kWp system and "why do you need my location?"
from being stored as a location.
"""

from __future__ import annotations

import re

from app.services.conversation.actions import ActionKind, Topic
from app.services.conversation.normalise import Normalised
from app.services.conversation.numbers import NUMBER_WORD_TOKENS

# Politeness and discourse markers that can precede a real question.
FILLERS = r"(?:ok|okay|so|and|but|well|hey|hi|hello|please|sorry|actually|thanks|thank you|" \
          r"just|quick|question|um|erm)"

INTERROGATIVE = r"(?:what|why|how|when|where|which|who|whose|whom)"
AUXILIARY = r"(?:can|could|do|does|did|is|are|was|were|should|would|will|shall|may|might|" \
            r"am|have|has|cannot)"
IMPERATIVE_ASK = r"(?:explain|tell|show|help|define|clarify|describe)"

_LEADING_FILLERS = re.compile(rf"^(?:{FILLERS}[\s,]+)+")

#: Q1 - the message opens with an interrogative, an auxiliary, or a request to
#: be told something.
_Q1 = re.compile(rf"^(?:{INTERROGATIVE}|{AUXILIARY}|{IMPERATIVE_ASK})\b")

#: Q2 - an interrogative clause embedded mid-sentence: "...and what is the
#: payback", "I get that, but how many panels fit".
_Q2 = re.compile(
    rf"\b{INTERROGATIVE}\s+(?:{AUXILIARY}|much|many|long|big|large|wide|far|about|exactly|come)\b"
)

#: Q3 - phrasings that are questions without an interrogative in front.
_Q3 = re.compile(
    r"(?:"
    r"tell me|let me know|"
    r"what does .{0,40}\bmean\b|what do you mean|"
    r"how come|"
    r"how (?:did|do) you (?:get|got|work|calculate|arrive|come up)|"
    r"where (?:does|did|do) .{0,40}\bcome from\b|"
    r"why do you need|"
    r"what if\b|what are my options|what happens (?:if|when)|"
    r"can you (?:explain|tell|show|walk)|"
    r"i (?:do not|don) understand|"
    r"not sure (?:what|why|how)|"
    r"no idea (?:what|why|how)|"
    r"is that (?:right|correct|accurate)"
    r")"
)

#: Tokens that do not make a `?` into a question. "1150?" and "6 kWp?" are
#: someone checking a value, not asking about the world.
_UNIT_TOKENS = frozenset(
    {"kwh", "kw", "kwp", "wp", "mwh", "eur", "euro", "euros", "usd", "dollar", "dollars",
     "m2", "sqm", "panels", "panel", "yes", "no", "ok", "okay"}
)

_WORD = re.compile(r"[a-z]+")


def _has_qualifying_word(text: str) -> bool:
    """A `?` counts only when something other than a bare value precedes it."""
    for token in _WORD.findall(text):
        if token in _UNIT_TOKENS or token in NUMBER_WORD_TOKENS:
            continue
        return True
    return False


def is_question(message: Normalised) -> bool:
    """True when the message asks something rather than answering something.

    Runs ahead of the value extractors deliberately. The alternative - extract
    first, classify second - is how "how large is the roof?" came to select a
    9.6 kWp system: `large` is in the size vocabulary.
    """
    text = message.text
    if not text:
        return False

    stripped = _LEADING_FILLERS.sub("", text).lstrip(" ,.;:")

    if _Q1.search(stripped):
        return True
    if _Q2.search(text):
        return True
    if _Q3.search(text):
        return True
    return bool(text.rstrip().endswith("?") and _has_qualifying_word(text))


#: Asking to see the choices, rather than asking about them.
#:
#: Matched on the *noun*, not on a well-formed interrogative in front of it.
#: The message that prompted this redesign was "whicj options that we have?" -
#: one typo away from every pattern keyed on `what|which`, and answered as an
#: unreadable consumption figure because of it. This is only ever consulted for
#: messages already classified as questions, so a bare `options` cannot capture
#: anything that was not one.
_OPTIONS = re.compile(
    r"\boptions?\b|\bchoices\b"
    r"|\b(?:what|which)\s+(?:are\s+)?(?:the\s+)?(?:sizes|systems)\b"
    r"|\bwhat (?:can|could) i (?:choose|pick)\b|\blist the (?:options|sizes)\b"
)

#: Asking for the reasoning behind something already stated.
_EXPLANATION = re.compile(
    r"\b(?:why|how come)\b|\bexplain\b|\bhow (?:did|do) you\b|\bwhat does .{0,40}\bmean\b"
    r"|\bhow is .{0,40}\b(?:calculated|measured|derived|worked out)\b"
)


def question_kind(message: Normalised) -> ActionKind:
    """Which flavour of question, so the answer can be shaped to it."""
    text = message.text
    if _OPTIONS.search(text):
        return ActionKind.REQUEST_OPTIONS
    if _EXPLANATION.search(text):
        return ActionKind.REQUEST_EXPLANATION
    return ActionKind.ASK_QUESTION


#: Ordered. First match wins, so the more specific topics come first.
#:
#: A bare `kwh` is deliberately **not** a yield trigger. It was, and that is
#: precisely why "-500 kWh" at the consumption step classified as an energy
#: question rather than an unusable figure. It counts only when it is clearly
#: talking about a rate or a period.
_TOPIC_PATTERNS: tuple[tuple[Topic, re.Pattern[str]], ...] = (
    (Topic.PRIVACY, re.compile(
        r"\b(privacy|gdpr|my data|personal data|stored|storing|store my|delete|who sees|"
        r"share my|third part)\b")),
    (Topic.LOCATION, re.compile(
        r"\b(location|address|coordinate|coordinates|latitude|longitude|lat|lon|geocod|"
        r"cape town|where is (?:the|this|my)|which (?:house|property|building))\b")),
    (Topic.PROPOSAL, re.compile(
        r"\b(proposal|pdf|share link|shareable|download|report|document|finalis|finaliz)\b")),
    (Topic.FX, re.compile(
        r"\b(exchange rate|usd|eur\b|euro|currency|conversion|convert|ecb|frankfurter|"
        r"dollar)\b")),
    # `save` as a verb, not just the noun: "what will I save?" is the most
    # natural way to ask about the annual saving and matched nothing before.
    # PRIVACY is checked first, so "save my data" is still a privacy question.
    (Topic.FINANCE, re.compile(
        r"\b(payback|save|saves|saved|saving|savings|cost|costs|capex|price|tariff|roi|"
        r"return|cash flow|net benefit|money|invoice|bill total)\b")),
    (Topic.YIELD, re.compile(
        r"\b(production|produce|generat|yield|output|pvgis|irradiat|shading|shade|"
        r"cloudy|weather|seasonal|kwh (?:a|per) (?:year|month)|per kwh)\b")),
    # Narrow on `panels` deliberately: a broad match here would take "how many
    # panels does the 9.6 kWp option have?", which is a question about the
    # system options, not about where they ended up.
    (Topic.LAYOUT, re.compile(
        r"\b(layout|placement|placed|place|fits?|fitting|spacing|gap|setback|portrait|"
        r"landscape|arrangement|overlap)\b"
        r"|\b(?:no|any|zero) panels\b|\bpanels? (?:on|across|per)\b"
        r"|\b(?:one|each|which|that) (?:side|facet)\b")),
    (Topic.ROOF, re.compile(
        r"\b(roof|facet|facets|pitch|azimuth|ridge|hip|hips|eave|eaves|edge|edges|tilt|"
        r"orientation|slope|square met|area|measurement|satellite|imagery)\b")),
    # `option` and `choice` land here: the only thing this workflow offers a
    # choice of is the system size. PROPOSAL, FX and FINANCE are checked first,
    # so "what are my options for sharing the proposal" still goes there.
    (Topic.SYSTEM_SIZE, re.compile(
        r"\b(system size|kwp|panel count|how many panels|which size|which option|"
        r"options?|choices?|the three|three sizes|3\.6|9\.6)\b")),
    (Topic.CONSUMPTION, re.compile(
        r"\b(consumption|usage|use per|my bill|units a month|monthly use|kwh|kilowatt hour|"
        r"annual|monthly)\b")),
)


def classify_topic(message: Normalised, *, default: Topic = Topic.GENERAL) -> Topic:
    """What the message is about. Ordered; the first pattern to match wins."""
    text = message.text
    for topic, pattern in _TOPIC_PATTERNS:
        if pattern.search(text):
            return topic
    return default


def topic_named_in(message: Normalised) -> Topic | None:
    """The topic the message itself names, or None.

    The difference from `classify_topic` is the absence of a default, and it
    matters: a topic inherited from the current step is not evidence about what
    the customer asked, so it must not be allowed to select a canned answer for
    them.
    """
    for topic, pattern in _TOPIC_PATTERNS:
        if pattern.search(message.text):
            return topic
    return None


__all__ = ["classify_topic", "is_question", "question_kind", "topic_named_in"]
