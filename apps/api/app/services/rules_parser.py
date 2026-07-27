"""Deterministic, step-aware parsing of user messages.

This runs *before* the language model, and for the phrasings the case actually
needs it succeeds every time - which is why the whole flow works with
`LLM_PROVIDER=rules` and no model pulled.

Being step-aware is what makes it safe. The bare token ``6`` means 6 kWp at the
system-size step and 6 kWh/month nowhere, while ``1150`` means consumption at
the consumption step and is not a plausible system size anywhere. Parsing
without knowing the step would have to guess between them; knowing the step
removes the ambiguity entirely.

Anything this module cannot parse confidently returns ``UNKNOWN`` and is handed
to the LLM, which is then validated against the same rules.
"""

from __future__ import annotations

import re

from app.domain.models import ChatIntent, ParsedChatMessage

# ---------------------------------------------------------------------------
# Number words
# ---------------------------------------------------------------------------

NUMBER_WORDS: dict[str, int] = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "twentyone": 21,
    "twentytwo": 22,
    "twentythree": 23,
    "twentyfour": 24,
}

ALLOWED_SIZES = (3.6, 6.0, 9.6)
PANEL_COUNT_TO_SIZE = {9: 3.6, 15: 6.0, 24: 9.6}

ORDINAL_SIZE_PHRASES: list[tuple[re.Pattern[str], float]] = [
    (re.compile(r"\b(smallest|lowest|cheapest|minimum|min|first|starter|small)\b"), 3.6),
    (re.compile(r"\b(middle|medium|mid|second|average|moderate)\b"), 6.0),
    (re.compile(r"\b(largest|biggest|maximum|max|last|third|highest|full|large)\b"), 9.6),
]

# A decimal pair, optionally parenthesised or labelled. The separator may carry
# a label ("lat -34.0466 lon 18.4649") as well as punctuation, so an optional
# longitude word is allowed between the two numbers.
COORD_PAIR = re.compile(
    r"(?P<lat>[-+]?\d{1,3}(?:\.\d+)?)"
    r"\s*(?:[,;/]\s*|\s+)"
    r"(?:lon(?:g(?:itude)?)?\.?\s*[:=]?\s*)?"
    r"(?P<lon>[-+]?\d{1,3}(?:\.\d+)?)"
)

# Numbers that may carry thousands separators: 1150, 1,150, 1 150. The sign is
# captured deliberately: dropping it would turn "-500" into a valid 500 kWh.
NUMERIC = re.compile(r"(?P<value>[-+]?\d{1,3}(?:[, ]\d{3})+(?:\.\d+)?|[-+]?\d+(?:\.\d+)?)")

CONFIRM_WORDS = re.compile(
    r"\b(yes|yeah|yep|sure|ok|okay|correct|confirm|confirmed|go ahead|proceed|"
    r"sounds good|that's right|thats right|continue)\b"
)

ROOF_QUESTION = re.compile(r"\b(roof|facet|pitch|azimuth|edge|ridge|hip|eave|area|panel)\b")
ENERGY_QUESTION = re.compile(r"\b(production|generate|generation|kwh|yield|energy|output)\b")
FINANCE_QUESTION = re.compile(
    r"\b(payback|saving|savings|cost|capex|price|financial|money|euro|eur|usd|"
    r"dollar|rate|exchange|return|roi)\b"
)


def _to_float(raw: str) -> float:
    return float(raw.replace(",", "").replace(" ", ""))


def _normalise(text: str) -> str:
    lowered = text.lower().strip()
    # "twenty-four" / "twenty four" -> "twentyfour" so one lookup covers both.
    lowered = re.sub(r"\b(twenty)[\s-]+(one|two|three|four)\b", r"\1\2", lowered)
    return lowered


def _word_number(text: str) -> int | None:
    for word, value in NUMBER_WORDS.items():
        if re.search(rf"\b{word}\b", text):
            return value
    return None


# ---------------------------------------------------------------------------
# Per-step parsers
# ---------------------------------------------------------------------------


def parse_location(text: str) -> ParsedChatMessage | None:
    """Extract a coordinate pair if one is present and in range.

    A location the user types is preserved verbatim by the caller; this only
    reports what was recognised. Resolution to the fixed case property happens
    in the workflow, not here.
    """
    for match in COORD_PAIR.finditer(text):
        lat = float(match.group("lat"))
        lon = float(match.group("lon"))
        if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
            return ParsedChatMessage(
                intent=ChatIntent.PROVIDE_LOCATION,
                latitude=lat,
                longitude=lon,
                confidence=1.0,
            )
    return None


