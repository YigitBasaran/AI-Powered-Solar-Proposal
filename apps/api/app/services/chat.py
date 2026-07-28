"""Message parsing pipeline: deterministic rules first, model second.

    user message
        -> step-aware rules parser
            parsed safely?  yes -> validated intent
                            no  -> Ollama structured output
                                     -> Pydantic validation
                                     -> state-machine validation

The rules parser handles every phrasing the case demonstrates, so the model is
a convenience for unusual wording rather than a dependency. When it is absent,
unavailable, or returns something that does not validate, the pipeline falls
back to UNKNOWN and the workflow asks the user to rephrase - it never guesses.
"""

from __future__ import annotations

import logging

from app.core.config import LlmProvider, Settings, get_settings
from app.core.errors import LlmUnavailableError
from app.domain.models import ChatIntent, ParsedChatMessage, ProjectStep
from app.services.rules_parser import parse_message as rules_parse

logger = logging.getLogger("solarvis.chat")

ParserSource = str  # "rules" | "llm" | "none"

# Values the model supplies are re-checked here. Trusting a model-supplied
# number because it type-checked would defeat the point of the whitelist.
ALLOWED_SIZES = (3.6, 6.0, 9.6)


def _sanitise(parsed: ParsedChatMessage, step: ProjectStep) -> ParsedChatMessage:
    """Strip anything the model had no business supplying at this step."""
    if step is ProjectStep.LOCATION and parsed.intent is ChatIntent.PROVIDE_LOCATION:
        # A written place name carries no coordinates, and that is fine: the
        # workflow always analyses the verified case coordinate and never reads
        # these fields. What must not survive is a *coordinate* that is not a
        # coordinate, whether a model or a regex produced it.
        has_lat = parsed.latitude is not None
        has_lon = parsed.longitude is not None
        if has_lat != has_lon:
            return ParsedChatMessage(intent=ChatIntent.UNKNOWN, confidence=0.0)
        if has_lat and not (
            -90.0 <= parsed.latitude <= 90.0  # type: ignore[operator]
            and -180.0 <= parsed.longitude <= 180.0  # type: ignore[operator]
        ):
            return ParsedChatMessage(intent=ChatIntent.UNKNOWN, confidence=0.0)

    if step is ProjectStep.CONSUMPTION and parsed.intent is ChatIntent.PROVIDE_CONSUMPTION:
        value = parsed.monthly_consumption_kwh
        if value is None or value <= 0 or value > 1_000_000:
            return ParsedChatMessage(intent=ChatIntent.UNKNOWN, confidence=0.0)

    if step is ProjectStep.SYSTEM_SIZE and parsed.intent is ChatIntent.SELECT_SYSTEM_SIZE:
        size = parsed.system_size_kwp
        if size is None or float(size) not in ALLOWED_SIZES:
            return ParsedChatMessage(intent=ChatIntent.UNKNOWN, confidence=0.0)

    return parsed


async def parse_user_message(
    text: str, *, step: ProjectStep, settings: Settings | None = None
) -> tuple[ParsedChatMessage, ParserSource]:
    settings = settings or get_settings()

    parsed = rules_parse(text, step=step.value)
    if parsed.intent is not ChatIntent.UNKNOWN:
        return _sanitise(parsed, step), "rules"

    if settings.llm_provider is not LlmProvider.OLLAMA:
        return parsed, "rules"

    from app.integrations.ollama import OllamaClient

    try:
        llm_parsed = await OllamaClient(settings).parse_message(text, step=step)
    except LlmUnavailableError as exc:
        logger.info("LLM unavailable, staying with rules result: %s", exc)
        if not settings.llm_fallback_enabled:
            raise
        return parsed, "rules"

    return _sanitise(llm_parsed, step), "llm"
