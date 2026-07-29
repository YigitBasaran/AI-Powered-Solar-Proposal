"""The answer service: the source hierarchy, the answer states, the LLM gate.

Every test here is about *where a sentence's numbers came from*. The chat can
say something plausible from four different places, and the difference between
them is the difference between a proposal that reproduces and one that does not.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.core.config import LlmProvider, get_settings
from app.domain.models import ProjectStep
from app.services.analysis import run_analysis, serialise_analysis
from app.services.conversation.actions import (
    ActionKind,
    AnswerSource,
    AnswerState,
    ConversationAction,
    Topic,
)
from app.services.conversation.answers import answer_question, compose, paraphrase
from app.services.conversation.facts import build_facts
from app.services.conversation.knowledge import find_entry
from app.services.conversation.normalise import normalise
from app.services.workflow import ProjectState


async def _snapshot(monthly: float = 1150.0, size: float = 6.0) -> dict:
    result = await run_analysis(
        monthly_consumption_kwh=monthly, system_size_kwp=size, settings=get_settings()
    )
    return dict(serialise_analysis(result))


def _state(**overrides) -> ProjectState:
    base = {
        "current_step": ProjectStep.PROPOSAL,
        "raw_location_input": "-34.04658242871865, 18.46491476666948",
        "monthly_consumption_kwh": 1150.0,
        "selected_system_size_kwp": 6.0,
        "analysis_status": "complete",
    }
    base.update(overrides)
    return ProjectState(**base)


def _ask(question: str, topic: Topic) -> ConversationAction:
    return ConversationAction(kind=ActionKind.ASK_QUESTION, topic=topic, question=question)


# ---------------------------------------------------------------------------
# The hierarchy
# ---------------------------------------------------------------------------


async def test_a_finalised_proposal_outranks_the_live_analysis(offline_env) -> None:
    """Two sources disagree; the issued document wins.

    This is the whole reason the hierarchy is ordered rather than "whatever is
    available": a customer holding a proposal must be told what the proposal
    says, not what the workspace has drifted to since.
    """
    issued = await _snapshot(1150.0, 6.0)
    later = await _snapshot(1150.0, 9.6)
    assert issued["layout"]["placedPanelCount"] != later["layout"]["placedPanelCount"]

    project = _state(
        analysis=later,
        proposal_snapshot=issued,
        has_finalised_proposal=True,
        selected_system_size_kwp=9.6,
    )
    answer = answer_question(action=_ask("how many panels?", Topic.LAYOUT), project=project)

    assert answer.source is AnswerSource.PROPOSAL_SNAPSHOT
    assert str(issued["layout"]["placedPanelCount"]) in answer.text
    assert str(later["layout"]["placedPanelCount"]) not in answer.text.split(".")[0]


async def test_the_roof_answers_before_anything_is_calculated(offline_env) -> None:
    """The roof is fixed, so it has a real answer at the very first step.

    Nothing about this needs an analysis, a consumption figure or a system
    size - which is the clearest demonstration that this is a conversation and
    not a wizard with a chat skin.
    """
    project = ProjectState(current_step=ProjectStep.LOCATION)
    answer = answer_question(action=_ask("how big is the roof?", Topic.ROOF), project=project)

    assert answer.state is AnswerState.ANSWERABLE_NOW
    assert answer.source is AnswerSource.CASE_CONFIG
    assert "m2" in answer.text


async def test_a_computed_figure_is_answered_from_the_analysis(offline_env) -> None:
    project = _state(analysis=await _snapshot())
    answer = answer_question(action=_ask("what is my payback?", Topic.FINANCE), project=project)

    assert answer.state is AnswerState.ANSWERABLE_NOW
    assert answer.source is AnswerSource.ANALYSIS
    assert "payback" in answer.text.lower()


# ---------------------------------------------------------------------------
# The states
# ---------------------------------------------------------------------------


def test_before_the_analysis_the_answer_is_the_methodology_plus_what_is_missing() -> None:
    """The disjointness rule, asserted.

    NOT_CALCULATED_YET is not a shrug: it is the methodology text with the
    missing inputs named.
    """
    project = ProjectState(current_step=ProjectStep.CONSUMPTION)
    answer = answer_question(action=_ask("what is my payback?", Topic.FINANCE), project=project)

    assert answer.state is AnswerState.NOT_CALCULATED_YET
    assert "divided by the annual saving" in answer.text, "the method is still explained"
    assert "monthly consumption" in answer.text and "system size" in answer.text


def test_a_methodology_question_is_answerable_at_any_step() -> None:
    project = ProjectState(current_step=ProjectStep.LOCATION)
    answer = answer_question(
        action=_ask("how is payback calculated?", Topic.FINANCE), project=project
    )
    assert answer.state in {
        AnswerState.ANSWERABLE_AS_METHODOLOGY,
        AnswerState.NOT_CALCULATED_YET,
    }
    assert "discount rate" in answer.text


async def test_a_recalculating_topic_says_so_and_names_the_change(offline_env) -> None:
    project = _state(
        analysis=await _snapshot(),
        monthly_consumption_kwh=900.0,
        analysis_status="recalculating",
        recalculating_inputs=frozenset({"monthly_consumption_kwh"}),
    )
    answer = answer_question(action=_ask("what is my payback?", Topic.FINANCE), project=project)

    assert answer.state is AnswerState.RECALCULATING
    assert "monthly consumption" in answer.text
    assert "recalculating" in answer.text.lower()


async def test_an_unaffected_topic_still_answers_during_a_recalculation(offline_env) -> None:
    project = _state(
        analysis=await _snapshot(),
        monthly_consumption_kwh=900.0,
        analysis_status="recalculating",
        recalculating_inputs=frozenset({"monthly_consumption_kwh"}),
    )
    answer = answer_question(action=_ask("how big is the roof?", Topic.ROOF), project=project)
    assert answer.state is AnswerState.ANSWERABLE_NOW


async def test_no_answer_ever_quotes_a_stale_figure(offline_env) -> None:
    """The specific number that must not appear.

    The snapshot was computed for 1,150 kWh a month; the project now says 900.
    The old annual saving is a real, correct-looking number, which is exactly
    what makes quoting it dangerous.
    """
    snapshot = await _snapshot(1150.0, 6.0)
    stale_saving = str(snapshot["financial"]["annualSavingsEur"]).split(".")[0]
    project = _state(analysis=snapshot, monthly_consumption_kwh=900.0)

    answer = answer_question(action=_ask("what will I save?", Topic.FINANCE), project=project)
    assert stale_saving not in answer.text.replace(",", "")


def test_asking_for_the_options_is_answered_in_full_not_deferred() -> None:
    """Found by probing: "which options do we have?" said "I don't have that yet".

    The options are fixed configuration and are listed right there in the same
    reply. Appending a missing-inputs note to a question that has just been
    answered reads as a refusal. The rule: only ASK_QUESTION - a question that
    wanted the *figure* - gets the note.
    """
    project = ProjectState(current_step=ProjectStep.SYSTEM_SIZE)
    action = ConversationAction(
        kind=ActionKind.REQUEST_OPTIONS,
        topic=Topic.SYSTEM_SIZE,
        question="which options do we have?",
    )
    answer = answer_question(action=action, project=project)

    assert "9 panels" in answer.text and "24 panels" in answer.text
    assert "I don't have that yet" not in answer.text
    assert answer.state is not AnswerState.NOT_CALCULATED_YET


def test_an_explanation_request_is_never_deferred_either() -> None:
    project = ProjectState(current_step=ProjectStep.LOCATION)
    action = ConversationAction(
        kind=ActionKind.REQUEST_EXPLANATION,
        topic=Topic.FINANCE,
        question="how is payback calculated?",
    )
    answer = answer_question(action=action, project=project)

    assert answer.state is AnswerState.ANSWERABLE_AS_METHODOLOGY
    assert "I don't have that yet" not in answer.text


def test_asking_what_you_will_save_is_a_finance_question() -> None:
    """Found by probing: the topic classifier had the noun but not the verb.

    "What will I save?" fell through to the step default, so at the system-size
    step it was answered as a question about system sizes.
    """
    from app.services.conversation.questions import classify_topic

    assert classify_topic(normalise("what will I save?")) is Topic.FINANCE
    assert classify_topic(normalise("how much do I save a year?")) is Topic.FINANCE
    assert classify_topic(normalise("do you store my data?")) is Topic.PRIVACY


def test_an_unrecognised_question_says_so_rather_than_guessing() -> None:
    project = ProjectState(current_step=ProjectStep.LOCATION)
    facts = build_facts(project=project, settings=get_settings(), topic=Topic.GENERAL)
    answer = compose(
        action=_ask("what is the airspeed of an unladen swallow?", Topic.GENERAL),
        facts=facts,
        entry=None,
        settings=get_settings(),
    )
    assert answer.state is AnswerState.UNSUPPORTED
    assert answer.source is AnswerSource.NONE


def test_the_scope_answer_refuses_the_things_this_is_not() -> None:
    project = ProjectState(current_step=ProjectStep.LOCATION)
    answer = answer_question(action=_ask("what can you do?", Topic.GENERAL), project=project)
    assert "permitting" in answer.text
    assert "binding quotation" in answer.text


# ---------------------------------------------------------------------------
# The paraphrase gate - five gates, same shape as the executive summary
# ---------------------------------------------------------------------------


def _finance_answer(snapshot: dict):
    project = _state(analysis=snapshot)
    settings = get_settings()
    action = _ask("what is my payback?", Topic.FINANCE)
    facts = build_facts(project=project, settings=settings, topic=Topic.FINANCE)
    entry = find_entry(normalise(action.question or "").text, Topic.FINANCE)
    return facts, compose(action=action, facts=facts, entry=entry, settings=settings)


async def test_paraphrase_is_skipped_when_no_model_is_configured(offline_env) -> None:
    facts, answer = _finance_answer(await _snapshot())
    result, reason = await paraphrase(
        question="q", answer=answer, facts=facts, settings=get_settings()
    )
    assert reason == "not_configured"
    assert result is answer


async def test_a_paraphrase_that_invents_a_number_is_discarded(offline_env) -> None:
    """The gate that matters. A wrong payback in prose reads exactly like a right one."""
    facts, answer = _finance_answer(await _snapshot())
    settings = get_settings().model_copy(
        update={"llm_provider": LlmProvider.OLLAMA, "ollama_base_url": "http://ollama.test"}
    )
    with respx.mock:
        respx.post("http://ollama.test/api/generate").mock(
            return_value=httpx.Response(
                200, json={"response": "You will save EUR 99,999 and pay back in 1.2 years."}
            )
        )
        result, reason = await paraphrase(
            question="what is my payback?", answer=answer, facts=facts, settings=settings
        )

    assert reason == "unsupported_numbers"
    assert result.text == answer.text
    assert result.source is not AnswerSource.LLM_PARAPHRASE


async def test_a_faithful_paraphrase_is_used_and_labelled(offline_env) -> None:
    facts, answer = _finance_answer(await _snapshot())
    settings = get_settings().model_copy(
        update={"llm_provider": LlmProvider.OLLAMA, "ollama_base_url": "http://ollama.test"}
    )
    faithful = "Payback is the converted capital cost divided by what you save each year."
    with respx.mock:
        respx.post("http://ollama.test/api/generate").mock(
            return_value=httpx.Response(200, json={"response": faithful})
        )
        result, reason = await paraphrase(
            question="what is my payback?", answer=answer, facts=facts, settings=settings
        )

    assert reason is None
    assert result.text == faithful
    assert result.source is AnswerSource.LLM_PARAPHRASE


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ({"response": ""}, "empty_response"),
        ({"response": " ".join(["word"] * 400)}, "too_long"),
    ],
)
async def test_the_remaining_paraphrase_gates(offline_env, response, expected) -> None:
    facts, answer = _finance_answer(await _snapshot())
    settings = get_settings().model_copy(
        update={"llm_provider": LlmProvider.OLLAMA, "ollama_base_url": "http://ollama.test"}
    )
    with respx.mock:
        respx.post("http://ollama.test/api/generate").mock(
            return_value=httpx.Response(200, json=response)
        )
        result, reason = await paraphrase(
            question="q", answer=answer, facts=facts, settings=settings
        )
    assert reason == expected
    assert result.text == answer.text


async def test_an_unreachable_model_falls_back_silently(offline_env) -> None:
    facts, answer = _finance_answer(await _snapshot())
    settings = get_settings().model_copy(
        update={"llm_provider": LlmProvider.OLLAMA, "ollama_base_url": "http://ollama.test"}
    )
    with respx.mock:
        respx.post("http://ollama.test/api/generate").mock(
            side_effect=httpx.ConnectError("refused")
        )
        result, reason = await paraphrase(
            question="q", answer=answer, facts=facts, settings=settings
        )
    assert reason == "unreachable"
    assert result.text == answer.text


async def test_the_model_never_receives_the_snapshot(offline_env) -> None:
    """Enforced by construction, not by prompt.

    If the snapshot were in the prompt, "use only the supplied facts" would be
    the only thing standing between a model and a recalculated payback.
    """
    facts, answer = _finance_answer(await _snapshot())
    settings = get_settings().model_copy(
        update={"llm_provider": LlmProvider.OLLAMA, "ollama_base_url": "http://ollama.test"}
    )
    captured: dict = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured.update(__import__("json").loads(request.content))
        return httpx.Response(200, json={"response": "Fine."})

    with respx.mock:
        respx.post("http://ollama.test/api/generate").mock(side_effect=_capture)
        await paraphrase(question="q", answer=answer, facts=facts, settings=settings)

    body = captured["prompt"] + captured["system"]
    for forbidden in ("cashFlow", "sourcePixelPolygon", "retrievedAt", "radiationDatabase"):
        assert forbidden not in body


def test_a_capitalised_question_still_reaches_the_right_help_entry() -> None:
    """Found by live probing: the registry was searched with the raw message.

    `action.question` is the text exactly as typed, because the transcript
    needs it that way. The triggers are lowercase, so every capitalised
    question matched nothing and fell through to the topic default - and
    "Why does a 6 kWp system have 15 panels?" was answered with the list of
    sizes rather than with how the panel count is derived.
    """
    project = ProjectState(current_step=ProjectStep.SYSTEM_SIZE)
    answer = answer_question(
        action=_ask("Why does a 6 kWp system have 15 panels?", Topic.SYSTEM_SIZE),
        project=project,
    )
    assert answer.help_topic == "panel_power"
    assert "400 Wp" in answer.text


def test_the_lowercase_form_of_the_same_question_is_unchanged() -> None:
    project = ProjectState(current_step=ProjectStep.SYSTEM_SIZE)
    answer = answer_question(
        action=_ask("why does a 6 kwp system have 15 panels?", Topic.SYSTEM_SIZE),
        project=project,
    )
    assert answer.help_topic == "panel_power"


@pytest.mark.parametrize(
    "kind",
    [ActionKind.OFF_TOPIC, ActionKind.ASK_QUESTION],
)
def test_a_reply_with_nothing_to_say_still_says_what_went_wrong(kind) -> None:
    """Found against a live model: "banana" came back as a bare prompt.

    Off-topic reaches the state machine only from the model, and the route
    builds no answer for it — so sharing the question branch produced an empty
    reply followed by the restated prompt, and nothing else. From the
    customer's side, off-topic and unreadable are the same event: what they
    typed could not be used, and the reply has to say so.
    """
    from app.services.workflow import handle_message

    outcome = handle_message(
        project=ProjectState(current_step=ProjectStep.CONSUMPTION),
        action=ConversationAction(kind=kind, topic=Topic.GENERAL),
        raw_text="banana",
        answer=None,
    )

    assert "couldn't read a consumption figure" in outcome.assistant_message
    assert "1,150 kWh" in outcome.assistant_message, "and what a good answer looks like"
    assert outcome.updates == {}
    assert outcome.next_step is ProjectStep.CONSUMPTION
