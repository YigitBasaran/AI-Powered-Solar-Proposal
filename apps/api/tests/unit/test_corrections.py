"""Regression tests for the six corrections review made to the conversation plan.

Each of these was a specific way the redesign could have shipped something
plausible and wrong. They are written before the implementation and kept
afterwards, because every one of them describes a mistake that is easy to make
again:

1. accepting a neighbour's roof as "the calibrated property",
2. claiming a value was preserved while recomputing it,
3. answering from an analysis that no longer describes the project,
4. letting a finalised project's values drift (see the API-level file),
5. telling a customer a fallback happened when nothing failed,
6. reading "what's the payback?" as anything other than a question.
"""

from __future__ import annotations

import pytest

from app.core.config import get_settings

CASE_LAT = -34.04658242871865
CASE_LON = 18.46491476666948

# One degree of latitude is ~111.32 km everywhere; longitude shrinks by cos(lat).
METRES_PER_DEGREE_LAT = 111_320.0


# ---------------------------------------------------------------------------
# Correction 1 - the fixed-location tolerance is metres, not hundreds of metres
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lat", "lon", "accepted", "why"),
    [
        (CASE_LAT, CASE_LON, True, "the exact case coordinate"),
        (34.04658242871865, CASE_LON, True, "the brief's documented missing minus sign"),
        (-34.04658, 18.46491, True, "the README/verify-script truncation, ~0.5 m away"),
        (-34.0466, 18.4649, True, "the parser-test truncation, ~2.4 m away"),
        (-34.04, 18.46, False, "~760 m away - a different street"),
        (41.0082, 28.9784, False, "Istanbul"),
        (51.5074, -0.1278, False, "London"),
        (40.7128, -74.0060, False, "New York"),
    ],
)
def test_only_the_calibrated_property_is_accepted(lat, lon, accepted, why) -> None:
    from app.services.conversation.extractors import matches_case_location

    assert matches_case_location(lat, lon) is accepted, why


def test_a_neighbouring_plot_is_not_the_calibrated_property() -> None:
    """150 m spans several plots here; it must not pass as the case roof."""
    from app.services.conversation.extractors import (
        case_location_distance_m,
        matches_case_location,
    )

    lat = CASE_LAT + 150.0 / METRES_PER_DEGREE_LAT
    assert 140.0 < case_location_distance_m(lat, CASE_LON) < 160.0
    assert matches_case_location(lat, CASE_LON) is False


def test_gps_scale_rounding_still_matches() -> None:
    """5 m is consumer-GPS noise, not a different building."""
    from app.services.conversation.extractors import (
        case_location_distance_m,
        matches_case_location,
    )

    lat = CASE_LAT + 5.0 / METRES_PER_DEGREE_LAT
    assert case_location_distance_m(lat, CASE_LON) < 10.0
    assert matches_case_location(lat, CASE_LON) is True


def test_the_tolerance_is_documented_and_small() -> None:
    from app.services.conversation.extractors import CASE_LOCATION_TOLERANCE_M

    assert 5.0 <= CASE_LOCATION_TOLERANCE_M <= 10.0


# ---------------------------------------------------------------------------
# Correction 2 - recalculation recomputes the dependents and nothing else
# ---------------------------------------------------------------------------


async def _snapshot(monthly: float, size: float) -> dict:
    from app.services.analysis import run_analysis, serialise_analysis

    result = await run_analysis(
        monthly_consumption_kwh=monthly,
        system_size_kwp=size,
        settings=get_settings(),
    )
    return dict(serialise_analysis(result))


# Consumption pairs chosen to straddle the savings cap. At 6 kWp the system
# produces 9,502 kWh a year, so 1,150/month (13,800/yr) is production-limited
# while 400/month (4,800/yr) is consumption-limited. Comparing only two
# production-limited values would show savings unchanged and wrongly declare
# them independent of consumption.
_CONSUMPTION_PAIRS = [(1150.0, 900.0), (1150.0, 400.0), (400.0, 200.0), (1150.0, 5000.0)]
_SYSTEM_SIZE_PAIRS = [(6.0, 9.6), (6.0, 3.6), (3.6, 9.6)]


