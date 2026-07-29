"""Reading a quantity out of English, in digits or in words.

Digits are handled first and unchanged - that path is unambiguous and already
carries the phrasings the brief demonstrates. Words are consulted only when the
digits found nothing, because a customer who writes "approximately one thousand
one hundred per month" has given a perfectly clear answer and should not be
asked to rephrase it as an integer.

The hard part is not parsing "one thousand one hundred". It is knowing when to
refuse: "a little over a thousand" contains a parseable magnitude and is still
not an answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

UNITS: dict[str, int] = {
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
}

TENS: dict[str, int] = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fourty": 40,  # common misspelling; costs nothing to accept
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}

#: `normalise` folds "twenty four" to "twentyfour", so the collapsed forms need
#: to be in the vocabulary too.
COMPOUND: dict[str, int] = {
    f"{ten}{unit}": tens_value + unit_value
    for ten, tens_value in (("twenty", 20),)
    for unit, unit_value in UNITS.items()
    if 1 <= unit_value <= 9
}

SCALES: dict[str, int] = {"hundred": 100, "thousand": 1000}

#: Words that carry a magnitude but no value of their own.
_ARTICLES = {"a", "an"}
_JOINERS = {"and"}

VALUE_WORDS: dict[str, int] = {**UNITS, **TENS, **COMPOUND}
NUMBER_WORD_TOKENS: frozenset[str] = frozenset(
    set(VALUE_WORDS) | set(SCALES) | _ARTICLES | _JOINERS
)

#: Softeners that do **not** move the value. "around 1150" is 1150.
APPROXIMATOR = re.compile(r"\b(about|around|roughly|approximately|approx|circa|ca\.|~)\b")

#: Softeners that make the value unknowable. "a little over a thousand" is not
#: 1000 - it is somewhere above it, and guessing would invent a number.
DIRECTIONAL = re.compile(
    r"\b(a (?:little|bit) (?:over|under|above|below|more|less)|just (?:over|under|above|below)|"
    r"(?:a bit|slightly) (?:more|less)|more than|less than|over|under|above|below|"
    r"upwards of|north of|give or take|somewhere (?:around|between)|between)\b"
)

#: Phrases that express a quantity without naming one at all.
VAGUE_QUANTIFIER = re.compile(
    r"\b(a lot|lots|loads|plenty|heaps|tons|a fair bit|quite a bit|quite high|quite low|"
    r"pretty high|pretty low|very high|very low|too much|too little|not much|hardly any|"
    r"average|normal|typical|the usual|same as (?:last|before|usual)|no idea|not sure|"
    r"dunno|do not know|it depends|depends)\b"
)

#: A number-word run followed by one of these is counting something other than
#: kilowatt-hours. "the one that fits fifteen panels" is not 15 kWh a month.
#: The trailing boundary matters: without it `months?` swallows the `month` in
#: "twelve hundred monthly" and a perfectly good answer is thrown away.
_COUNTS_SOMETHING_ELSE = re.compile(
    r"^\s*(?:panels?|modules?|kwp|kw|facets?|years?|months?)\b"
)

#: Below this, a value spelled out in words is far more likely to be a stray
#: word than a monthly consumption. "around one" is not 1 kWh.
MIN_PLAUSIBLE_WORD_VALUE = 10


@dataclass(frozen=True)
class WordNumber:
    """A magnitude read out of words, or a reason it could not be."""

    value: float | None
    reason: str | None = None
    span: str | None = None

    @property
    def ok(self) -> bool:
        return self.value is not None


def _accumulate(tokens: list[str]) -> int:
    """Standard English composition: 'one thousand one hundred and fifty'."""
    total = 0
    current = 0
    for token in tokens:
        if token in _JOINERS:
            continue
        if token in _ARTICLES:
            # "a thousand" means one thousand; a bare "a" contributes nothing.
            current = current or 1
            continue
        if token in VALUE_WORDS:
            current += VALUE_WORDS[token]
        elif token == "hundred":
            current = (current or 1) * 100
        elif token == "thousand":
            total += (current or 1) * 1000
            current = 0
    return total + current


def _colloquial_pair(tokens: list[str]) -> int | None:
    """'eleven fifty' meaning 1150 - a form `_accumulate` reads as 61.

    Deliberately narrow. It fires only for two value words that are each at
    least ten, with no scale word anywhere, so "twenty four" (24) and "thirty
    five" (35) are untouched: their second word is below ten.
    """
    values = [t for t in tokens if t not in _JOINERS and t not in _ARTICLES]
    if len(values) != 2:
        return None
    if any(t in SCALES for t in values):
        return None
    if not all(t in VALUE_WORDS for t in values):
        return None
    first, second = (VALUE_WORDS[t] for t in values)
    if not (10 <= first <= 99 and 10 <= second <= 99):
        return None
    return first * 100 + second


def _runs(text: str) -> list[tuple[list[str], int, int]]:
    """Maximal runs of number-ish tokens, with their character span."""
    runs: list[tuple[list[str], int, int]] = []
    current: list[str] = []
    start = end = 0
    for match in re.finditer(r"[a-z.~]+", text):
        token = match.group(0)
        if token in NUMBER_WORD_TOKENS:
            if not current:
                start = match.start()
            current.append(token)
            end = match.end()
        elif current:
            runs.append((current, start, end))
            current = []
    if current:
        runs.append((current, start, end))
    # A run of only articles or joiners carries no magnitude.
    return [r for r in runs if any(t in VALUE_WORDS or t in SCALES for t in r[0])]


def parse_word_number(text: str, *, require_plausible: bool = True) -> WordNumber:
    """Read a magnitude written in words, or explain why it is not readable.

    ``require_plausible`` applies the floor that rejects "around one"; it is
    off for contexts where a small integer is meaningful, such as a panel count.
    """
    if VAGUE_QUANTIFIER.search(text) and not _runs(text):
        return WordNumber(None, reason="vague_quantity")

    runs = _runs(text)
    if not runs:
        return WordNumber(None, reason=None)
    if len(runs) > 1:
        return WordNumber(None, reason="multiple_figures")

    tokens, start, end = runs[0]

    # "fifteen panels" is a count of panels, not a consumption figure.
    if _COUNTS_SOMETHING_ELSE.match(text[end:]):
        return WordNumber(None, reason=None)

    preceding = text[:start]
    if DIRECTIONAL.search(preceding):
        return WordNumber(None, reason="vague_quantity", span=text[start:end])

    value = _colloquial_pair(tokens)
    if value is None:
        value = _accumulate(tokens)

    if value <= 0:
        return WordNumber(None, reason="not_positive", span=text[start:end])
    if require_plausible and value < MIN_PLAUSIBLE_WORD_VALUE:
        return WordNumber(None, reason="implausible_bare_word", span=text[start:end])

    return WordNumber(float(value), span=text[start:end])


def parse_count_of(text: str, noun: str) -> int | None:
    """"fifteen panels" -> 15. A count, so the plausibility floor does not apply.

    Only a run immediately followed by the noun counts, so "the one that fits
    fifteen panels" reads 15 rather than the stray "one" - which is what the
    old dict-ordered scan returned.
    """
    pattern = re.compile(rf"^\s*{noun}\b")
    for tokens, _start, end in _runs(text):
        if pattern.match(text[end:]):
            value = _colloquial_pair(tokens)
            if value is None:
                value = _accumulate(tokens)
            return value if value > 0 else None
    return None


def has_vague_quantifier(text: str) -> bool:
    return bool(VAGUE_QUANTIFIER.search(text))


__all__ = [
    "APPROXIMATOR",
    "DIRECTIONAL",
    "MIN_PLAUSIBLE_WORD_VALUE",
    "NUMBER_WORD_TOKENS",
    "VAGUE_QUANTIFIER",
    "WordNumber",
    "has_vague_quantifier",
    "parse_count_of",
    "parse_word_number",
]
