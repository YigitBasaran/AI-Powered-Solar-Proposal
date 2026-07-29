"""Customer-facing executive summary.

The language model is allowed to write *prose*. It is not allowed to produce a
*number*.

That distinction is enforced rather than requested. The model receives a small,
closed set of already-computed values and is asked to describe them. Whatever
comes back is then checked: every number appearing in the generated text must
match one of the values it was given. A summary containing an invented,
recalculated or subtly altered figure is discarded and the deterministic
template is used instead.

This matters because a hallucinated payback period in customer-facing prose is
indistinguishable from a real one, and sits directly above a table of correct
figures that nobody re-reads.
"""

from __future__ import annotations

import asyncio
import logging
import re
from decimal import Decimal
from typing import Any

from app.core.config import LlmProvider, Settings, get_settings
from app.core.errors import LlmUnavailableError

logger = logging.getLogger("solarvis.summary")

# Numbers the model is permitted to echo, plus small integers it may reasonably
# use for counting ("all four facets", "the two triangles").
ALWAYS_ALLOWED = {Decimal(n) for n in range(0, 25)}

# A number in the generated prose, with optional thousands separators.
NUMBER_IN_TEXT = re.compile(r"\d[\d,]*(?:\.\d+)?")

MAX_WORDS = 110


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value).replace(",", ""))
    except Exception:
        return None


def allowed_values(snapshot: dict[str, Any]) -> set[Decimal]:
    """Every number the model is allowed to repeat.

    Built from the snapshot itself, including the rounded forms a writer would
    naturally use, so "9,502 kWh" is accepted for 9502.18 but "9,800" is not.
    """
    layout = snapshot["layout"]
    energy = snapshot["energy"]
    financial = snapshot["financial"]
    fx = snapshot["exchangeRate"]

    raw: list[Any] = [
        layout["requestedSystemSizeKwp"],
        layout["feasibleSystemSizeKwp"],
        layout["requestedPanelCount"],
        layout["placedPanelCount"],
        len(layout.get("facets", [])),
        energy["totalAnnualProductionKwh"],
        energy["installedPowerKwp"],
        financial["annualConsumptionKwh"],
        financial["annualProductionKwh"],
        financial["coveredEnergyKwh"],
        financial["coveragePercent"],
        financial["annualSavingsEur"],
        financial["electricityPriceEurPerKwh"],
        financial["originalCapex"]["amount"],
        financial["convertedCapex"]["amount"],
        financial["simplePaybackYears"],
        financial["twentyYearNetBenefitEur"],
        len(financial.get("cashFlow", [])) - 1,  # the analysis period, 20
        fx["rate"],
    ]
    raw.extend(f["annualProductionKwh"] for f in energy.get("facets", []))
    raw.extend(f["panelCount"] for f in energy.get("facets", []))

    allowed: set[Decimal] = set(ALWAYS_ALLOWED)
    for value in raw:
        if value is None:
            continue
        number = _decimal(value)
        if number is None:
            continue
        allowed.add(number)
        # Rounded forms a human writer would use.
        allowed.add(number.quantize(Decimal("1")))
        allowed.add(number.quantize(Decimal("0.1")))
        allowed.add(number.quantize(Decimal("0.01")))
    return allowed


def unsupported_numbers(text: str, allowed: set[Decimal]) -> list[str]:
    """Numbers in `text` that are not among the values supplied to the model."""
    offenders: list[str] = []
    for match in NUMBER_IN_TEXT.finditer(text):
        token = match.group(0)
        number = _decimal(token)
        if number is None:
            continue
        if number not in allowed:
            offenders.append(token)
    return offenders