async def test_the_declared_consumption_dependencies_are_the_real_ones(offline_env) -> None:
    """Derive the dependency set by experiment; assert the declared map matches.

    Two properties, because either alone is satisfiable by a wrong map:

    * **safety** - no field outside the map ever moves, for any pair;
    * **tightness** - every field in the map moves for some pair, so the map
      cannot be padded with things that are actually independent.

    The point is to refuse to *assume* that consumption-dependence lives only
    under a section called ``financial``. If a future field elsewhere starts
    depending on consumption, safety fails and the map has to be updated.
    """
    from app.services.conversation.invalidation import DEPENDS_ON_CONSUMPTION, differing_paths

    observed: set[str] = set()
    for before, after in _CONSUMPTION_PAIRS:
        moved = differing_paths(await _snapshot(before, 6.0), await _snapshot(after, 6.0))
        assert moved <= set(DEPENDS_ON_CONSUMPTION), (
            f"consumption {before} -> {after} moved fields outside the declared map: "
            f"{sorted(moved - set(DEPENDS_ON_CONSUMPTION))}"
        )
        observed |= moved

    assert observed == set(DEPENDS_ON_CONSUMPTION), (
        f"declared but never observed to move: {sorted(set(DEPENDS_ON_CONSUMPTION) - observed)}"
    )


async def test_the_declared_system_size_dependencies_are_the_real_ones(offline_env) -> None:
    from app.services.conversation.invalidation import DEPENDS_ON_SYSTEM_SIZE, differing_paths

    observed: set[str] = set()
    for before, after in _SYSTEM_SIZE_PAIRS:
        moved = differing_paths(await _snapshot(1150.0, before), await _snapshot(1150.0, after))
        assert moved <= set(DEPENDS_ON_SYSTEM_SIZE), (
            f"size {before} -> {after} moved fields outside the declared map: "
            f"{sorted(moved - set(DEPENDS_ON_SYSTEM_SIZE))}"
        )
        observed |= moved

    assert observed == set(DEPENDS_ON_SYSTEM_SIZE)


async def test_the_roof_and_the_rate_never_move(offline_env) -> None:
    """Two sections no project input may touch, asserted directly."""
    from app.services.conversation.invalidation import (
        DEPENDS_ON_CONSUMPTION,
        DEPENDS_ON_SYSTEM_SIZE,
    )

    everything = set(DEPENDS_ON_CONSUMPTION) | set(DEPENDS_ON_SYSTEM_SIZE)
    assert not [p for p in everything if p.startswith(("roof.", "exchangeRate."))]


async def test_recomputing_for_consumption_leaves_everything_else_byte_identical(
    offline_env,
) -> None:
    """Not "we believe these are unchanged" - they are compared."""
    from app.services.analysis import recompute_for_consumption

    original = await _snapshot(1150.0, 6.0)
    updated = recompute_for_consumption(
        snapshot=original, monthly_consumption_kwh=900.0, settings=get_settings()
    )

    for section in ("roof", "layout", "energy", "exchangeRate"):
        assert updated[section] == original[section], f"{section} was rebuilt, not preserved"

    assert updated["financial"] != original["financial"]
    assert updated["financial"]["annualConsumptionKwh"] == pytest.approx(900.0 * 12)


async def test_recomputing_for_consumption_never_refetches_the_rate(offline_env) -> None:
    """The customer keeps the rate they were quoted, not a fresher one."""
    from app.services.analysis import recompute_for_consumption

    original = await _snapshot(1150.0, 6.0)
    updated = recompute_for_consumption(
        snapshot=original, monthly_consumption_kwh=900.0, settings=get_settings()
    )

    assert updated["exchangeRate"]["rate"] == original["exchangeRate"]["rate"]
    assert updated["exchangeRate"]["rateDate"] == original["exchangeRate"]["rateDate"]
    assert updated["exchangeRate"]["retrievedAt"] == original["exchangeRate"]["retrievedAt"]
    assert (
        updated["financial"]["convertedCapex"] == original["financial"]["convertedCapex"]
    ), "CAPEX does not depend on consumption"


