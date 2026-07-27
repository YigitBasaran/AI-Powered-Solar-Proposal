"""Financial feasibility.

A pure function of its inputs: no I/O, no clock, no network. Everything is
``Decimal`` because these are money figures that must reconcile exactly between
the web proposal and the PDF.

The case methodology, unchanged:

    annual consumption = monthly consumption x 12
    covered energy     = min(annual production, annual consumption)
    annual savings     = covered energy x electricity price
    payback            = converted CAPEX / annual savings

Savings are capped at consumption because the case grants no export
compensation - generating beyond what the household uses earns nothing here.

CAPEX arrives in USD and savings accrue in EUR, so the conversion is applied
before any comparison. It is passed in as an already-retrieved
:class:`CapexConversion` rather than fetched here, which is what makes a
finalised proposal reproducible: the rate is a stored input, not a live lookup.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from app.domain.models import (
    CapexConversion,
    CashFlowYear,
    ExchangeRate,
    FinancialResult,
)

ANALYSIS_YEARS = 20
CENTS = Decimal("0.01")


def to_money(value: Decimal) -> Decimal:
    """Round to cents for presentation. Internals keep full precision."""
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


def convert_capex(
    *, amount: Decimal, from_currency: str, exchange_rate: ExchangeRate
) -> CapexConversion:
    """Apply the stored rate. ``rate`` is quoted as 1 base = rate quote."""
    if from_currency.upper() != exchange_rate.base_currency.upper():
        raise ValueError(
            f"CAPEX is in {from_currency} but the rate converts from {exchange_rate.base_currency}"
        )
    return CapexConversion(
        original_amount=amount,
        original_currency=exchange_rate.base_currency,
        converted_amount=amount * exchange_rate.rate,
        converted_currency=exchange_rate.quote_currency,
        exchange_rate=exchange_rate,
    )


def annual_consumption_kwh(monthly_consumption_kwh: float) -> float:
    if monthly_consumption_kwh <= 0:
        raise ValueError("monthly consumption must be positive")
    return monthly_consumption_kwh * 12.0


def calculate_financials(
    *,
    monthly_consumption_kwh: float,
    annual_production_kwh: float,
    electricity_price_eur_per_kwh: Decimal,
    capex: CapexConversion,
    analysis_years: int = ANALYSIS_YEARS,
) -> FinancialResult:
    if annual_production_kwh < 0:
        raise ValueError("annual production cannot be negative")
    if electricity_price_eur_per_kwh <= 0:
        raise ValueError("electricity price must be positive")

    consumption = annual_consumption_kwh(monthly_consumption_kwh)
    covered = min(annual_production_kwh, consumption)
    coverage_percent = (covered / consumption) * 100.0 if consumption else 0.0

    # Canonicalise money to cents ONCE, then derive the whole series from those
    # values. Carrying full precision through and rounding each year for display
    # instead makes consecutive rows differ by 2530.57 in some years and 2530.58
    # in others, so a customer adding up the printed table cannot reconcile it
    # against the printed annual saving. Cash is discrete; the model should be
    # too. The divergence from the unrounded series is under 10 cents across
    # twenty years, well inside the case's flat-price assumption.
    savings = to_money(Decimal(str(covered)) * electricity_price_eur_per_kwh)
    capex_eur = to_money(capex.converted_amount)

    # A system that saves nothing never pays back; null is the honest answer.
    payback: float | None = float(capex_eur / savings) if savings > 0 else None

    cash_flow = [
        CashFlowYear(
            year=0,
            annual_savings_eur=Decimal("0.00"),
            cumulative_cash_flow_eur=-capex_eur,
        )
    ]
    for year in range(1, analysis_years + 1):
        cash_flow.append(
            CashFlowYear(
                year=year,
                annual_savings_eur=savings,
                cumulative_cash_flow_eur=-capex_eur + (Decimal(year) * savings),
            )
        )

    return FinancialResult(
        annual_consumption_kwh=consumption,
        annual_production_kwh=annual_production_kwh,
        covered_energy_kwh=covered,
        coverage_percent=coverage_percent,
        electricity_price_eur_per_kwh=electricity_price_eur_per_kwh,
        annual_savings_eur=savings,
        capex_conversion=CapexConversion(
            original_amount=to_money(capex.original_amount),
            original_currency=capex.original_currency,
            converted_amount=to_money(capex.converted_amount),
            converted_currency=capex.converted_currency,
            # The precise rate is preserved: only display values are rounded.
            exchange_rate=capex.exchange_rate,
        ),
        simple_payback_years=payback,
        twenty_year_net_benefit_eur=cash_flow[-1].cumulative_cash_flow_eur,
        cash_flow=cash_flow,
    )