def deterministic_summary(snapshot: dict[str, Any]) -> str:
    """The fallback, and the reference the model's output is judged against.

    Written from the same values, by code. If the model is unavailable, slow,
    or produces something that does not validate, this is what ships - so the
    proposal never depends on a model being present.
    """
    layout = snapshot["layout"]
    energy = snapshot["energy"]
    financial = snapshot["financial"]
    fx = snapshot["exchangeRate"]

    facets = len(layout.get("facets", []))
    payback = financial.get("simplePaybackYears")

    parts = [
        f"This {layout['feasibleSystemSizeKwp']:g} kWp system places "
        f"{layout['placedPanelCount']} panels across "
        f"{facets} roof {'facet' if facets == 1 else 'facets'}, "
        f"generating an estimated {float(energy['totalAnnualProductionKwh']):,.0f} kWh "
        f"per year.",
        f"That covers {float(financial['coveragePercent']):.1f}% of your "
        f"{float(financial['annualConsumptionKwh']):,.0f} kWh annual usage and saves "
        f"about EUR {float(financial['annualSavingsEur']):,.0f} a year at "
        f"EUR {financial['electricityPriceEurPerKwh']} per kWh.",
        f"The capital cost of USD {float(financial['originalCapex']['amount']):,.0f} "
        f"converts to EUR {float(financial['convertedCapex']['amount']):,.2f} at the "
        f"{fx['dataProvider']} reference rate of {fx['rate']}.",
    ]
    if payback:
        parts.append(
            f"The system pays for itself in about {float(payback):.1f} years, with a "
            f"cumulative net benefit of EUR "
            f"{float(financial['twentyYearNetBenefitEur']):,.0f} over 20 years."
        )
    else:
        parts.append("The system does not pay back within the 20-year analysis period.")

    if layout.get("capacityWarning"):
        parts.append(
            f"Note: only {layout['placedPanelCount']} of the "
            f"{layout['requestedPanelCount']} panels requested physically fit on this roof, "
            f"and every figure above reflects what fits."
        )
    return " ".join(parts)


def _prompt_values(snapshot: dict[str, Any]) -> dict[str, Any]:
    """The closed set of facts handed to the model."""
    layout = snapshot["layout"]
    energy = snapshot["energy"]
    financial = snapshot["financial"]
    fx = snapshot["exchangeRate"]
    return {
        "systemSizeKwp": layout["feasibleSystemSizeKwp"],
        "panelCount": layout["placedPanelCount"],
        "facetCount": len(layout.get("facets", [])),
        "annualProductionKwh": round(float(energy["totalAnnualProductionKwh"])),
        "annualConsumptionKwh": round(float(financial["annualConsumptionKwh"])),
        "coveragePercent": round(float(financial["coveragePercent"]), 1),
        "annualSavingsEur": financial["annualSavingsEur"],
        "electricityPriceEurPerKwh": financial["electricityPriceEurPerKwh"],
        "originalCapexUsd": financial["originalCapex"]["amount"],
        "convertedCapexEur": financial["convertedCapex"]["amount"],
        "usdToEurRate": fx["rate"],
        "simplePaybackYears": (
            round(float(financial["simplePaybackYears"]), 2)
            if financial.get("simplePaybackYears")
            else None
        ),
        "twentyYearNetBenefitEur": financial["twentyYearNetBenefitEur"],
    }


async def generate_summary(
    snapshot: dict[str, Any], settings: Settings | None = None
) -> tuple[str, str]:
    """Return `(summary, source)` where source is "llm" or "deterministic".

    The model is only ever an improvement on the fallback, never a dependency:
    any failure, timeout, or unvalidatable output falls through to the template.

    That includes taking too long. This call sits inside the request that
    finalises a proposal, so every second it spends is a second the customer
    watches a spinner and an intermediate proxy counts towards its own idle
    timeout. A proxy that gives up first loses the *response* to work that has
    already been done: the proposal exists, and the customer is looking at an
    error. Observed on 2026-07-29 with `OLLAMA_TIMEOUT_SECONDS=120` — the web
    container logged `socket hang up` for a finalize whose proposal row was
    written seconds later.

    So the budget here is separate from, and shorter than, the transport
    timeout, and it bounds the whole attempt rather than one read.
    """
    settings = settings or get_settings()
    fallback = deterministic_summary(snapshot)

    if settings.llm_provider is not LlmProvider.OLLAMA:
        return fallback, "deterministic"

    from app.integrations.ollama import OllamaClient

    try:
        async with asyncio.timeout(settings.summary_timeout_seconds):
            text = await OllamaClient(settings).explain(_prompt_values(snapshot))
    except TimeoutError:
        logger.warning(
            "summary: model exceeded the %.0fs budget; using deterministic text",
            settings.summary_timeout_seconds,
        )
        return fallback, "deterministic"
    except LlmUnavailableError as exc:
        logger.info("summary: model unavailable (%s); using deterministic text", exc)
        return fallback, "deterministic"

    text = " ".join(text.split())
    if not text:
        logger.info("summary: model returned nothing; using deterministic text")
        return fallback, "deterministic"

    if len(text.split()) > MAX_WORDS:
        logger.warning("summary: model exceeded the word limit; using deterministic text")
        return fallback, "deterministic"

    offenders = unsupported_numbers(text, allowed_values(snapshot))
    if offenders:
        # The model invented, recalculated or altered a figure. Discarding the
        # whole summary is the only safe response: there is no way to tell which
        # of the remaining sentences were also fabricated.
        logger.warning(
            "summary: rejected model output containing unsupported numbers %s", offenders
        )
        return fallback, "deterministic"

    return text, "llm"
