"""Who actually handled a message.

The old contract reported ``parser_source: "rules"`` both when the rules parser
succeeded and when the model was tried and failed. A degraded run was therefore
indistinguishable from a healthy one at the API boundary - which is exactly how
a defect that silenced the entire language layer went unnoticed for a whole
build.

The distinction that matters is between *the model was never needed* and *the
model was asked and could not answer*. Only the second is a fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class FallbackReason(StrEnum):
    """Why the deterministic path produced the answer.

    The first two are ordinary operation; everything below them means an
    attempted model call did not yield a usable action.
    """

    RULES_SUFFICIENT = "rules_sufficient"
    NOT_CONFIGURED = "not_configured"

    UNREACHABLE = "unreachable"
    TIMEOUT = "timeout"
    HTTP_ERROR = "http_error"
    EMPTY_RESPONSE = "empty_response"
    INVALID_JSON = "invalid_json"
    SCHEMA_REJECTED = "schema_rejected"
    DOMAIN_REJECTED = "domain_rejected"


#: Reasons that mean nothing went wrong. A customer must never be told a
#: fallback happened because the rules parser did its job.
BENIGN_REASONS = frozenset({FallbackReason.RULES_SUFFICIENT, FallbackReason.NOT_CONFIGURED})


@dataclass(frozen=True)
class Interpretation:
    """How one message came to be understood."""

    configured_provider: str
    effective_provider: str
    attempted_provider: str | None = None
    fallback_reason: str | None = None
    model_name: str | None = None
    latency_ms: int | None = None

    @property
    def is_customer_visible_fallback(self) -> bool:
        """True only when a model was genuinely reached and did not deliver.

        Deliberately **not** ``effective_provider != configured_provider``.
        That reads true for nearly every message on an Ollama-configured stack,
        because the deterministic parser answers most of them - and a chip on
        every reply looks like a permanent malfunction rather than a fallback.
        """
        if self.attempted_provider is None:
            return False
        return self.effective_provider != self.attempted_provider

    def to_payload(self) -> dict[str, object]:
        """The shape stored in `ChatMessage.payload_json` and returned to the client."""
        return {
            "configuredProvider": self.configured_provider,
            "attemptedProvider": self.attempted_provider,
            "effectiveProvider": self.effective_provider,
            "fallbackReason": self.fallback_reason,
            "modelName": self.model_name,
            "latencyMs": self.latency_ms,
        }

    @property
    def parser_source(self) -> str:
        """The legacy flat field, derived so it can never disagree with the object."""
        return "llm" if self.effective_provider == "ollama" else "rules"


def rules_only(configured_provider: str, *, reason: FallbackReason) -> Interpretation:
    """The model was never contacted."""
    return Interpretation(
        configured_provider=configured_provider,
        effective_provider="rules",
        attempted_provider=None,
        fallback_reason=reason.value,
    )


__all__ = ["BENIGN_REASONS", "FallbackReason", "Interpretation", "rules_only"]
