"""The chat-driven workflow state machine.

Every transition is validated here, in deterministic code. The router decides
what a message *meant*; only this module decides whether that is allowed and
what changes as a result. A language model can influence the first and can
reach the second only as a :class:`ConversationAction` whose values are a
closed, validated model - the same object a rules-parsed message produces, and
subject to the same checks.

The dispatch is an exhaustive ``match`` ending in ``assert_never``. Adding a
thirteenth :class:`ActionKind` without handling it is a type error rather than
a 409 someone finds in production.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, assert_never

from app.core.config import Settings, get_settings
from app.core.errors import ValidationError
from app.domain.models import ExtractedValues, ProjectStep
from app.services.conversation.actions import (
    ActionKind,
    Answer,
    ConversationAction,
    ExtractionStatus,
    Topic,
)
from app.services.conversation.extractors import matches_case_location

WELCOME = (
    "Welcome to solarVis AI.\n\n"
    "I'll guide you through a complete solar feasibility assessment: roof "
    "measurements, panel placement, annual production, financial return and a "
    "shareable proposal. You can ask me a question at any point without losing "
    "your place.\n\n"
    "One thing to be straight about first: this build analyses a single "
    "verified property — Galway Road, Cape Town. Its roof is traced from "
    "satellite imagery and calibrated by hand, and nothing here fetches or "
    "calibrates a new one.\n\n"
    "To begin, confirm the case property, or enter its latitude and longitude "
    "from the brief."
)

CONSUMPTION_PROMPT = "What is your monthly electricity consumption?"


@dataclass(frozen=True)
class ProjectState:
    """A read-model of one project.

    The state machine and the answer service both need to know where a project
    stands; neither should need a database session to find out. Keeping this a
    plain frozen object also makes "a question mutated nothing" trivially true
    for everything downstream of the route.
    """

    current_step: ProjectStep
    raw_location_input: str | None = None
    monthly_consumption_kwh: float | None = None
    selected_system_size_kwp: float | None = None
    analysis_status: str = "pending"
    analysis: dict[str, Any] | None = None
    has_finalised_proposal: bool = False
    proposal_snapshot: dict[str, Any] | None = None
    proposal_share_token: str | None = None
    #: Which inputs a recomputation currently in flight is changing. Empty
    #: means "recalculating, cause unrecorded", which is treated as the widest
    #: possible blast radius rather than the narrowest.
    recalculating_inputs: frozenset[str] = frozenset()
    pending_confirmation: str | None = None
    revision_of_project_id: str | None = None

    @property
    def has_analysis(self) -> bool:
        return bool(self.analysis)


@dataclass
class StepOutcome:
    """The result of handling one user message.

    ``updates`` is the *only* channel by which anything changes. A question
    returns an empty one, which is what makes "a question never mutates state"
    mechanically checkable rather than conventional.
    """

    assistant_message: str
    next_step: ProjectStep
    updates: dict[str, object]
    accepted: bool = True
    #: True when the text came from the answer service rather than from here.
    answered: bool = False
    #: Set when the reply asks a yes/no question the next message may answer.
    pending_confirmation: str | None = None
    #: Which project inputs this message changed, for selective recomputation.
    changed_inputs: frozenset[str] = field(default_factory=frozenset)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


def system_size_prompt(settings: Settings) -> str:
    lines = ["Which system size would you like?", ""]
    for size in settings.allowed_system_sizes_kwp:
        count = settings.required_panel_count(size)
        lines.append(f"  - {size:g} kWp  ({count} panels x {settings.panel_power_wp} Wp)")
    lines.append("")
    lines.append("You can reply with a size, a panel count, or 'smallest', 'middle' or 'largest'.")
    return "\n".join(lines)


def location_prompt(settings: Settings) -> str:
    loc = settings.case_location
    return (
        f"Confirm the case property, or enter its coordinates: "
        f"{loc.resolved_latitude:.6f}, {loc.resolved_longitude:.6f}"
    )


def restate(step: ProjectStep, settings: Settings) -> str:
    """The question the workflow is currently waiting on, if any."""
    match step:
        case ProjectStep.LOCATION:
            return location_prompt(settings)
        case ProjectStep.CONSUMPTION:
            return CONSUMPTION_PROMPT
        case ProjectStep.SYSTEM_SIZE:
            return system_size_prompt(settings)
        case _:
            return ""


def _describe_location(settings: Settings, raw: str) -> str:
    loc = settings.case_location
    return (
        f'Thanks — I\'ve recorded your input as "{raw.strip()}".\n\n'
        f"This case study uses one fixed property, so the analysis runs at the "
        f"verified case coordinate {loc.resolved_latitude:.6f}, "
        f"{loc.resolved_longitude:.6f} (Cape Town, South Africa).\n\n"
        f"A note on that coordinate: the brief prints the latitude without a "
        f"minus sign, which places it in open sea. The southern-hemisphere "
        f"reading is the real property, and it is what I use. Both values are "
        f"kept on record.\n\n" + CONSUMPTION_PROMPT
    )


def location_offer(settings: Settings, raw: str) -> str:
    """The block-and-offer reply. Nothing is stored.

    The honest version of what earlier builds did silently: an arbitrary
    address was accepted, echoed back, and then quietly analysed against Cape
    Town geometry. Storing a stranger's address against this roof's
    measurements is not a cosmetic problem - every figure downstream would be
    labelled with a property it does not describe.
    """
    loc = settings.case_location
    return (
        f'I can\'t analyse "{raw.strip()}".\n\n'
        f"This build works on one verified property — Galway Road, Cape Town, at "
        f"{loc.resolved_latitude:.6f}, {loc.resolved_longitude:.6f}. Its roof is "
        f"traced from a satellite raster and calibrated by hand, and analysing "
        f"anywhere else would need a fresh image fetch and a new calibration. "
        f"Neither is automated here, so I'd rather say so than run Cape Town's "
        f"roof under someone else's address.\n\n"
        f"Shall I go ahead with the case property? Reply \"yes\", or enter its "
        f"latitude and longitude."
    )


def _describe_consumption(settings: Settings, monthly: float) -> str:
    annual = monthly * 12.0
    return (
        f"Monthly consumption: {monthly:,.0f} kWh\n"
        f"Annual consumption = {monthly:,.0f} x 12 = {annual:,.0f} kWh/year\n"
        f"Electricity unit price: EUR {settings.case_electricity_price:.2f}/kWh\n\n"
        + system_size_prompt(settings)
    )


def _describe_system_size(settings: Settings, size: float) -> str:
    count = settings.required_panel_count(size)
    return (
        f"Selected system size: {size:g} kWp\n"
        f"Required panels = {size:g} kWp x 1,000 / {settings.panel_power_wp} Wp "
        f"= {count} panels\n"
        f"Panel dimensions: {settings.panel_width_m:g} m x "
        f"{settings.panel_height_m:g} m\n\n"
        "I'll now load the satellite image, reconstruct the roof, place the "
        "panels, and calculate production and financial return."
    )


#: How an unusable figure is explained. Every branch names what was read, so
#: "-500 kWh" is refused as a negative number rather than as gibberish.
def _consumption_refusal(action: ConversationAction) -> str:
    tail = "Please give your monthly electricity use in kWh — for example: 1,150 kWh"
    match action.reason:
        case "not_positive":
            return f"A consumption figure has to be greater than zero. {tail}"
        case "too_large":
            return f"That figure is far larger than any household meter reads. {tail}"
        case "vague_quantity" | "implausible_bare_word" | "multiple_figures":
            return (
                "That could be anywhere in quite a range, and I'd rather not guess. " + tail
            )
        case _:
            return f"I couldn't read a consumption figure there. {tail}"


def _size_refusal(action: ConversationAction, settings: Settings) -> str:
    if action.reason == "not_an_allowed_size":
        return "That isn't one of the three available sizes.\n\n" + system_size_prompt(settings)
    return "Please choose one of the three available sizes.\n\n" + system_size_prompt(settings)


# ---------------------------------------------------------------------------
# The handlers
# ---------------------------------------------------------------------------


def _stay(
    project: ProjectState, message: str, *, accepted: bool = False, **kwargs: Any
) -> StepOutcome:
    return StepOutcome(
        assistant_message=message,
        next_step=project.current_step,
        updates={},
        accepted=accepted,
        **kwargs,
    )


def _answered_outcome(
    project: ProjectState, answer: Answer | None, settings: Settings
) -> StepOutcome:
    """A question. Same step, no updates, and the pending prompt restated."""
    if answer is None or not answer.text.strip():
        # A question routed here with nothing to say is a bug upstream, not a
        # reason to reply with a bare prompt. Say what happened instead.
        return _unknown(project, settings)

    text = answer.text
    prompt = restate(project.current_step, settings)
    if prompt and prompt not in text:
        text = f"{text}\n\n{prompt}"
    return _stay(project, text, accepted=True, answered=True)


def _handle_location(
    project: ProjectState, action: ConversationAction, raw: str, settings: Settings
) -> StepOutcome:
    values = action.values
    if values.latitude is not None and values.longitude is not None:
        if matches_case_location(values.latitude, values.longitude, settings):
            return _accept_location(project, raw, settings)
        return _stay(
            project, location_offer(settings, raw), pending_confirmation="use_case_location"
        )

    # A written place. Geocoding is out of scope, so there is no way to know
    # whether it is this property - and guessing "yes" is the failure this
    # branch exists to prevent.
    return _stay(
        project, location_offer(settings, raw), pending_confirmation="use_case_location"
    )


def _accept_location(project: ProjectState, raw: str, settings: Settings) -> StepOutcome:
    loc = settings.case_location
    return StepOutcome(
        assistant_message=_describe_location(settings, raw),
        next_step=ProjectStep.CONSUMPTION,
        updates={
            "raw_location_input": raw,
            "resolved_latitude": loc.resolved_latitude,
            "resolved_longitude": loc.resolved_longitude,
        },
    )


def _set_consumption(
    project: ProjectState, action: ConversationAction, settings: Settings, *, advance: bool
) -> StepOutcome:
    if action.extraction is not ExtractionStatus.VALID:
        return _stay(project, _consumption_refusal(action))
    monthly = action.values.monthly_consumption_kwh
    if monthly is None or monthly <= 0:
        raise ValidationError("Monthly consumption must be greater than zero.")
    return StepOutcome(
        assistant_message=_describe_consumption(settings, monthly),
        next_step=ProjectStep.SYSTEM_SIZE if advance else project.current_step,
        updates={"monthly_consumption_kwh": monthly},
        changed_inputs=frozenset({"monthly_consumption_kwh"}),
    )


def _set_system_size(
    project: ProjectState, action: ConversationAction, settings: Settings, *, advance: bool
) -> StepOutcome:
    if action.extraction is not ExtractionStatus.VALID or action.values.system_size_kwp is None:
        return _stay(project, _size_refusal(action, settings))
    size = float(action.values.system_size_kwp)
    # Defence in depth: the whitelist is enforced here as well as in the
    # extractor, because this is the only place a model-supplied value lands.
    if size not in settings.allowed_system_sizes_kwp:
        raise ValidationError(
            f"{size} kWp is not an available system size.",
            details={"allowed": list(settings.allowed_system_sizes_kwp)},
        )
    return StepOutcome(
        assistant_message=_describe_system_size(settings, size),
        next_step=ProjectStep.ROOF_RECONSTRUCTION if advance else project.current_step,
        updates={"selected_system_size_kwp": size},
        changed_inputs=frozenset({"selected_system_size_kwp"}),
    )


def _provide_value(
    project: ProjectState, action: ConversationAction, raw: str, settings: Settings
) -> StepOutcome:
    match project.current_step:
        case ProjectStep.LOCATION:
            return _handle_location(project, action, raw, settings)
        case ProjectStep.CONSUMPTION:
            return _set_consumption(project, action, settings, advance=True)
        case ProjectStep.SYSTEM_SIZE:
            return _set_system_size(project, action, settings, advance=True)
        case _:
            # A value arriving at a step that is not asking for one. Treated as
            # a correction rather than refused outright, because that is almost
            # always what it is.
            return _change_value(project, action, raw, settings)


CHANGEABLE: dict[Topic, str] = {
    Topic.CONSUMPTION: "monthly_consumption_kwh",
    Topic.SYSTEM_SIZE: "selected_system_size_kwp",
}


def _change_value(
    project: ProjectState, action: ConversationAction, raw: str, settings: Settings
) -> StepOutcome:
    """A correction to a value already given.

    The step does not move: rewinding would blank the progress rail for a
    change that affects one figure. What moves is the analysis, which is marked
    for recomputation of exactly the sections the change can reach.
    """
    if action.topic is Topic.LOCATION:
        return _stay(
            project, location_offer(settings, raw), pending_confirmation="use_case_location"
        )

    field_name = CHANGEABLE.get(action.topic)
    if field_name is None:
        return _stay(
            project,
            "I'm not sure which value you'd like to change. You can change your "
            "monthly consumption or the system size — tell me which and what to.",
        )

    if action.topic is Topic.CONSUMPTION:
        outcome = _set_consumption(project, action, settings, advance=False)
    else:
        outcome = _set_system_size(project, action, settings, advance=False)

    if not outcome.updates:
        return outcome

    if project.has_analysis:
        outcome.assistant_message = _recalculating_notice(action.topic, outcome.updates, settings)
    return outcome


def _recalculating_notice(topic: Topic, updates: dict[str, object], settings: Settings) -> str:
    if topic is Topic.CONSUMPTION:
        monthly = float(updates["monthly_consumption_kwh"])  # type: ignore[arg-type]
        return (
            f"Updated to {monthly:,.0f} kWh a month ({monthly * 12:,.0f} kWh a year). "
            f"Consumption drives coverage, savings and payback, so I'm recalculating "
            f"those now. The roof, the panel layout, the modelled production and the "
            f"exchange rate are unaffected and carry over unchanged."
        )
    size = float(updates["selected_system_size_kwp"])  # type: ignore[arg-type]
    count = settings.required_panel_count(size)
    return (
        f"Updated to {size:g} kWp ({count} panels). That changes the layout, the "
        f"modelled production and the financials, so I'm recalculating them now. "
        f"The roof measurements and the exchange rate you were quoted carry over "
        f"unchanged."
    )


def _navigate(project: ProjectState, action: ConversationAction, settings: Settings) -> StepOutcome:
    target = action.target_step
    if target is None:
        return _stay(
            project,
            "Which part would you like to go back to — the location, your "
            "consumption, or the system size?",
        )
    if target is ProjectStep.LOCATION:
        return _stay(
            project,
            "The location is fixed for this case study, so there is nothing to "
            "change there. " + location_prompt(settings),
        )
    return _stay(
        project,
        f"Of course. {restate(target, settings)}",
        accepted=True,
        pending_confirmation=None,
    )


def _confirm(
    project: ProjectState, action: ConversationAction, raw: str, settings: Settings
) -> StepOutcome:
    if project.pending_confirmation == "use_case_location":
        # Recorded as the case property rather than as the word "yes": the
        # customer confirmed the property, and that is what the field means.
        loc = settings.case_location
        return StepOutcome(
            assistant_message=_describe_location(settings, "the case property"),
            next_step=ProjectStep.CONSUMPTION,
            updates={
                "raw_location_input": f"Case property — Galway Road, Cape Town "
                f"({loc.resolved_latitude:.6f}, {loc.resolved_longitude:.6f})",
                "resolved_latitude": loc.resolved_latitude,
                "resolved_longitude": loc.resolved_longitude,
            },
        )
    if project.pending_confirmation == "reset":
        return StepOutcome(
            assistant_message="Starting over.\n\n" + WELCOME,
            next_step=ProjectStep.LOCATION,
            updates={
                "raw_location_input": None,
                "resolved_latitude": None,
                "resolved_longitude": None,
                "monthly_consumption_kwh": None,
                "selected_system_size_kwp": None,
                "analysis_json": None,
                "analysis_status": "pending",
            },
        )
    if project.current_step is ProjectStep.LOCATION:
        # Nothing was offered yet, but "yes" at the location step can only
        # sensibly mean the standing offer.
        return _confirm(
            project=ProjectState(
                **{**project.__dict__, "pending_confirmation": "use_case_location"}
            ),
            action=action,
            raw=raw,
            settings=settings,
        )
    prompt = restate(project.current_step, settings)
    return _stay(
        project,
        f"Right — {prompt}" if prompt else "Understood.",
        accepted=True,
    )


def _reset(project: ProjectState) -> StepOutcome:
    """Two-step, because one word wiping the whole project is a hostile default."""
    if project.pending_confirmation == "reset":
        return _confirm(project, ConversationAction(kind=ActionKind.CONFIRM), "", get_settings())
    return _stay(
        project,
        "Starting over clears your consumption, your system size and any "
        "analysis that has run — the proposal you have already finalised, if "
        "any, stays exactly as it is. Shall I go ahead?",
        accepted=True,
        pending_confirmation="reset",
    )


def handle_message(
    *,
    project: ProjectState,
    action: ConversationAction,
    raw_text: str,
    answer: Answer | None = None,
    settings: Settings | None = None,
) -> StepOutcome:
    """Apply one classified message to the workflow.

    Total over :class:`ActionKind`: every kind has a branch and the fallthrough
    is ``assert_never``, so a new kind cannot reach production unhandled.
    """
    settings = settings or get_settings()

    match action.kind:
        case (
            ActionKind.ASK_QUESTION
            | ActionKind.REQUEST_OPTIONS
            | ActionKind.REQUEST_EXPLANATION
        ):
            return _answered_outcome(project, answer, settings)

        case ActionKind.OFF_TOPIC:
            # Handled like UNKNOWN rather than shared with the question
            # branch. Off-topic reaches here only from the model, and the
            # route does not build an answer for it - so sharing the branch
            # produced an *empty* reply followed by the restated prompt.
            # "banana" at the consumption step came back as a bare "What is
            # your monthly electricity consumption?", which tells a customer
            # nothing about what went wrong. From their side, off-topic and
            # unreadable are the same event: the assistant could not use what
            # they typed.
            return _unknown(project, settings)

        case ActionKind.UNSUPPORTED_REQUEST:
            return _stay(
                project,
                "I can't do that. Every engineering and financial figure here is "
                "computed by the backend from the case data — nothing I'm told in "
                "a message can change one.\n\n"
                + (answer.text if answer is not None else restate(project.current_step, settings)),
                accepted=False,
            )

        case ActionKind.PROVIDE_VALUE:
            return _provide_value(project, action, raw_text, settings)

        case ActionKind.CHANGE_PREVIOUS_VALUE:
            return _change_value(project, action, raw_text, settings)

        case ActionKind.UPDATE_FIELD:
            return _update_field(project, action, raw_text, settings)

        case ActionKind.COMPARE_OPTIONS:
            # A comparison is a question that happens to name two things. The
            # answer service already assembles it from the calculated figures,
            # and nothing about asking may move the workflow.
            return _answered_outcome(project, answer, settings)

        case ActionKind.CLARIFY:
            # The router could not tell what was meant and declined to guess.
            # The pending question is restated underneath, so a customer who
            # simply mistyped can carry on without re-reading the thread.
            prompt = restate(project.current_step, settings)
            text = action.clarification or "Sorry - which value did you mean?"
            return _stay(
                project,
                f"{text}\n\n{prompt}" if prompt and prompt not in text else text,
                accepted=False,
            )

        case ActionKind.NAVIGATE:
            return _navigate(project, action, settings)

        case ActionKind.CONFIRM:
            return _confirm(project, action, raw_text, settings)

        case ActionKind.RESET:
            return _reset(project)

        case ActionKind.CANCEL:
            prompt = restate(project.current_step, settings)
            return _stay(
                project,
                f"No problem — nothing changed. {prompt}" if prompt else "No problem.",
                accepted=True,
            )

        case ActionKind.UNKNOWN:
            return _unknown(project, settings)

        case _:  # pragma: no cover - exhaustiveness guard
            assert_never(action.kind)


def _unknown(project: ProjectState, settings: Settings) -> StepOutcome:
    match project.current_step:
        case ProjectStep.LOCATION:
            return _stay(
                project,
                "I couldn't read a location there. " + location_prompt(settings),
            )
        case ProjectStep.CONSUMPTION:
            return _stay(project, _consumption_refusal(ConversationAction(kind=ActionKind.UNKNOWN)))
        case ProjectStep.SYSTEM_SIZE:
            return _stay(
                project, _size_refusal(ConversationAction(kind=ActionKind.UNKNOWN), settings)
            )
        case _:
            return _stay(
                project,
                "I didn't follow that. I can explain the roof measurements, how "
                "the panels are placed, where the production figures come from, "
                "the exchange rate, or how payback is worked out.",
            )


def initial_assistant_message() -> str:
    return WELCOME


def progress(step: ProjectStep) -> list[dict[str, object]]:
    """Progress rail state for the UI."""
    labels = [
        (ProjectStep.LOCATION, "Location"),
        (ProjectStep.CONSUMPTION, "Usage"),
        (ProjectStep.SYSTEM_SIZE, "System"),
        (ProjectStep.ROOF_RECONSTRUCTION, "Roof"),
        (ProjectStep.PANEL_LAYOUT, "Layout"),
        (ProjectStep.ENERGY_YIELD, "Yield"),
        (ProjectStep.EXCHANGE_RATE, "FX"),
        (ProjectStep.FINANCIAL_ANALYSIS, "Finance"),
        (ProjectStep.PROPOSAL, "Proposal"),
    ]
    order = [s for s, _ in labels]
    try:
        current_index = order.index(step)
    except ValueError:
        current_index = len(order)  # COMPLETED

    return [
        {
            "step": s.value,
            "label": label,
            "state": (
                "done" if i < current_index else "active" if i == current_index else "pending"
            ),
        }
        for i, (s, label) in enumerate(labels)
    ]


__all__ = [
    "CHANGEABLE",
    "CONSUMPTION_PROMPT",
    "WELCOME",
    "ExtractedValues",
    "ProjectState",
    "StepOutcome",
    "handle_message",
    "initial_assistant_message",
    "location_offer",
    "location_prompt",
    "progress",
    "restate",
    "system_size_prompt",
]


def _update_field(
    project: ProjectState,
    action: ConversationAction,
    raw_text: str,
    settings: Settings,
) -> StepOutcome:
    """Set a field the customer named, from wherever they happen to be.

    The field arrives already converted into the unit the workflow stores, so
    this does not re-interpret it - it applies the same validation and the same
    whitelist a value supplied at its own step would face. An update is not a
    shortcut around the state machine; it is a different way in to it.

    Tariff is recognised by the router but not yet storable: there is no
    per-project tariff column, and the financial model reads a configured rate.
    Saying so is better than accepting the number and quietly ignoring it, which
    would leave the customer believing their payback had been recalculated.
    """
    from app.services.conversation import field_updates

    if action.field == field_updates.FIELD_TARIFF:
        prompt = restate(project.current_step, settings)
        return _stay(
            project,
            "I can't change the electricity tariff yet — the financial model uses "
            f"a configured rate of {settings.case_electricity_price:.2f} EUR/kWh "
            "for this case, and per-project tariffs aren't stored. Everything else "
            "you've given me still stands."
            + (f"\n\n{prompt}" if prompt else ""),
            accepted=False,
        )

    # Consumption and system size both map to columns the state machine already
    # owns, so they go through the existing correction path and inherit its
    # validation, its recalculation and its wording.
    return _change_value(project, action, raw_text, settings)
