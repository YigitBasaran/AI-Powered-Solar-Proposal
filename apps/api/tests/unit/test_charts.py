"""The SVG charts that go into the PDF.

A chart in a proposal is read once, on paper or on a phone, with no tooltip and
no way to ask it a question. So the assertions here are mostly about the
figures being *legible*: a bar chart shows shape well and value badly, and the
one thing a customer wants from the monthly chart is what a given month
produces.
"""

from __future__ import annotations

import re

import pytest

from app.services.charts import cash_flow_chart, monthly_production_chart

MONTHLY = [412.0, 520.5, 690.0, 800.4, 940.1, 1010.6, 1102.9, 1040.2, 870.7, 700.3, 500.8, 413.5]


def test_twelve_values_are_required() -> None:
    with pytest.raises(ValueError, match="12 values"):
        monthly_production_chart(MONTHLY[:11])


def test_every_month_prints_its_own_figure() -> None:
    """The gridlines only get you to "about 1,100"."""
    svg = monthly_production_chart(MONTHLY)

    for value in MONTHLY:
        assert f"{value:,.0f}" in svg, f"{value} is not readable on the chart"


def test_the_figures_are_not_clipped_by_the_viewbox() -> None:
    """The tallest bar's label sits above it, so the plot needs headroom.

    Asserted on geometry rather than by eye: every text y-coordinate has to
    fall inside the viewBox, and the label for the tallest month is the one
    that would escape it.
    """
    svg = monthly_production_chart(MONTHLY, height=260)

    ys = [float(match) for match in re.findall(r'<text[^>]*\sy="([\d.]+)"', svg)]
    assert ys, "no text found on the chart"
    assert min(ys) >= 0, "a label is above the top edge"
    assert max(ys) <= 260, "a label is below the bottom edge"


def test_the_month_names_are_still_there() -> None:
    svg = monthly_production_chart(MONTHLY)
    for month in ("Jan", "Jul", "Dec"):
        assert f">{month}<" in svg


def test_the_chart_names_itself_for_assistive_technology() -> None:
    svg = monthly_production_chart(MONTHLY)
    assert 'role="img"' in svg
    assert "aria-label=" in svg


def test_a_flat_month_does_not_break_the_scale() -> None:
    """All-zero production is a real state - a failed year, or a stub."""
    svg = monthly_production_chart([0.0] * 12)
    assert "<svg" in svg
    assert "nan" not in svg.lower()


def test_the_cash_flow_chart_still_renders() -> None:
    flow = [
        {"year": year, "cumulativeCashFlowEur": -10000 + year * 2400}
        for year in range(21)
    ]
    svg = cash_flow_chart(flow, payback_years=4.2)
    assert "<svg" in svg
    assert "nan" not in svg.lower()