def parse_consumption(text: str) -> ParsedChatMessage | None:
    """Extract a monthly consumption figure.

    Handles ``1150``, ``1,150``, ``1150 kWh``, ``around 1150 per month``. An
    annual figure is converted, because a user answering "13800 a year" is
    giving a valid answer to a question about monthly use.
    """
    normalised = _normalise(text)
    match = NUMERIC.search(normalised)
    if match is None:
        return None

    value = _to_float(match.group("value"))
    if value <= 0 or value > 1_000_000:
        return None

    tail = normalised[match.end() : match.end() + 24]
    if re.search(r"\b(per year|a year|annual|annually|yearly|/year|p\.?a\.?)\b", tail):
        value /= 12.0

    return ParsedChatMessage(
        intent=ChatIntent.PROVIDE_CONSUMPTION,
        monthly_consumption_kwh=round(value, 4),
        confidence=1.0,
    )


def parse_system_size(text: str) -> ParsedChatMessage | None:
    """Resolve one of the three permitted sizes.

    Accepts the size itself (``6``, ``6 kWp``), the panel count (``15 panels``,
    ``fifteen panels``) and relative phrasing (``the middle option``). Anything
    outside the whitelist is refused rather than snapped to a neighbour: the
    brief allows exactly three sizes, and silently upgrading a request for
    5 kWp would be a lie about what was quoted.
    """
    normalised = _normalise(text)

    # Explicit panel counts first: "24 panels" must not be read as 24 kWp.
    panel_match = re.search(r"(?P<n>\d{1,3})\s*(?:x\s*)?panels?\b", normalised)
    if panel_match:
        count = int(panel_match.group("n"))
        if count in PANEL_COUNT_TO_SIZE:
            return ParsedChatMessage(
                intent=ChatIntent.SELECT_SYSTEM_SIZE,
                system_size_kwp=PANEL_COUNT_TO_SIZE[count],
                confidence=1.0,
            )
        return None

    if re.search(r"\bpanels?\b", normalised):
        word_count = _word_number(normalised)
        if word_count in PANEL_COUNT_TO_SIZE:
            return ParsedChatMessage(
                intent=ChatIntent.SELECT_SYSTEM_SIZE,
                system_size_kwp=PANEL_COUNT_TO_SIZE[word_count],
                confidence=1.0,
            )
        return None

    for number_match in NUMERIC.finditer(normalised):
        value = _to_float(number_match.group("value"))
        for allowed in ALLOWED_SIZES:
            if abs(value - allowed) < 1e-9:
                return ParsedChatMessage(
                    intent=ChatIntent.SELECT_SYSTEM_SIZE,
                    system_size_kwp=allowed,
                    confidence=1.0,
                )

    for pattern, size in ORDINAL_SIZE_PHRASES:
        if pattern.search(normalised):
            return ParsedChatMessage(
                intent=ChatIntent.SELECT_SYSTEM_SIZE,
                system_size_kwp=size,
                confidence=0.9,
            )

    return None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def classify_question(text: str) -> ChatIntent:
    lowered = text.lower()
    if FINANCE_QUESTION.search(lowered):
        return ChatIntent.ASK_FINANCIAL_QUESTION
    if ENERGY_QUESTION.search(lowered):
        return ChatIntent.ASK_ENERGY_QUESTION
    if ROOF_QUESTION.search(lowered):
        return ChatIntent.ASK_ROOF_QUESTION
    return ChatIntent.UNKNOWN


def parse_message(text: str, *, step: str) -> ParsedChatMessage:
    """Parse ``text`` in the context of ``step``.

    Returns ``UNKNOWN`` with zero confidence when nothing can be read safely,
    which is the caller's signal to try the language model.
    """
    from app.domain.models import ProjectStep

    if not text or not text.strip():
        return ParsedChatMessage(intent=ChatIntent.UNKNOWN, confidence=0.0)

    parsers = {
        ProjectStep.LOCATION.value: parse_location,
        ProjectStep.CONSUMPTION.value: parse_consumption,
        ProjectStep.SYSTEM_SIZE.value: parse_system_size,
    }

    parser = parsers.get(step)
    if parser is not None:
        parsed = parser(text)
        if parsed is not None:
            return parsed

    if CONFIRM_WORDS.search(text.lower()):
        return ParsedChatMessage(intent=ChatIntent.CONFIRM, confidence=0.9)

    question = classify_question(text)
    if question is not ChatIntent.UNKNOWN:
        return ParsedChatMessage(intent=question, confidence=0.7)

    return ParsedChatMessage(intent=ChatIntent.UNKNOWN, confidence=0.0)
