"""What the user is trying to do, and what may be said back.

The router produces a :class:`ConversationAction`; the answer service produces
an :class:`Answer`. Neither touches the database. The state machine decides
whether an action is *allowed*, and only it may mutate anything.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from app.domain.models import ExtractedValues, ProjectStep


class ActionKind(StrEnum):
    """What kind of move the user made.

    Distinct from *which value* they supplied - that is `ExtractedValues`. A
    message can be a `provide_value` whose value is unusable; the kind says
    what they were doing, the extraction status says how it went.
    """

    PROVIDE_VALUE = "provide_value"
    #: A named field is being set, whatever step is pending.
    #:
    #: Distinct from `CHANGE_PREVIOUS_VALUE`, which only ever meant "correct the
    #: thing we were just discussing" and had to guess which field that was.
    #: This one carries the field explicitly, so it can be honoured from any
    #: step without the guess.
    UPDATE_FIELD = "update_field"
    COMPARE_OPTIONS = "compare_options"
    #: The message could mean more than one thing and we will not pick for them.
    CLARIFY = "clarify"
    ASK_QUESTION = "ask_question"
    REQUEST_OPTIONS = "request_options"
    REQUEST_EXPLANATION = "request_explanation"
    CHANGE_PREVIOUS_VALUE = "change_previous_value"
    NAVIGATE = "navigate"
    CONFIRM = "confirm"
    #: "Email this to the customer."
    #:
    #: Never itself a send. It sets a pending confirmation and shows who would
    #: receive it; only a `CONFIRM` on the very next turn causes anything to
    #: leave the building. Kept a distinct kind rather than folded into
    #: `NAVIGATE` or `PROVIDE_VALUE` so that "a message can request a send" is
    #: something the type system states and the exhaustive `match` in
    #: `handle_message` forces every reader to account for.
    SEND_PROPOSAL = "send_proposal"
    RESET = "reset"
    CANCEL = "cancel"
    UNSUPPORTED_REQUEST = "unsupported_request"
    OFF_TOPIC = "off_topic"
    UNKNOWN = "unknown"


class Topic(StrEnum):
    """What the message is about, independent of what kind of move it is."""

    LOCATION = "location"
    CONSUMPTION = "consumption"
    SYSTEM_SIZE = "system_size"
    ROOF = "roof"
    LAYOUT = "layout"
    YIELD = "yield"
    FX = "fx"
    FINANCE = "finance"
    PROPOSAL = "proposal"
    PRIVACY = "privacy"
    GENERAL = "general"


class ExtractionStatus(StrEnum):
    """How the step's extractor got on.

    The distinction between ABSENT and the other three is what keeps a
    question from being eaten by a numeric extractor, and what keeps an
    unusable figure from being mistaken for a question:

        A message from which the extractor read a *quantity* - valid or not -
        is an answer to the question that was asked, not a question about it.
        A message from which it read nothing falls through to the question
        detector.

    Without AMBIGUOUS, "a little over a thousand" would have to be reported as
    "not greater than zero", which is nonsense for a phrase containing no
    number at all.
    """

    ABSENT = "absent"
    INVALID = "invalid"
    AMBIGUOUS = "ambiguous"
    VALID = "valid"


class AnswerState(StrEnum):
    """Whether a question can be answered, and on what basis.

    ``NOT_CALCULATED_YET`` and ``ANSWERABLE_AS_METHODOLOGY`` overlap, so the
    rule is written down rather than left to taste:

        NOT_CALCULATED_YET implies the answer text *is* the methodology text,
        plus a note of what is still needed.

    So "how will payback be calculated?" is ANSWERABLE_AS_METHODOLOGY at any
    step, while "what is my payback?" before the analysis is NOT_CALCULATED_YET
    and says the same thing with the missing inputs named.
    """

    ANSWERABLE_NOW = "answerable_now"
    ANSWERABLE_AS_METHODOLOGY = "answerable_as_methodology"
    NOT_CALCULATED_YET = "not_calculated_yet"
    RECALCULATING = "recalculating"
    OUT_OF_SCOPE = "out_of_scope"
    UNSUPPORTED = "unsupported"


class AnswerSource(StrEnum):
    """Where an answer's facts came from. Ordered: earlier wins.

    A lower-priority source may never override a higher one. This is enforced
    structurally rather than by prompt: the model is handed a closed fact
    bundle and the already-composed deterministic answer, and is only ever
    asked to reword - so an LLM answer is by construction a view of a higher
    source.
    """

    PROPOSAL_SNAPSHOT = "proposal_snapshot"
    ANALYSIS = "analysis"
    CASE_CONFIG = "case_config"
    HELP_KNOWLEDGE = "help_knowledge"
    LLM_PARAPHRASE = "llm_paraphrase"
    NONE = "none"


class ConversationAction(BaseModel):
    """The router's output. Never carries an instruction to mutate anything.

    ``target_step`` is derived deterministically from
    ``(current_step, kind, topic)``. It is **not** in the model-facing schema:
    letting a language model name the next step would hand it a workflow
    control channel, which is exactly what the state machine exists to own.
    """

    kind: ActionKind
    topic: Topic = Topic.GENERAL

    values: ExtractedValues = Field(default_factory=ExtractedValues)
    extraction: ExtractionStatus = ExtractionStatus.ABSENT

    #: Why an extraction failed - "not_positive", "vague_quantity", ... Used to
    #: choose the wording, never to decide whether the value is acceptable.
    reason: str | None = None

    #: The question as the user asked it, for the answer service and the log.
    question: str | None = None

    target_step: ProjectStep | None = None

    #: Which field an `UPDATE_FIELD` names, and what it should become.
    #:
    #: Carried separately from `values` because `values` is keyed by the
    #: workflow's own slots, and an update may arrive in units the slot does not
    #: use - an annual consumption figure has to become a monthly one before the
    #: state machine ever sees it.
    field: str | None = None
    field_value: float | None = None
    field_unit: str | None = None

    #: What to ask when the message is ambiguous. Never a guess at the answer.
    clarification: str | None = None

    @property
    def is_question(self) -> bool:
        return self.kind in {
            ActionKind.ASK_QUESTION,
            ActionKind.REQUEST_OPTIONS,
            ActionKind.REQUEST_EXPLANATION,
        }

    @property
    def wants_mutation(self) -> bool:
        return self.kind in {
            ActionKind.PROVIDE_VALUE,
            ActionKind.CHANGE_PREVIOUS_VALUE,
            ActionKind.UPDATE_FIELD,
            ActionKind.RESET,
        }


class Answer(BaseModel):
    """What the answer service produced, and how it knows."""

    text: str
    state: AnswerState
    source: AnswerSource
    topic: Topic
    help_topic: str | None = None


__all__ = [
    "ActionKind",
    "Answer",
    "AnswerSource",
    "AnswerState",
    "ConversationAction",
    "ExtractionStatus",
    "Topic",
]
