"""Orchestrates the deterministic analysis pipeline.

Roof -> layout -> facet-level PVGIS -> FX -> financials, in that order, because
each step genuinely needs the previous one: PVGIS is called with the capacity
that actually fits, and the financial model is driven by the production that
capacity actually yields.

Where the requested system does not fit, the *feasible* capacity flows onward.
Nothing downstream ever sees the requested figure as though it were installed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.core.config import Settings, get_settings
from app.core.errors import (
    PvgisInconsistentProvenanceError,
    PvgisUnavailableError,
)
from app.domain.models import (
    CapexConversion,
    ExchangeRate,
    ExchangeRateSource,
    FacetYieldResult,
    FinancialResult,
    PanelLayout,
    RoofModel,
    YieldResult,
)
from app.integrations.exchange_rates import ExchangeRateService
from app.integrations.pvgis import PvgisClient, PvgisProbeSet, probe_facets
from app.services.financial import calculate_financials, convert_capex
from app.services.layout import assert_layout_valid, generate_layout
from app.services.roof import build_roof_model, roof_summary

logger = logging.getLogger("solarvis.analysis")


@dataclass
class AnalysisResult:
    roof: RoofModel
    layout: PanelLayout
    yield_result: YieldResult
    exchange_rate: ExchangeRate
    capex: CapexConversion
    financial: FinancialResult

    @property
    def capacity_warning(self) -> str | None:
        return self.layout.capacity_warning


def compute_yield(
    layout: PanelLayout,
    probes: PvgisProbeSet,
    settings: Settings,
) -> YieldResult:
    """Production for a layout, from observations already made.

    No I/O. Every facet was probed at 1 kWp before the layout existed - the
    optimiser needed all of them to rank - so production here is
    `installed kWp x specific yield`, arithmetic over the *same* observation
    the ranking used. Two calls at two different powers could disagree; one
    call and a multiplication cannot.
    """
    facet_results: list[FacetYieldResult] = []
    monthly_total = [0.0] * 12
    annual_total = 0.0

    for facet_layout in layout.facet_layouts:
        probe = probes.probes.get(facet_layout.facet_id)
        if probe is None:
            # The layout placed panels on a facet that was never probed. That
            # is a bug in the caller, not a facet worth zero.
            raise PvgisUnavailableError(
                f"no PVGIS probe for {facet_layout.facet_id}, which the layout uses"
            )

        installed_kwp = facet_layout.panel_count * settings.panel_power_kwp
        specific = probe.result.specific_yield_kwh_per_kwp
        monthly = [m * installed_kwp for m in probe.result.monthly_specific_yield_kwh_per_kwp]
        annual = installed_kwp * specific

        facet_results.append(
            FacetYieldResult(
                facet_id=probe.facet_id,
                panel_count=facet_layout.panel_count,
                installed_power_kwp=installed_kwp,
                pitch_deg=probe.angle_deg,
                compass_azimuth_deg=probe.compass_azimuth_deg,
                pvgis_aspect_deg=probe.pvgis_aspect_deg,
                annual_production_kwh=annual,
                specific_yield_kwh_per_kwp=specific,
                monthly_production_kwh=monthly,
                data_source=probe.result.data_source,
            )
        )
        annual_total += annual
        monthly_total = [a + b for a, b in zip(monthly_total, monthly, strict=True)]

    return YieldResult(
        facet_results=facet_results,
        total_annual_production_kwh=annual_total,
        total_monthly_production_kwh=monthly_total,
        installed_power_kwp=layout.feasible_system_size_kwp,
        data_source=probes.data_source,
        radiation_database=single_radiation_database(probes),
    )


def single_radiation_database(probes: PvgisProbeSet) -> str:
    """The one dataset every probe came from.

    The optimiser compares the four specific yields directly, so a yield drawn
    from a different dataset is not comparable with the others - ranking on it
    would silently change which roof planes get panels. Disagreement is
    therefore a failure of the batch, not something to average or pick from.
    """
    databases = probes.radiation_databases
    if len(databases) == 1:
        return next(iter(databases))

    detail = {fid: p.result.radiation_database for fid, p in probes.probes.items()}
    logger.error("PVGIS returned mixed radiation databases: %s", detail)
    raise PvgisInconsistentProvenanceError(
        "PVGIS answered from more than one radiation database, so the facet yields "
        f"are not comparable and no layout can be optimised: {detail}",
        details={"radiationDatabases": detail},
    )


async def run_analysis(
    *,
    monthly_consumption_kwh: float,
    system_size_kwp: float,
    settings: Settings | None = None,
    pvgis_client: PvgisClient | None = None,
    fx_service: ExchangeRateService | None = None,
    probes: PvgisProbeSet | None = None,
) -> AnalysisResult:
    settings = settings or get_settings()
    fx = fx_service or ExchangeRateService(settings)

    roof = build_roof_model(settings)

    # Every facet, once, at 1 kWp - concurrently, on one connection pool. This
    # has to happen before the layout exists, because the optimiser ranks the
    # facets against each other.
    probes = probes or await probe_facets(roof.facets, settings=settings, client=pvgis_client)

    layout = generate_layout(roof, system_size_kwp, probes.yields(), settings)
    assert_layout_valid(roof, layout, settings)

    yield_result = compute_yield(layout, probes, settings)

    rate = await fx.get_usd_to_eur_rate()
    capex = convert_capex(
        amount=Decimal(str(settings.case_capex_amount)),
        from_currency=settings.case_capex_currency,
        exchange_rate=rate,
    )

    financial = calculate_financials(
        monthly_consumption_kwh=monthly_consumption_kwh,
        annual_production_kwh=yield_result.total_annual_production_kwh,
        electricity_price_eur_per_kwh=Decimal(str(settings.case_electricity_price)),
        capex=capex,
    )

    logger.info(
        "analysis | %.1f kWp requested, %.1f kWp feasible, %.0f kWh/yr, "
        "payback %.2f yr, fx %s (%s)",
        system_size_kwp,
        layout.feasible_system_size_kwp,
        yield_result.total_annual_production_kwh,
        financial.simple_payback_years or -1.0,
        rate.rate,
        rate.retrieval_source.value,
    )

    return AnalysisResult(
        roof=roof,
        layout=layout,
        yield_result=yield_result,
        exchange_rate=rate,
        capex=capex,
        financial=financial,
    )


def serialise_analysis(result: AnalysisResult) -> dict[str, object]:
    """The snapshot payload.

    This is what gets persisted on a proposal and what both the share page and
    the PDF renderer read. Neither recomputes anything, which is what makes a
    finalised proposal reproducible.
    """
    fin = result.financial
    rate = result.exchange_rate

    return {
        "roof": {
            **roof_summary(result.roof),
            "facetGeometry": [
                {
                    "id": f.id,
                    "sourcePixelPolygon": [
                        {"x": round(p.x, 2), "y": round(p.y, 2)} for p in f.source_pixel_polygon
                    ],
                }
                for f in result.roof.facets
            ],
        },
        "layout": {
            "requestedSystemSizeKwp": result.layout.requested_system_size_kwp,
            "requestedPanelCount": result.layout.requested_panel_count,
            "placedPanelCount": result.layout.placed_panel_count,
            "feasibleSystemSizeKwp": result.layout.feasible_system_size_kwp,
            "capacityWarning": result.layout.capacity_warning,
            "facets": [
                {
                    "facetId": fl.facet_id,
                    "orientation": fl.orientation.value,
                    "panelCount": fl.panel_count,
                    "panels": [
                        {
                            "id": p.id,
                            "sourcePixelPolygon": [
                                {"x": round(pt.x, 2), "y": round(pt.y, 2)}
                                for pt in p.source_pixel_polygon
                            ],
                        }
                        for p in fl.panels
                    ],
                }
                for fl in result.layout.facet_layouts
            ],
        },
        "energy": {
            "totalAnnualProductionKwh": round(result.yield_result.total_annual_production_kwh, 2),
            "totalMonthlyProductionKwh": [
                round(v, 2) for v in result.yield_result.total_monthly_production_kwh
            ],
            "installedPowerKwp": result.yield_result.installed_power_kwp,
            "dataSource": result.yield_result.data_source.value,
            "radiationDatabase": result.yield_result.radiation_database,
            "facets": [
                {
                    "facetId": f.facet_id,
                    "panelCount": f.panel_count,
                    "installedPowerKwp": f.installed_power_kwp,
                    "pitchDeg": f.pitch_deg,
                    "compassAzimuthDeg": round(f.compass_azimuth_deg, 2),
                    "pvgisAspectDeg": round(f.pvgis_aspect_deg, 2),
                    "annualProductionKwh": round(f.annual_production_kwh, 2),
                    "specificYieldKwhPerKwp": round(f.specific_yield_kwh_per_kwp, 2),
                    "monthlyProductionKwh": [round(v, 2) for v in f.monthly_production_kwh],
                    "dataSource": f.data_source.value,
                }
                for f in result.yield_result.facet_results
            ],
        },
        "exchangeRate": {
            "rate": str(rate.rate),
            "rateDate": rate.rate_date.isoformat(),
            "baseCurrency": rate.base_currency,
            "quoteCurrency": rate.quote_currency,
            "sourceApi": rate.source_api,
            "dataProvider": rate.data_provider,
            "retrievalSource": rate.retrieval_source.value,
            "isLive": rate.retrieval_source.is_live,
            "isFixture": rate.retrieval_source.is_fixture,
            "retrievedAt": rate.retrieved_at.isoformat(),
        },
        "financial": serialise_financial(fin),
    }


def serialise_financial(fin: FinancialResult) -> dict[str, object]:
    """The `financial` block on its own.

    Shared with `recompute_for_consumption`, which rebuilds this section and
    nothing else - if the two produced different shapes, a recalculated
    proposal would quietly stop matching a freshly analysed one.
    """
    return {
        "annualConsumptionKwh": fin.annual_consumption_kwh,
        "annualProductionKwh": round(fin.annual_production_kwh, 2),
        "coveredEnergyKwh": round(fin.covered_energy_kwh, 2),
        "coveragePercent": round(fin.coverage_percent, 2),
        "electricityPriceEurPerKwh": str(fin.electricity_price_eur_per_kwh),
        "annualSavingsEur": str(fin.annual_savings_eur),
        "originalCapex": {
            "amount": str(fin.capex_conversion.original_amount),
            "currency": fin.capex_conversion.original_currency,
        },
        "convertedCapex": {
            "amount": str(fin.capex_conversion.converted_amount),
            "currency": fin.capex_conversion.converted_currency,
        },
        "simplePaybackYears": fin.simple_payback_years,
        "twentyYearNetBenefitEur": str(fin.twenty_year_net_benefit_eur),
        "cashFlow": [
            {
                "year": y.year,
                "annualSavingsEur": str(y.annual_savings_eur),
                "cumulativeCashFlowEur": str(y.cumulative_cash_flow_eur),
            }
            for y in fin.cash_flow
        ],
    }


# ---------------------------------------------------------------------------
# Dependency-aware recomputation
# ---------------------------------------------------------------------------
#
# `run_analysis` is for the *first* analysis. When a customer corrects a value
# afterwards, re-running the whole pipeline would rebuild things that could not
# have changed - and, worse, would re-fetch the exchange rate, moving the
# figure they were quoted out from under them mid-conversation. That is the
# same failure the immutable-proposal design exists to prevent.
#
# So each input gets a path that touches only what depends on it. What is
# preserved is *proved* preserved: `tests/unit/test_corrections.py` compares
# the untouched sections byte for byte.


def _section(snapshot: dict[str, object], name: str) -> dict[str, Any]:
    """One top-level snapshot section, typed for reading.

    Snapshots are `dict[str, object]` because that is what `serialise_analysis`
    produces; the sections inside are known to be mappings.
    """
    block = snapshot.get(name)
    if not isinstance(block, dict):
        raise ValueError(f"snapshot is missing the {name!r} section")
    return block


def exchange_rate_from_snapshot(snapshot: dict[str, object]) -> ExchangeRate:
    """Rebuild the rate observation a snapshot already recorded.

    Re-reading it from the provider would be a different observation. This is
    the one the customer was shown, so it is the one that carries forward.
    """
    block = _section(snapshot, "exchangeRate")
    return ExchangeRate(
        source_api=str(block["sourceApi"]),
        data_provider=str(block["dataProvider"]),
        rate_date=date.fromisoformat(str(block["rateDate"])),
        base_currency=str(block["baseCurrency"]),
        quote_currency=str(block["quoteCurrency"]),
        rate=Decimal(str(block["rate"])),
        retrieval_source=ExchangeRateSource(str(block["retrievalSource"])),
        retrieved_at=datetime.fromisoformat(str(block["retrievedAt"])),
    )


def _capex_from(rate: ExchangeRate, settings: Settings) -> CapexConversion:
    return convert_capex(
        amount=Decimal(str(settings.case_capex_amount)),
        from_currency=settings.case_capex_currency,
        exchange_rate=rate,
    )


def recompute_for_consumption(
    *,
    snapshot: dict[str, object],
    monthly_consumption_kwh: float,
    settings: Settings | None = None,
) -> dict[str, object]:
    """A new snapshot for a changed consumption. Financial only.

    No PVGIS request, no layout run, no exchange-rate lookup. The roof, the
    panel layout and the modelled production do not depend on how much
    electricity the household uses, so they are carried across untouched.
    """
    settings = settings or get_settings()
    rate = exchange_rate_from_snapshot(snapshot)
    energy = _section(snapshot, "energy")

    financial = calculate_financials(
        monthly_consumption_kwh=monthly_consumption_kwh,
        annual_production_kwh=float(energy["totalAnnualProductionKwh"]),
        electricity_price_eur_per_kwh=Decimal(str(settings.case_electricity_price)),
        capex=_capex_from(rate, settings),
    )

    return {**snapshot, "financial": serialise_financial(financial)}


async def recompute_for_system_size(
    *,
    snapshot: dict[str, object],
    system_size_kwp: float,
    monthly_consumption_kwh: float,
    settings: Settings | None = None,
    pvgis_client: PvgisClient | None = None,
    probes: PvgisProbeSet | None = None,
) -> dict[str, object]:
    """A new snapshot for a changed system size. Layout, yield and financial.

    The roof is rebuilt from the same fixed calibration - deterministic, so it
    lands byte-identical - and the exchange rate is the observation already
    recorded rather than a fresh one.
    """
    settings = settings or get_settings()
    roof = build_roof_model(settings)

    probes = probes or await probe_facets(roof.facets, settings=settings, client=pvgis_client)

    layout = generate_layout(roof, system_size_kwp, probes.yields(), settings)
    assert_layout_valid(roof, layout, settings)
    yield_result = compute_yield(layout, probes, settings)

    rate = exchange_rate_from_snapshot(snapshot)
    capex = _capex_from(rate, settings)
    financial = calculate_financials(
        monthly_consumption_kwh=monthly_consumption_kwh,
        annual_production_kwh=yield_result.total_annual_production_kwh,
        electricity_price_eur_per_kwh=Decimal(str(settings.case_electricity_price)),
        capex=capex,
    )

    recomputed = serialise_analysis(
        AnalysisResult(
            roof=roof,
            layout=layout,
            yield_result=yield_result,
            exchange_rate=rate,
            capex=capex,
            financial=financial,
        )
    )
    # The rate block is carried across verbatim rather than re-serialised, so
    # `retrievedAt` keeps the instant of the original observation.
    return {**recomputed, "exchangeRate": snapshot["exchangeRate"]}
