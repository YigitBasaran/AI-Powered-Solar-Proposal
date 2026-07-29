"""Reading a value out of a message that is trying to supply one.

Every extractor is **tri-state plus one**. The distinction that matters is
between "nothing here looks like an answer" and "an answer is here and it is
not usable", because those deserve different replies and because collapsing
them is how "-500 kWh" came to be classified as a question about energy:

    A message from which the extractor read a quantity - valid or not - is an
    answer to the question that was asked, not a question about it.

Extraction is deliberately free of policy. `extract_location` reports that a
coordinate was read; whether that coordinate is the *calibrated* property is a
workflow decision, and `matches_case_location` is the helper it uses.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from app.core.config import EARTH_RADIUS_M, Settings, get_settings
from app.domain.models import ExtractedValues
from app.services.conversation.actions import ExtractionStatus
from app.services.conversation.normalise import Normalised
from app.services.conversation.numbers import DIRECTIONAL, parse_count_of, parse_word_number

# ---------------------------------------------------------------------------
# Patterns (carried over verbatim - they are load-bearing and well covered)
# ---------------------------------------------------------------------------

#: A decimal pair, optionally parenthesised or labelled. The separator may
#: carry a label ("lat -34.0466 lon 18.4649"), so an optional longitude word is
#: allowed between the two numbers.
COORD_PAIR = re.compile(
    r"(?P<lat>[-+]?\d{1,3}(?:\.\d+)?)"
    r"\s*(?:[,;/]\s*|\s+)"
    r"(?:lon(?:g(?:itude)?)?\.?\s*[:=]?\s*)?"
    r"(?P<lon>[-+]?\d{1,3}(?:\.\d+)?)"
)

#: Numbers that may carry thousands separators: 1150, 1,150, 1 150. The sign is
#: captured deliberately: dropping it would turn "-500" into a valid 500 kWh.
NUMERIC = re.compile(r"(?P<value>[-+]?\d{1,3}(?:[, ]\d{3})+(?:\.\d+)?|[-+]?\d+(?:\.\d+)?)")

#: An energy unit directly after a number. A figure carrying one is answering
#: the question that was asked; a bare number elsewhere in the sentence is not.
ENERGY_UNIT_AFTER_NUMBER = re.compile(r"^\s*(?:kwh|kw h|kw-h|kilowatt[\s-]?hours?|units?)\b")

ANNUAL_TAIL = re.compile(r"\b(per year|a year|annual|annually|yearly|/year|p\.?a\.?)\b")

#: Any letter, used only to tell a written place name from punctuation or emoji.
PLACE_NAME_LETTERS = re.compile(r"[^\W\d_]")

#: Relative phrasing for the three sizes.
#:
#: Bare `large` and `full` are deliberately absent. They are adjectives, not
#: selections, and having them here is why "how large is the roof?" selected a
#: 9.6 kWp system. `largest`, `biggest` and `maximum` carry the intent.
ORDINAL_SIZE_PHRASES: tuple[tuple[re.Pattern[str], float], ...] = (
    (re.compile(r"\b(smallest|lowest|cheapest|minimum|min|first|starter|small)\b"), 3.6),
    (re.compile(r"\b(middle|medium|mid|second|average|moderate)\b"), 6.0),
    (re.compile(r"\b(largest|biggest|maximum|max|last|third|highest)\b"), 9.6),
)

PANEL_COUNT_TO_SIZE: dict[int, float] = {9: 3.6, 15: 6.0, 24: 9.6}

MAX_MONTHLY_CONSUMPTION_KWH = 1_000_000

#: How close a coordinate has to be to count as *the* calibrated property.
#:
#: Ten metres, not two hundred. The roof here is one plot among many and a
#: 200 m radius takes in a dozen other buildings - accepting one of those as
#: "the case property" would attach a stranger's address to this roof's
#: geometry. Ten metres is consumer-GPS noise and still covers every truncated
#: form of the coordinate in the repository (the README's `-34.04658, 18.46491`
#: is 0.5 m away; the parser tests' `-34.0466, 18.4649` is 2.4 m).
CASE_LOCATION_TOLERANCE_M = 10.0


@dataclass(frozen=True)
class Extraction:
    """What the extractor read, and how it went."""

    status: ExtractionStatus
    values: ExtractedValues = field(default_factory=ExtractedValues)
    reason: str | None = None
    span: str | None = None

    @property
    def read_a_quantity(self) -> bool:
        """True unless the message contained nothing resembling an answer."""
        return self.status is not ExtractionStatus.ABSENT


ABSENT = Extraction(ExtractionStatus.ABSENT)


def _to_float(raw: str) -> float:
    return float(raw.replace(",", "").replace(" ", ""))


# ---------------------------------------------------------------------------
# Location
# ---------------------------------------------------------------------------


def case_location_distance_m(lat: float, lon: float, settings: Settings | None = None) -> float:
    """Ground distance to the calibrated property, in metres.

    Equirectangular rather than haversine: over a few hundred metres the two
    agree to well under a millimetre, and this is only ever asked about points
    that are either the same plot or obviously elsewhere.
    """
    settings = settings or get_settings()
    case_lat = settings.case_resolved_latitude
    case_lon = settings.case_resolved_longitude

    mean_lat = math.radians((lat + case_lat) / 2.0)
    dx = math.radians(lon - case_lon) * math.cos(mean_lat)
    dy = math.radians(lat - case_lat)
    return float(EARTH_RADIUS_M * math.hypot(dx, dy))


def matches_case_location(lat: float, lon: float, settings: Settings | None = None) -> bool:
    """Is this coordinate the calibrated property?

    Accepts the brief's printed coordinate as well as the resolved one. The
    brief omits the minus sign on the latitude, putting the point in open sea;
    that error is documented, so someone pasting it verbatim is identifying the
    case property, not a different place.
    """
    settings = settings or get_settings()
    if case_location_distance_m(lat, lon, settings) <= CASE_LOCATION_TOLERANCE_M:
        return True
    # The brief omits the minus sign; someone pasting it verbatim means this place.
    return bool(
        lat > 0 and case_location_distance_m(-lat, lon, settings) <= CASE_LOCATION_TOLERANCE_M
    )


def extract_location(message: Normalised) -> Extraction:
    """Read a coordinate pair, or recognise a written place.

    Reports only what was *read*. Whether it is the calibrated property, and
    what to do about it, belongs to the state machine.
    """
    for match in COORD_PAIR.finditer(message.text):
        lat = float(match.group("lat"))
        lon = float(match.group("lon"))
        if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
            return Extraction(
                ExtractionStatus.VALID,
                ExtractedValues(latitude=lat, longitude=lon),
                span=match.group(0),
            )

    if len(PLACE_NAME_LETTERS.findall(message.text)) >= 3:
        # A place name with no coordinates. Geocoding is out of scope, so
        # nothing here is verified - it is a location the customer named.
        return Extraction(ExtractionStatus.VALID, span=message.raw.strip())

    return ABSENT


# ---------------------------------------------------------------------------
# Consumption
# ---------------------------------------------------------------------------


def _consumption_from_digits(text: str) -> Extraction | None:
    candidates = list(NUMERIC.finditer(text))
    if not candidates:
        return None

    unit_qualified = [
        m for m in candidates if ENERGY_UNIT_AFTER_NUMBER.match(text[m.end() : m.end() + 20])
    ]
    # A unit-qualified figure that turns out to be invalid (say "-500 kWh") is
    # still the customer's answer, so it is refused rather than skipped in
    # favour of some other number in the sentence.
    match = unit_qualified[0] if unit_qualified else candidates[0]
    span = match.group(0)
    value = _to_float(match.group("value"))

    if DIRECTIONAL.search(text[: match.start()]):
        # "just under 1200" names a bound, not a figure.
        return Extraction(ExtractionStatus.AMBIGUOUS, reason="vague_quantity", span=span)
    if value <= 0:
        return Extraction(ExtractionStatus.INVALID, reason="not_positive", span=span)
    if value > MAX_MONTHLY_CONSUMPTION_KWH:
        return Extraction(ExtractionStatus.INVALID, reason="too_large", span=span)

    if ANNUAL_TAIL.search(text[match.end() : match.end() + 24]):
        value /= 12.0

    return Extraction(
        ExtractionStatus.VALID,
        ExtractedValues(monthly_consumption_kwh=round(value, 4)),
        span=span,
    )


_AMBIGUOUS_WORD_REASONS = {"vague_quantity", "implausible_bare_word", "multiple_figures"}


def extract_consumption(message: Normalised) -> Extraction:
    """A monthly consumption figure, in digits or in words.

    Digits first: that path is unambiguous and carries every phrasing the brief
    demonstrates. Words are consulted only when the digits found nothing, so
    "1150" can never be re-read as something else.
    """
    from_digits = _consumption_from_digits(message.text)
    if from_digits is not None:
        return from_digits

    word = parse_word_number(message.text)
    if word.ok:
        value = word.value or 0.0
        if ANNUAL_TAIL.search(message.text):
            value /= 12.0
        return Extraction(
            ExtractionStatus.VALID,
            ExtractedValues(monthly_consumption_kwh=round(value, 4)),
            span=word.span,
        )
    if word.reason in _AMBIGUOUS_WORD_REASONS:
        return Extraction(ExtractionStatus.AMBIGUOUS, reason=word.reason, span=word.span)
    if word.reason == "not_positive":
        return Extraction(ExtractionStatus.INVALID, reason=word.reason, span=word.span)

    return ABSENT


# ---------------------------------------------------------------------------
# System size
# ---------------------------------------------------------------------------


def extract_system_size(message: Normalised, settings: Settings | None = None) -> Extraction:
    """One of the three permitted sizes, or an honest refusal.

    A size outside the whitelist is refused rather than snapped to a
    neighbour - quoting a system the customer did not ask for would be a lie
    about what was priced.
    """
    settings = settings or get_settings()
    allowed = tuple(settings.allowed_system_sizes_kwp)
    text = message.text

    # Explicit panel counts first: "24 panels" must not be read as 24 kWp.
    digits = re.search(r"(?P<n>\d{1,3})\s*(?:x\s*)?panels?\b", text)
    if digits:
        count = int(digits.group("n"))
        if count in PANEL_COUNT_TO_SIZE:
            return Extraction(
                ExtractionStatus.VALID,
                ExtractedValues(system_size_kwp=PANEL_COUNT_TO_SIZE[count]),
                span=digits.group(0),
            )
        return Extraction(
            ExtractionStatus.INVALID, reason="not_an_allowed_size", span=digits.group(0)
        )

    if re.search(r"\bpanels?\b", text):
        count = parse_count_of(text, "panels?") or 0
        if count in PANEL_COUNT_TO_SIZE:
            return Extraction(
                ExtractionStatus.VALID,
                ExtractedValues(system_size_kwp=PANEL_COUNT_TO_SIZE[count]),
            )
        if count:
            return Extraction(ExtractionStatus.INVALID, reason="not_an_allowed_size")
        return ABSENT

    numbers = list(NUMERIC.finditer(text))
    for match in numbers:
        value = _to_float(match.group("value"))
        for size in allowed:
            if abs(value - size) < 1e-9:
                return Extraction(
                    ExtractionStatus.VALID,
                    ExtractedValues(system_size_kwp=size),
                    span=match.group(0),
                )

    for pattern, size in ORDINAL_SIZE_PHRASES:
        if pattern.search(text):
            return Extraction(
                ExtractionStatus.VALID,
                ExtractedValues(system_size_kwp=size),
            )

    if numbers:
        # A number was offered and it is not one of the three.
        return Extraction(
            ExtractionStatus.INVALID, reason="not_an_allowed_size", span=numbers[0].group(0)
        )

    return ABSENT


__all__ = [
    "CASE_LOCATION_TOLERANCE_M",
    "Extraction",
    "case_location_distance_m",
    "extract_consumption",
    "extract_location",
    "extract_system_size",
    "matches_case_location",
]
