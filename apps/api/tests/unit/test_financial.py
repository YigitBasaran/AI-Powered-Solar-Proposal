"""Financial service tests, including the end-to-end case scenario."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.domain.models import ExchangeRate, ExchangeRateSource
from app.services.financial import (
    annual_consumption_kwh,
    calculate_financials,
    convert_capex,
)

PRICE = Decimal("0.25")
CAPEX_USD = Decimal("10000")
RATE = Decimal("0.87897")


def make_rate(value: Decimal = RATE, *, base: str = "USD") -> ExchangeRate:
    return ExchangeRate(
        source_api="Frankfurter",
        data_provider="ECB",
        rate_date=date(2026, 7, 24),
        base_currency=base,
        quote_currency="EUR",
        rate=value,
        retrieval_source=ExchangeRateSource.LIVE,
        retrieved_at=datetime.now(UTC),
    )


def make_capex(value: Decimal = RATE):
    return convert_capex(amount=CAPEX_USD, from_currency="USD", exchange_rate=make_rate(value))


# ---------------------------------------------------------------------------
# Consumption
# ---------------------------------------------------------------------------


def test_case_annual_consumption() -> None:
    assert annual_consumption_kwh(1150) == 13_800.0


@pytest.mark.parametrize("bad", [0, -1, -1150])
def test_non_positive_consumption_is_rejected(bad) -> None:
    with pytest.raises(ValueError):
        annual_consumption_kwh(bad)


# ---------------------------------------------------------------------------
# CAPEX conversion
# ---------------------------------------------------------------------------


def test_capex_converts_by_multiplying_by_the_rate() -> None:
    capex = make_capex()
    assert capex.converted_amount == CAPEX_USD * RATE
    assert capex.converted_amount == Decimal("8789.70000")
    assert capex.original_currency == "USD"
    assert capex.converted_currency == "EUR"


def test_capex_conversion_direction_is_not_inverted() -> None:
    """1 USD = 0.879 EUR, so USD CAPEX must shrink, not grow."""
    assert make_capex().converted_amount < CAPEX_USD


def test_capex_rejects_a_currency_mismatch() -> None:
    with pytest.raises(ValueError):
        convert_capex(amount=CAPEX_USD, from_currency="GBP", exchange_rate=make_rate(base="USD"))


def test_capex_conversion_keeps_the_precise_rate() -> None:
    result = calculate_financials(
        monthly_consumption_kwh=1150,
        annual_production_kwh=10_122.31,
        electricity_price_eur_per_kwh=PRICE,
        capex=make_capex(),
    )
    assert result.capex_conversion.exchange_rate.rate == Decimal("0.87897")
    assert result.capex_conversion.converted_amount == Decimal("8789.70")


# ---------------------------------------------------------------------------
# The case scenario
# ---------------------------------------------------------------------------


@pytest.fixture
def case_result():
    """6 kWp all-north at the resolved site: the headline scenario."""
    return calculate_financials(
        monthly_consumption_kwh=1150,
        annual_production_kwh=10_122.31,
        electricity_price_eur_per_kwh=PRICE,
        capex=make_capex(),
    )


def test_case_consumption_and_coverage(case_result) -> None:
    assert case_result.annual_consumption_kwh == 13_800.0
    assert case_result.covered_energy_kwh == pytest.approx(10_122.31)
    assert case_result.coverage_percent == pytest.approx(73.35, abs=0.02)


def test_case_annual_savings(case_result) -> None:
    assert case_result.annual_savings_eur == Decimal("2530.58")


def test_case_payback(case_result) -> None:
    assert case_result.simple_payback_years == pytest.approx(3.473, abs=0.005)


def test_case_twenty_year_net_benefit(case_result) -> None:
    assert case_result.twenty_year_net_benefit_eur == Decimal("41821.90")


def test_cash_flow_spans_year_zero_to_twenty(case_result) -> None:
    assert [y.year for y in case_result.cash_flow] == list(range(21))


def test_year_zero_is_the_negative_converted_capex(case_result) -> None:
    year0 = case_result.cash_flow[0]
    assert year0.cumulative_cash_flow_eur == Decimal("-8789.70")
    assert year0.annual_savings_eur == Decimal("0.00")


def test_year_one_adds_one_year_of_savings(case_result) -> None:
    year1 = case_result.cash_flow[1]
    assert year1.annual_savings_eur == Decimal("2530.58")
    assert year1.cumulative_cash_flow_eur == Decimal("-6259.12")


def test_year_twenty_matches_the_headline_net_benefit(case_result) -> None:
    assert case_result.cash_flow[20].cumulative_cash_flow_eur == (
        case_result.twenty_year_net_benefit_eur
    )


def test_cumulative_cash_flow_crosses_zero_at_the_payback_year(case_result) -> None:
    payback = case_result.simple_payback_years
    assert payback is not None
    below = [y for y in case_result.cash_flow if y.cumulative_cash_flow_eur < 0]
    assert max(y.year for y in below) == int(payback)


def test_parity_would_have_misstated_the_payback(case_result) -> None:
    """Why the FX work exists at all."""
    naive = calculate_financials(
        monthly_consumption_kwh=1150,
        annual_production_kwh=10_122.31,
        electricity_price_eur_per_kwh=PRICE,
        capex=make_capex(Decimal("1.0")),
    )
    assert naive.simple_payback_years is not None
    assert case_result.simple_payback_years is not None
    error = naive.simple_payback_years / case_result.simple_payback_years - 1.0
    assert error == pytest.approx(0.1378, abs=0.005)


# ---------------------------------------------------------------------------
# Coverage cap
# ---------------------------------------------------------------------------


def test_production_below_consumption_is_fully_credited() -> None:
    result = calculate_financials(
        monthly_consumption_kwh=1150,
        annual_production_kwh=5_000.0,
        electricity_price_eur_per_kwh=PRICE,
        capex=make_capex(),
    )
    assert result.covered_energy_kwh == 5_000.0
    assert result.annual_savings_eur == Decimal("1250.00")


def test_production_above_consumption_is_capped() -> None:
    """No export compensation in the case methodology."""
    result = calculate_financials(
        monthly_consumption_kwh=1150,
        annual_production_kwh=25_000.0,
        electricity_price_eur_per_kwh=PRICE,
        capex=make_capex(),
    )
    assert result.covered_energy_kwh == 13_800.0
    assert result.coverage_percent == pytest.approx(100.0)
    assert result.annual_savings_eur == Decimal("3450.00")


def test_doubling_production_beyond_consumption_does_not_change_savings() -> None:
    a = calculate_financials(
        monthly_consumption_kwh=1150,
        annual_production_kwh=20_000.0,
        electricity_price_eur_per_kwh=PRICE,
        capex=make_capex(),
    )
    b = calculate_financials(
        monthly_consumption_kwh=1150,
        annual_production_kwh=40_000.0,
        electricity_price_eur_per_kwh=PRICE,
        capex=make_capex(),
    )
    assert a.annual_savings_eur == b.annual_savings_eur


# ---------------------------------------------------------------------------
# Degenerate inputs
# ---------------------------------------------------------------------------


def test_zero_production_gives_no_payback_rather_than_infinity() -> None:
    result = calculate_financials(
        monthly_consumption_kwh=1150,
        annual_production_kwh=0.0,
        electricity_price_eur_per_kwh=PRICE,
        capex=make_capex(),
    )
    assert result.annual_savings_eur == Decimal("0.00")
    assert result.simple_payback_years is None
    assert result.twenty_year_net_benefit_eur == Decimal("-8789.70")


def test_negative_production_is_rejected() -> None:
    with pytest.raises(ValueError):
        calculate_financials(
            monthly_consumption_kwh=1150,
            annual_production_kwh=-1.0,
            electricity_price_eur_per_kwh=PRICE,
            capex=make_capex(),
        )


def test_non_positive_price_is_rejected() -> None:
    with pytest.raises(ValueError):
        calculate_financials(
            monthly_consumption_kwh=1150,
            annual_production_kwh=1000.0,
            electricity_price_eur_per_kwh=Decimal("0"),
            capex=make_capex(),
        )


# ---------------------------------------------------------------------------
# Money handling
# ---------------------------------------------------------------------------


def test_all_money_values_are_decimal(case_result) -> None:
    assert isinstance(case_result.annual_savings_eur, Decimal)
    assert isinstance(case_result.twenty_year_net_benefit_eur, Decimal)
    for year in case_result.cash_flow:
        assert isinstance(year.cumulative_cash_flow_eur, Decimal)


def test_money_is_rounded_to_two_places(case_result) -> None:
    assert case_result.annual_savings_eur.as_tuple().exponent == -2
    for year in case_result.cash_flow:
        assert year.cumulative_cash_flow_eur.as_tuple().exponent == -2


def test_cumulative_flow_is_internally_consistent(case_result) -> None:
    """Each year advances by exactly one year of savings."""
    savings = case_result.annual_savings_eur
    for previous, current in zip(
        case_result.cash_flow[1:], case_result.cash_flow[2:], strict=False
    ):
        delta = current.cumulative_cash_flow_eur - previous.cumulative_cash_flow_eur
        assert delta == savings


def test_calculation_is_deterministic(case_result) -> None:
    again = calculate_financials(
        monthly_consumption_kwh=1150,
        annual_production_kwh=10_122.31,
        electricity_price_eur_per_kwh=PRICE,
        capex=make_capex(),
    )
    assert again.model_dump_json(exclude={"capex_conversion"}) == (
        case_result.model_dump_json(exclude={"capex_conversion"})
    )
