"""What the model is told about the project.

The defect these cover: the prompt had a `Known so far:` line and nothing ever
filled it, because the router never passed one and the project state was built
*after* routing had already finished. Every live prompt said "nothing yet", and
the model was then asked to resolve "make it the bigger one" against nothing.
"""

from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.domain.models import ProjectStep
from app.services.conversation.context import (
    MAX_TURN_CHARS,
    RECENT_TURN_LIMIT,
    Turn,
    build_context,
)
from app.services.conversation.normalise import normalise
from app.services.workflow import ProjectState


def _context(**kwargs):
    state = ProjectState(current_step=kwargs.pop("step", ProjectStep.SYSTEM_SIZE), **kwargs)
    return build_context(state, settings=get_settings())


def test_an_empty_project_says_so_rather_than_inventing() -> None:
    assert "nothing yet" in _context(step=ProjectStep.LOCATION).as_prompt_block()


def test_confirmed_values_reach_the_prompt() -> None:
    block = _context(monthly_consumption_kwh=1150.0, selected_system_size_kwp=6.0).as_prompt_block()

    assert "1,150" in block
    assert "13,800" in block, "the annual figure is derived so the model never has to"
    assert "6" in block


def test_the_pending_question_and_the_choices_reach_the_prompt() -> None:
    """Without these, "the bigger one" has no antecedent."""
    block = _context(step=ProjectStep.SYSTEM_SIZE).as_prompt_block()

    assert "3.6 kWp" in block and "6 kWp" in block and "9.6 kWp" in block
    assert "Pending question" in block


def test_calculated_results_are_labelled_as_the_backend_s() -> None:
    """The model may quote them. It may never produce or adjust one."""
    state = ProjectState(
        current_step=ProjectStep.PROPOSAL,
        analysis={
            "energy": {"totalAnnualProductionKwh": 9502.2},
            "layout": {"placedPanelCount": 15},
            "financial": {
                "coveragePercent": 68.86,
                "annualSavingsEur": 2375.55,
                "simplePaybackYears": 3.7,
            },
        },
    )
    block = build_context(state, settings=get_settings()).as_prompt_block()

    assert "9,502" in block and "15" in block and "3.70" in block
    assert "never invent or alter these" in block


def test_a_snapshot_figure_of_an_unexpected_type_does_not_break_the_prompt() -> None:
    """Snapshot values come from JSON; a currency amount may well be a string.

    A prompt is not worth a 500, so an unformattable figure is carried as text
    rather than raised on.
    """
    state = ProjectState(
        current_step=ProjectStep.PROPOSAL,
        analysis={"financial": {"annualSavingsEur": "2375.55", "simplePaybackYears": None}},
    )
    block = build_context(state, settings=get_settings()).as_prompt_block()
    assert "2,375.55" in block


def test_history_is_bounded() -> None:
    """An unbounded transcript crowds out the instructions and outranks the state."""
    turns = [Turn("user", f"message {i}") for i in range(30)]
    context = build_context(
        ProjectState(current_step=ProjectStep.SYSTEM_SIZE),
        settings=get_settings(),
        recent=turns,
    )

    assert len(context.recent) == RECENT_TURN_LIMIT
    assert context.recent[-1].content == "message 29", "the most recent turn must survive"


def test_a_long_turn_is_trimmed_rather_than_dropped() -> None:
    context = build_context(
        ProjectState(current_step=ProjectStep.SYSTEM_SIZE),
        settings=get_settings(),
        recent=[Turn("user", "x" * 5000)],
    )
    assert len(context.recent[0].content) <= MAX_TURN_CHARS


def test_the_prompt_carries_the_context_when_one_is_supplied() -> None:
    from app.services.conversation.llm import build_prompt

    context = _context(monthly_consumption_kwh=1150.0)
    prompt = build_prompt(
        normalise("make it the bigger one"),
        step=ProjectStep.SYSTEM_SIZE,
        known={},
        context=context,
    )

    assert "1,150" in prompt
    assert "9.6 kWp" in prompt


def test_the_prompt_still_builds_without_a_context() -> None:
    """The unit tests call the router without a database; that must keep working."""
    from app.services.conversation.llm import build_prompt

    prompt = build_prompt(normalise("hello"), step=ProjectStep.CONSUMPTION, known={})
    assert "Current workflow step" in prompt


@pytest.mark.parametrize("figure", [0.0, 1150.0, 99999.0])
def test_no_figure_is_ever_asked_of_the_model(figure: float) -> None:
    """The context supplies numbers so the model never has to produce one."""
    block = _context(monthly_consumption_kwh=figure).as_prompt_block()
    assert "Confirmed so far" in block
