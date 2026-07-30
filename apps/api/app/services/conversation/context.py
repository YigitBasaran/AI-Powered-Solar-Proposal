"""What the model is told about the project before it classifies a message.

The defect this exists for: the prompt had a `Known so far:` line and nothing
ever filled it. `classify_with_model` accepted a `known` argument, the router
never passed one, and every live prompt therefore rendered `Known so far:
nothing yet`. The model was asked to interpret "make it the bigger one" while
being told nothing about what had already been chosen, and then blamed for
guessing.

No conversation history reached it either, so "actually make it 10000" had no
antecedent to resolve against.

Two rules shape what goes in.

**Only verified state.** Every figure here is one the backend computed and
stored. The model is never asked to produce a number; it is given the numbers so
it can *refer* to them. That is the difference between "6 kWp covers about 69% of
your usage" and a plausible invention.

**Bounded, not complete.** A transcript that grows without limit eventually
crowds out the instructions, and the tail of a long conversation is where the
model's attention goes - so an old turn can quietly outrank the current one. A
compact summary plus a short recent window keeps the prompt a fixed size and
keeps the *state* authoritative rather than whatever was said twenty turns ago.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: How many recent turns travel with the summary.
#:
#: Enough to resolve "make it the bigger one" against the message that offered
#: the options, and not so many that the transcript competes with the state.
RECENT_TURN_LIMIT = 6

#: Longest a single quoted turn may be before it is trimmed.
MAX_TURN_CHARS = 320


@dataclass(frozen=True)
class Turn:
    role: str
    content: str


@dataclass(frozen=True)
class ConversationContext:
    """The compact, authoritative picture handed to the model."""

    step: str
    pending_question: str
    known: dict[str, Any] = field(default_factory=dict)
    results: dict[str, Any] = field(default_factory=dict)
    choices: list[str] = field(default_factory=list)
    recent: list[Turn] = field(default_factory=list)

    def as_prompt_block(self) -> str:
        """The context as the model sees it.

        Rendered as labelled lines rather than JSON: a small instruct model
        follows prose more reliably than it follows a nested object, and the
        labels double as a reminder of which values are settled.
        """
        lines = [f"Current step: {self.step}"]
        if self.pending_question:
            lines.append(f"Pending question: {self.pending_question}")

        lines.append(
            "Confirmed so far: "
            + (
                ", ".join(f"{k} = {v}" for k, v in self.known.items())
                if self.known
                else "nothing yet"
            )
        )
        if self.choices:
            lines.append("Available choices: " + ", ".join(self.choices))
        if self.results:
            lines.append(
                "Calculated results (computed by the backend - never invent or alter these): "
                + ", ".join(f"{k} = {v}" for k, v in self.results.items())
            )
        if self.recent:
            lines.append("Recent turns, oldest first:")
            lines.extend(f"  {t.role}: {t.content}" for t in self.recent)
        return "\n".join(lines)


def _put(target: dict[str, Any], key: str, value: Any, spec: str) -> None:
    """Format a snapshot figure, or omit it.

    Snapshot values arrive from JSON and are not guaranteed to be the type this
    would like them to be - a currency amount may be a string, and a
    `Decimal`-backed field certainly is. A prompt is not worth a 500, so an
    unformattable value is left out rather than raised on.
    """
    if value is None:
        return
    try:
        target[key] = format(int(value) if spec == "d" else float(value), spec)
    except (TypeError, ValueError):
        target[key] = str(value)


def _trim(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= MAX_TURN_CHARS else flat[: MAX_TURN_CHARS - 1] + "…"


def build_context(
    project: Any,
    *,
    settings: Any,
    recent: list[Turn] | None = None,
) -> ConversationContext:
    """Assemble the model's view of a project from stored state only."""
    known: dict[str, Any] = {}
    if project.raw_location_input:
        known["property"] = "the verified case property (Galway Road, Cape Town)"
    if project.monthly_consumption_kwh is not None:
        monthly = project.monthly_consumption_kwh
        known["monthly_consumption_kwh"] = f"{monthly:,.0f}"
        known["annual_consumption_kwh"] = f"{monthly * 12:,.0f}"
    if project.selected_system_size_kwp is not None:
        known["system_size_kwp"] = f"{project.selected_system_size_kwp:g}"

    results: dict[str, Any] = {}
    snapshot = project.analysis if isinstance(project.analysis, dict) else None
    if snapshot:
        energy = snapshot.get("energy") or {}
        layout = snapshot.get("layout") or {}
        financial = snapshot.get("financial") or {}
        _put(results, "annual_production_kwh", energy.get("totalAnnualProductionKwh"), ",.0f")
        _put(results, "panels_placed", layout.get("placedPanelCount"), "d")
        _put(results, "coverage_percent", financial.get("coveragePercent"), ".1f")
        _put(results, "annual_savings_eur", financial.get("annualSavingsEur"), ",.2f")
        _put(results, "payback_years", financial.get("simplePaybackYears"), ".2f")

    choices = [f"{size:g} kWp" for size in settings.allowed_system_sizes_kwp]

    return ConversationContext(
        step=str(getattr(project.current_step, "value", project.current_step)),
        pending_question=_pending_question(project, settings),
        known=known,
        results=results,
        choices=choices,
        recent=[Turn(t.role, _trim(t.content)) for t in (recent or [])][-RECENT_TURN_LIMIT:],
    )


def _pending_question(project: Any, settings: Any) -> str:
    from app.services.workflow import restate

    try:
        return " ".join(restate(project.current_step, settings).split())
    except Exception:  # pragma: no cover - a prompt must never fail the request
        return ""
