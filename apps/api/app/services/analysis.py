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
from decimal import Decimal

from app.core.config import Settings, get_settings
from app.domain.models import (
    CapexConversion,
    DataSource,
    ExchangeRate,
    FacetYieldResult,
    FinancialResult,
    PanelLayout,
    RoofModel,
    YieldResult,
)
from app.integrations.exchange_rates import ExchangeRateService
from app.integrations.pvgis import PvgisClient, PvgisFacetYieldRankingProvider
from app.services.financial import calculate_financials, convert_capex
from app.services.layout import assert_layout_valid, generate_layout
from app.services.roof import build_roof_model, roof_summary
from app.services.yield_ranking import (
    FacetYieldRankingProvider,
    FixtureFacetYieldRankingProvider,
)

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


async def compute_yield(
    roof: RoofModel,
    layout: PanelLayout,
    client: PvgisClient,
    settings: Settings,
) -> YieldResult:
    """Facet-level PVGIS for every facet that received panels."""
    facet_results: list[FacetYieldResult] = []
    monthly_total = [0.0] * 12
    annual_total = 0.0
    radiation_db: str | None = None
    sources: set[DataSource] = set()

    for facet_layout in layout.facet_layouts:
        facet = roof.facet(facet_layout.facet_id)
        installed_kwp = facet_layout.panel_count * settings.panel_power_kwp

        result = await client.pvcalc(
            lat=settings.case_resolved_latitude,
            lon=settings.case_resolved_longitude,
            peak_power_kwp=installed_kwp,
            angle_deg=facet.pitch_deg,
            aspect_deg=facet.pvgis_aspect_deg,
        )

        facet_results.append(
            FacetYieldResult(
                facet_id=facet.id,
                panel_count=facet_layout.panel_count,
                installed_power_kwp=installed_kwp,
                pitch_deg=facet.pitch_deg,
                compass_azimuth_deg=facet.compass_azimuth_deg,
                pvgis_aspect_deg=facet.pvgis_aspect_deg,
                annual_production_kwh=result.annual_kwh,
                specific_yield_kwh_per_kwp=result.specific_yield_kwh_per_kwp,
                monthly_production_kwh=result.monthly_kwh,
                data_source=result.data_source,
            )
        )
        annual_total += result.annual_kwh
        monthly_total = [a + b for a, b in zip(monthly_total, result.monthly_kwh, strict=True)]
        radiation_db = result.radiation_database
        sources.add(result.data_source)

    # If any facet fell back, the aggregate must report the weaker provenance
    # rather than the best one.
    for candidate in (
        DataSource.LIVE_FALLBACK_FIXTURE,
        DataSource.FIXTURE,
        DataSource.LIVE_FALLBACK_CACHE,
        DataSource.CACHE,
        DataSource.LIVE,
    ):
        if candidate in sources:
            aggregate_source = candidate
            break
    else:
        aggregate_source = DataSource.LIVE

    return YieldResult(
        facet_results=facet_results,
        total_annual_production_kwh=annual_total,
        total_monthly_production_kwh=monthly_total,
        installed_power_kwp=layout.feasible_system_size_kwp,
        data_source=aggregate_source,
        radiation_database=radiation_db,
    )


async def run_analysis(
    *,
    monthly_consumption_kwh: float,
    system_size_kwp: float,
    settings: Settings | None = None,
    pvgis_client: PvgisClient | None = None,
    fx_service: ExchangeRateService | None = None,
    ranking_provider: FacetYieldRankingProvider | None = None,
) -> AnalysisResult:
    settings = settings or get_settings()
    client = pvgis_client or PvgisClient(settings)
    fx = fx_service or ExchangeRateService(settings)

    roof = build_roof_model(settings)

    # Ranking uses live PVGIS when available and the captured table otherwise;
    # either way it is the same port the optimiser was written against.
    provider = ranking_provider or (
        PvgisFacetYieldRankingProvider(client, settings=settings)
        if settings.pvgis_mode.value == "live"
        else FixtureFacetYieldRankingProvider()
    )

    layout = await generate_layout(roof, system_size_kwp, provider, settings)
    assert_layout_valid(roof, layout, settings)

    yield_result = await compute_yield(roof, layout, client, settings)

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
        "financial": {
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
        },
    }
