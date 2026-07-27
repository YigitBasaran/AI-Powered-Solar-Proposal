"""The chat-driven workflow state machine.

Every transition is validated here, in deterministic code. The language model
may interpret what a user *meant*, but it can only ever hand this module a
:class:`ParsedChatMessage`, which is then checked against the same rules a
rules-parsed message faces. There is no path by which a model response can move
the workflow somewhere the state machine would not allow, or set a value the
state machine would not accept.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings, get_settings
from app.core.errors import InvalidStepTransitionError, ValidationError
from app.domain.models import ChatIntent, ParsedChatMessage, ProjectStep

WELCOME = (
    "Welcome to solarVis AI.\n\n"
    "I'll guide you through a complete solar feasibility assessment, including "
    "roof measurements, panel placement, annual production, financial return "
    "and a shareable proposal.\n\n"
    "To begin, enter the project latitude and longitude."
)

CONSUMPTION_PROMPT = "What is your monthly electricity consumption?"


@dataclass
class StepOutcome:
    """The result of handling one user message."""

    assistant_message: str
    next_step: ProjectStep
    updates: dict[str, object]
    accepted: bool = True


def system_size_prompt(settings: Settings) -> str:
    lines = ["Which system size would you like?", ""]
    for size in settings.allowed_system_sizes_kwp:
        count = settings.required_panel_count(size)
        lines.append(f"  - {size:g} kWp  ({count} panels x {settings.panel_power_wp} Wp)")
    lines.append("")
    lines.append("You can reply with a size, a panel count, or 'smallest', 'middle' or 'largest'.")
    return "\n".join(lines)


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


def handle_message(
    *,
    current_step: ProjectStep,
    parsed: ParsedChatMessage,
    raw_text: str,
    settings: Settings | None = None,
) -> StepOutcome:
    """Apply one parsed message to the workflow.

    Raises :class:`InvalidStepTransitionError` when a value arrives for a step
    that is not the current one, so a stray or replayed message cannot rewrite
    an earlier answer.
    """
    settings = settings or get_settings()

    if current_step is ProjectStep.LOCATION:
        if parsed.intent is not ChatIntent.PROVIDE_LOCATION:
            return StepOutcome(
                assistant_message=(
                    "I need a latitude and longitude to start. For example: -34.04658, 18.46491"
                ),
                next_step=ProjectStep.LOCATION,
                updates={},
                accepted=False,
            )
        loc = settings.case_location
        return StepOutcome(
            assistant_message=_describe_location(settings, raw_text),
            next_step=ProjectStep.CONSUMPTION,
            updates={
                "raw_location_input": raw_text,
                "resolved_latitude": loc.resolved_latitude,
                "resolved_longitude": loc.resolved_longitude,
            },
        )

    if current_step is ProjectStep.CONSUMPTION:
        if parsed.intent is not ChatIntent.PROVIDE_CONSUMPTION or (
            parsed.monthly_consumption_kwh is None
        ):
            return StepOutcome(
                assistant_message=(
                    "I couldn't read a consumption figure. Please give your "
                    "monthly electricity use in kWh, for example: 1,150 kWh"
                ),
                next_step=ProjectStep.CONSUMPTION,
                updates={},
                accepted=False,
            )
        monthly = parsed.monthly_consumption_kwh
        if monthly <= 0:
            raise ValidationError("Monthly consumption must be greater than zero.")
        return StepOutcome(
            assistant_message=_describe_consumption(settings, monthly),
            next_step=ProjectStep.SYSTEM_SIZE,
            updates={"monthly_consumption_kwh": monthly},
        )

    if current_step is ProjectStep.SYSTEM_SIZE:
        if parsed.intent is not ChatIntent.SELECT_SYSTEM_SIZE or (parsed.system_size_kwp is None):
            return StepOutcome(
                assistant_message=(
                    "Please choose one of the three available sizes.\n\n"
                    + system_size_prompt(settings)
                ),
                next_step=ProjectStep.SYSTEM_SIZE,
                updates={},
                accepted=False,
            )
        size = float(parsed.system_size_kwp)
        # Defence in depth: the whitelist is enforced here as well as in the
        # parser, because this is the only place a model-supplied value lands.
        if size not in settings.allowed_system_sizes_kwp:
            raise ValidationError(
                f"{size} kWp is not an available system size.",
                details={"allowed": list(settings.allowed_system_sizes_kwp)},
            )
        return StepOutcome(
            assistant_message=_describe_system_size(settings, size),
            next_step=ProjectStep.ROOF_RECONSTRUCTION,
            updates={"selected_system_size_kwp": size},
        )

    if parsed.intent in (
        ChatIntent.ASK_ROOF_QUESTION,
        ChatIntent.ASK_ENERGY_QUESTION,
        ChatIntent.ASK_FINANCIAL_QUESTION,
        ChatIntent.CONFIRM,
        ChatIntent.UNKNOWN,
    ):
        return StepOutcome(
            assistant_message="",  # answered by the explanation layer
            next_step=current_step,
            updates={},
            accepted=False,
        )

    raise InvalidStepTransitionError(
        f"Nothing to do for intent {parsed.intent} at step {current_step}."
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
