"""The curated help registry.

The load-bearing test here is the one that forbids a hardcoded number. A help
entry reading "the roof sits at 25 degrees" is correct today and silently wrong
the day `ROOF_PITCH_DEG` changes - and nothing else in the suite would notice,
because prose is not compared against anything.
"""

from __future__ import annotations

import re

import pytest

from app.core.config import get_settings
from app.services.conversation.actions import AnswerState, Topic
from app.services.conversation.knowledge import (
    BY_KEY,
    DEFAULT_FOR_TOPIC,
    ENTRIES,
    RESOLVERS,
    find_entry,
)
from app.services.conversation.normalise import normalise
from app.services.summary import unsupported_numbers

DIGIT = re.compile(r"\d")

# The registry the plan specifies. Named individually so a deletion is a test
# failure rather than a quiet reduction in what the assistant can explain.
REQUIRED_KEYS = {
    "fixed_location",
    "latitude_sign",
    "consumption_unit",
    "kw_vs_kwh",
    "kwp_definition",
    "system_options",
    "panel_power",
    "roof_pitch",
    "pixel_to_metre",
    "roof_facets",
    "roof_edges",
    "placement_method",
    "setbacks",
    "unplaced_panels",
    "pvgis",
    "specific_yield",
    "shading_limitations",
    "yield_uncertainty",
    "fx_source",
    "fx_fallback",
    "capex_conversion",
    "annual_savings",
    "payback",
    "cash_flow",
    "proposal_snapshot",
    "share_link",
    "pdf",
    "privacy",
    "scope_limitations",
    "workflow_overview",
    "change_a_value",
}


def test_the_registry_covers_every_documented_topic() -> None:
    assert set(BY_KEY) >= REQUIRED_KEYS


def test_keys_are_unique() -> None:
    assert len(BY_KEY) == len(ENTRIES)


def test_every_topic_has_a_default_entry() -> None:
    for topic in Topic:
        assert topic in DEFAULT_FOR_TOPIC, f"{topic} would answer nothing"
        assert DEFAULT_FOR_TOPIC[topic] in BY_KEY


@pytest.mark.parametrize("entry", ENTRIES, ids=lambda e: e.key)
def test_no_entry_hardcodes_a_number(entry) -> None:
    """Every figure arrives through a settings-derived placeholder."""
    assert not DIGIT.search(entry.body), (
        f"{entry.key} contains a literal number; use a {{placeholder}} so it "
        f"tracks the configuration instead of going stale"
    )


@pytest.mark.parametrize("entry", ENTRIES, ids=lambda e: e.key)
def test_every_placeholder_resolves(entry) -> None:
    for key in entry.settings_keys:
        assert key in RESOLVERS, f"{entry.key} references an unknown placeholder {key!r}"
    rendered = entry.render(get_settings())
    assert "{" not in rendered and "}" not in rendered


@pytest.mark.parametrize("entry", ENTRIES, ids=lambda e: e.key)
def test_rendered_numbers_all_come_from_settings(entry) -> None:
    """The rendered text may only contain numbers a resolver produced.

    Reuses the executive summary's number scanner, so the same definition of
    "a number in prose" guards both surfaces.
    """
    import contextlib
    from decimal import Decimal, InvalidOperation

    settings = get_settings()
    allowed: set[Decimal] = set()
    for key in entry.settings_keys:
        produced = RESOLVERS[key](settings)
        for token in unsupported_numbers(produced, set()):
            with contextlib.suppress(InvalidOperation):
                allowed.add(Decimal(token.replace(",", "")))

    offenders = unsupported_numbers(entry.render(settings), allowed)
    assert not offenders, f"{entry.key} rendered unexplained numbers {offenders}"


@pytest.mark.parametrize("entry", ENTRIES, ids=lambda e: e.key)
def test_bodies_are_substantive(entry) -> None:
    assert len(entry.body.split()) >= 25, "a one-line answer is not an explanation"


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "topic", "expected"),
    [
        ("what's the payback?", Topic.FINANCE, "payback"),
        ("how is production calculated?", Topic.YIELD, "pvgis"),
        ("where does the exchange rate come from?", Topic.FX, "fx_source"),
        ("what happens if the rate service is down?", Topic.FX, "fx_fallback"),
        ("why do you need my location?", Topic.PRIVACY, "privacy"),
        ("can I use a different address?", Topic.LOCATION, "fixed_location"),
        ("what is the difference between kW and kWh?", Topic.CONSUMPTION, "kw_vs_kwh"),
        ("what does kWp mean?", Topic.SYSTEM_SIZE, "kwp_definition"),
        ("which options do we have?", Topic.SYSTEM_SIZE, "system_options"),
        ("how steep is the roof?", Topic.ROOF, "roof_pitch"),
        ("how are the panels arranged?", Topic.LAYOUT, "placement_method"),
        ("does this include shading?", Topic.YIELD, "shading_limitations"),
        ("can I share the proposal?", Topic.PROPOSAL, "share_link"),
        ("what can you do?", Topic.GENERAL, "scope_limitations"),
    ],
)
def test_find_entry_selects_the_right_explanation(message, topic, expected) -> None:
    entry = find_entry(normalise(message).text, topic)
    assert entry is not None
    assert entry.key == expected


def test_an_unmatched_message_still_gets_the_topic_default() -> None:
    entry = find_entry("mmm", Topic.FINANCE)
    assert entry is not None and entry.key == DEFAULT_FOR_TOPIC[Topic.FINANCE]


def test_an_unmatched_message_with_no_topic_returns_nothing() -> None:
    assert find_entry("mmm") is None


def test_scope_entry_is_marked_out_of_scope() -> None:
    """It is the entry that says what the assistant will not do."""
    assert BY_KEY["scope_limitations"].state is AnswerState.OUT_OF_SCOPE


def test_the_scope_entry_names_the_exclusions() -> None:
    body = BY_KEY["scope_limitations"].body.lower()
    for excluded in ("structural", "permitting", "quotation"):
        assert excluded in body
