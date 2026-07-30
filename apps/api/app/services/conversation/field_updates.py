"""Setting a named field, from anywhere in the workflow.

The defect this replaces: updating a value you had already given depended on a
single regex that required the word after `change` to be one of `it|the|my|to`.
So `change my consumption to 10000` matched and `change consumption to 10000`
did not - and the miss was not a polite refusal, it was worse. At the
system-size step the bare number was read as a system size and refused as "not
one of the three available sizes"; at the location step the whole sentence was
read as a place name. Three steps, three different wrong answers, none of them
mentioning consumption.

Two properties matter here and neither is obvious.

**A named field plus a value is an update, even when it is phrased as a
question.** "Can you change my annual consumption to 10000?" is an instruction
with a courtesy wrapper. Routing it to the question detector - which is what
happens if you check "is this a question?" first - produces an explanation of
what consumption means and changes nothing, which is maddening. So this runs
*before* question detection, and it is deliberately narrow enough to earn that
position: it fires only when a field is named **and** a usable value is present.
"Why would you change my consumption?" names a field, carries no value, and
falls through to the question detector where it belongs.

**Units are part of the value.** `10000 kWh/year` and `10000 kWh/month` differ
by a factor of twelve, and the workflow stores months. Converting here, once,
keeps that conversion out of the state machine and out of the model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.conversation.actions import Topic
from app.services.conversation.normalise import Normalised

#: The fields a customer may set by name.
#:
#: Location is deliberately absent: this is one calibrated property, and a
#: request to move it is refused elsewhere with an explanation rather than
#: silently accepted here.
FIELD_CONSUMPTION = "monthly_consumption_kwh"
FIELD_TARIFF = "electricity_tariff_eur_per_kwh"
FIELD_SYSTEM_SIZE = "selected_system_size_kwp"

_FIELD_TOPICS = {
    FIELD_CONSUMPTION: Topic.CONSUMPTION,
    FIELD_TARIFF: Topic.FINANCE,
    FIELD_SYSTEM_SIZE: Topic.SYSTEM_SIZE,
}

#: How a customer names each field. Ordered: the first match wins, and system
#: size is tested before consumption because "system size" contains no
#: consumption word while "energy usage of my system" contains both.
_FIELD_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Bare `system` is deliberately absent. It matched the "SYSTEM:" prefix of a
    # prompt-injection attempt, which then looked like an ordinary request to
    # set the system size - a field being set by the one class of message that
    # must never set anything.
    (
        FIELD_SYSTEM_SIZE,
        re.compile(r"\b(system size|array size|kwp|installation size|system capacity)\b"),
    ),
    # Tariff before consumption, and `use` is not a consumption word.
    #
    # "Could you use 0.31 EUR/kWh instead?" names a price, but `use` was also in
    # the consumption pattern and consumption was tested first - so a tariff
    # correction silently rewrote the customer's consumption to 0.31 kWh.
    (
        FIELD_TARIFF,
        re.compile(
            # Bare `rate` is deliberately absent: this domain already has an
            # exchange rate, and "the USD to EUR rate is now 1.0" would
            # otherwise read as an instruction to set the electricity tariff.
            r"\b(tariff|price per kwh|electricity price|unit price|per kwh)\b"
            r"|eur\s*/\s*kwh"
            r"|\belectricity rate\b"
        ),
    ),
    (
        FIELD_CONSUMPTION,
        re.compile(
            r"\b(consumption|usage|demand|electricity used?|"
            r"energy used?|kwh per (?:year|month)|bill)\b"
        ),
    ),
)

#: Verbs that mean "set this to", including the polite interrogative forms.
#:
#: Deliberately excludes bare `use` and bare `make`. "I pay 0.30 per kWh and
#: **use** about 1150 kWh a month" is a customer answering the consumption
#: question and mentioning their tariff in passing - with `use` in this list it
#: was read as an instruction to set the tariff to 0.30, which is both wrong and
#: the kind of wrong nobody would notice. `use ... instead` still works, because
#: `instead` is what makes it an instruction.
_SETTING = re.compile(
    r"\b(change|set|update|switch|correct|adjust|revise)\b"
    r"|\bmake it\b"
    r"|\bactually\b"
    r"|\binstead\b"
    r"|\bis (?:actually|now)\b"
)

#: A number, with optional thousands separators.
_NUMBER = re.compile(r"[-+]?\d{1,3}(?:[, ]\d{3})+(?:\.\d+)?|[-+]?\d+(?:\.\d+)?")

#: Units that pin what a bare figure means.
_ANNUAL = re.compile(r"\b(per year|a year|/\s*year|per annum|yearly|annual(?:ly)?|pa)\b")
_MONTHLY = re.compile(r"\b(per month|a month|/\s*month|monthly|each month)\b")


@dataclass(frozen=True)
class FieldUpdate:
    """A named field, a value, and the unit that value was given in."""

    field: str
    value: float
    unit: str
    topic: Topic


def _named_field(text: str) -> str | None:
    for field, pattern in _FIELD_PATTERNS:
        if pattern.search(text):
            return field
    return None


def _first_number(text: str) -> float | None:
    match = _NUMBER.search(text)
    if match is None:
        return None
    try:
        return float(match.group(0).replace(",", "").replace(" ", ""))
    except ValueError:  # pragma: no cover - the pattern guarantees a number
        return None


def detect(message: Normalised) -> FieldUpdate | None:
    """A field update, or `None` if this message is not one.

    Returns `None` freely. Everything downstream of this is a *guess* about what
    the customer meant, and a wrong guess here silently rewrites a value they
    are relying on - so the bar is a named field, a setting verb, and a number.
    """
    text = message.text

    field = _named_field(text)
    if field is None:
        return None
    if not _SETTING.search(text):
        return None

    value = _first_number(text)
    if value is None:
        # A field named with no figure is a question about it, not an update.
        # "Can you change my consumption?" is asking how, not telling us what.
        return None

    unit = _unit_for(field, text)
    return FieldUpdate(field=field, value=value, unit=unit, topic=_FIELD_TOPICS[field])


def _unit_for(field: str, text: str) -> str:
    if field == FIELD_CONSUMPTION:
        if _ANNUAL.search(text):
            return "kWh/year"
        if _MONTHLY.search(text):
            return "kWh/month"
        # Unqualified. The workflow asks for a monthly figure, so that is the
        # reading that matches what the customer was asked - but it is recorded
        # as assumed, and the reply says which reading was taken so a customer
        # who meant the other one can see it immediately.
        return "kWh/month (assumed)"
    if field == FIELD_SYSTEM_SIZE:
        return "kWp"
    return "EUR/kWh"


def to_monthly_kwh(update: FieldUpdate) -> float:
    """Consumption in the unit the workflow stores.

    An annual figure divided by twelve, which is the same rule the intake
    extractor applies - stated once here so a value entering through the update
    path and a value entering through the answer path cannot disagree.
    """
    if update.unit == "kWh/year":
        return update.value / 12.0
    return update.value