async def test_recomputing_for_system_size_preserves_the_roof_and_the_rate(offline_env) -> None:
    """The layout and production genuinely change; the roof and rate do not."""
    from app.services.analysis import recompute_for_system_size

    original = await _snapshot(1150.0, 6.0)
    updated = await recompute_for_system_size(
        snapshot=original,
        system_size_kwp=9.6,
        monthly_consumption_kwh=1150.0,
        settings=get_settings(),
    )

    assert updated["roof"] == original["roof"]
    assert updated["exchangeRate"] == original["exchangeRate"]
    assert updated["layout"]["requestedSystemSizeKwp"] == 9.6
    assert updated["layout"]["placedPanelCount"] == 24
    assert updated["energy"]["totalAnnualProductionKwh"] != original["energy"][
        "totalAnnualProductionKwh"
    ]


async def test_a_selective_recompute_equals_a_full_reanalysis(offline_env) -> None:
    """The shortcut is a shortcut, not a different answer.

    If these ever diverged, an edited project would quietly disagree with a
    freshly analysed one for the same inputs.
    """
    from app.services.analysis import recompute_for_system_size
    from app.services.conversation.invalidation import differing_paths

    original = await _snapshot(1150.0, 6.0)
    selective = await recompute_for_system_size(
        snapshot=original,
        system_size_kwp=9.6,
        monthly_consumption_kwh=1150.0,
        settings=get_settings(),
    )
    full = await _snapshot(1150.0, 9.6)

    assert differing_paths(selective, full) == set()


# ---------------------------------------------------------------------------
# Correction 3 (part) - the volatile ignore list may not hide a domain value
# ---------------------------------------------------------------------------


def test_volatile_paths_exclude_every_domain_and_provenance_value() -> None:
    """An ignore list is the easiest place to hide a real difference."""
    from app.services.conversation.invalidation import VOLATILE_SNAPSHOT_PATHS

    for path in VOLATILE_SNAPSHOT_PATHS:
        assert not path.startswith(("roof.", "layout.", "energy.", "financial.")), (
            f"{path} is domain output and must never be normalised away"
        )

    forbidden = {
        "exchangeRate.rate",
        "exchangeRate.rateDate",
        "exchangeRate.retrievalSource",
        "exchangeRate.dataProvider",
        "exchangeRate.sourceApi",
        "exchangeRate.isLive",
        "exchangeRate.isFixture",
        "energy.dataSource",
        "energy.radiationDatabase",
    }
    assert VOLATILE_SNAPSHOT_PATHS.isdisjoint(forbidden), "provenance is data, not metadata"


async def test_identical_inputs_differ_only_in_volatile_paths(offline_env) -> None:
    """Proves determinism, and that the ignore list is not missing anything."""
    from app.services.conversation.invalidation import VOLATILE_SNAPSHOT_PATHS, differing_paths

    a = await _snapshot(1150.0, 6.0)
    b = await _snapshot(1150.0, 6.0)

    assert differing_paths(a, b, ignore=frozenset()) <= set(VOLATILE_SNAPSHOT_PATHS)
    assert differing_paths(a, b) == set()


# ---------------------------------------------------------------------------
# Correction 3 - a stale analysis is never an answer source
# ---------------------------------------------------------------------------


def _project_state(**overrides):
    from app.domain.models import ProjectStep
    from app.services.workflow import ProjectState

    base = {
        "current_step": ProjectStep.PROPOSAL,
        "raw_location_input": "-34.04658242871865, 18.46491476666948",
        "monthly_consumption_kwh": 1150.0,
        "selected_system_size_kwp": 6.0,
        "analysis_status": "complete",
        "analysis": None,
        "has_finalised_proposal": False,
        "proposal_snapshot": None,
    }
    base.update(overrides)
    return ProjectState(**base)


async def test_a_signature_mismatch_withholds_the_affected_sections(offline_env) -> None:
    from app.services.conversation.actions import AnswerState, Topic
    from app.services.conversation.facts import build_facts

    snapshot = await _snapshot(1150.0, 6.0)
    # The project has moved on; the snapshot still describes 1150.
    project = _project_state(monthly_consumption_kwh=900.0, analysis=snapshot)

    finance = build_facts(project=project, settings=get_settings(), topic=Topic.FINANCE)
    assert finance.state is not AnswerState.ANSWERABLE_NOW
    assert "annualSavingsEur" not in finance.values


async def test_unaffected_sections_stay_answerable_during_a_change(offline_env) -> None:
    from app.services.conversation.actions import AnswerState, Topic
    from app.services.conversation.facts import build_facts

    snapshot = await _snapshot(1150.0, 6.0)
    project = _project_state(
        monthly_consumption_kwh=900.0, analysis=snapshot, analysis_status="recalculating"
    )

    roof = build_facts(project=project, settings=get_settings(), topic=Topic.ROOF)
    assert roof.state is AnswerState.ANSWERABLE_NOW, "the roof does not depend on consumption"

    finance = build_facts(project=project, settings=get_settings(), topic=Topic.FINANCE)
    assert finance.state is AnswerState.RECALCULATING


async def test_a_stale_status_does_not_answer_from_the_old_numbers(offline_env) -> None:
    from app.services.conversation.actions import AnswerState, Topic
    from app.services.conversation.facts import build_facts

    snapshot = await _snapshot(1150.0, 6.0)
    project = _project_state(
        monthly_consumption_kwh=900.0, analysis=snapshot, analysis_status="stale"
    )

    finance = build_facts(project=project, settings=get_settings(), topic=Topic.FINANCE)
    assert finance.state is AnswerState.NOT_CALCULATED_YET
    assert not finance.values


# ---------------------------------------------------------------------------
# Correction 5 - "handled with safe fallback" means something actually failed
# ---------------------------------------------------------------------------


async def test_rules_answering_cleanly_records_no_attempt(offline_env) -> None:
    """Configured=ollama plus a clear message is not a fallback event."""
    from app.core.config import LlmProvider
    from app.domain.models import ProjectStep
    from app.services.conversation.router import route_message

    settings = get_settings().model_copy(update={"llm_provider": LlmProvider.OLLAMA})
    routed = await route_message("1150 kWh", step=ProjectStep.CONSUMPTION, settings=settings)

    assert routed.interpretation.configured_provider == "ollama"
    assert routed.interpretation.attempted_provider is None, "no HTTP call was made"
    assert routed.interpretation.effective_provider == "rules"
    assert routed.interpretation.fallback_reason == "rules_sufficient"
    assert routed.interpretation.is_customer_visible_fallback is False


async def test_a_failed_model_call_is_a_customer_visible_fallback(offline_env) -> None:
    import httpx
    import respx

    from app.core.config import LlmProvider
    from app.domain.models import ProjectStep
    from app.services.conversation.router import route_message

    settings = get_settings().model_copy(
        update={"llm_provider": LlmProvider.OLLAMA, "ollama_base_url": "http://ollama.test"}
    )
    with respx.mock:
        respx.post("http://ollama.test/api/generate").mock(
            return_value=httpx.Response(200, json={"response": '{"kind": "teleport"}'})
        )
        routed = await route_message(
            "whichever one my neighbour got",
            step=ProjectStep.SYSTEM_SIZE,
            settings=settings,
        )

    assert routed.interpretation.attempted_provider == "ollama"
    assert routed.interpretation.effective_provider == "rules"
    assert routed.interpretation.fallback_reason == "schema_rejected"
    assert routed.interpretation.is_customer_visible_fallback is True


# ---------------------------------------------------------------------------
# Correction 6 - contractions are expanded for routing, raw is kept verbatim
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected_fragment"),
    [
        ("what's the payback?", "what is"),
        ("how's the roof measured?", "how is"),
        ("where's the exchange rate from?", "where is"),
        ("can't I give annual consumption?", "cannot"),
        ("doesn't that include shading?", "does not"),
        ("I don't understand kWp", "do not"),
        ("that’s right", "that is"),
    ],
)
def test_contractions_are_expanded_for_routing(raw, expected_fragment) -> None:
    from app.services.conversation.normalise import normalise

    normalised = normalise(raw)
    assert expected_fragment in normalised.text
    assert normalised.raw == raw, "the transcript must keep exactly what was typed"


@pytest.mark.parametrize(
    "raw",
    [
        "what's the payback?",
        "can't I use annual consumption",
        "doesn't this include shading",
        "how's production calculated",
    ],
)
def test_contracted_questions_route_as_questions(raw) -> None:
    from app.services.conversation.normalise import normalise
    from app.services.conversation.questions import is_question

    assert is_question(normalise(raw)) is True
