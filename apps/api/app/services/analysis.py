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
    DataSource,
    ExchangeRate,
    ExchangeRateSource,
    FacetYieldResult,
    FinancialResult,
    PanelLayout,
    RoofModel,
    YieldResult,
)
from app.integrations.exchange_rates import ExchangeRateService
from app.integrations.pvgis import (
    FacetProbe,
    PvgisClient,
    PvgisProbeSet,
    PvgisResult,
    classify_endpoint,
    probe_facets,
)
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
    #: The observations every figure above derives from. Carried so the
    #: snapshot can record where they came from, and so a later size change can
    #: reuse them instead of asking PVGIS the same four questions again.
    probes: PvgisProbeSet | None = None

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
        probes=probes,
    )


def serialise_provenance(probes: PvgisProbeSet | None) -> dict[str, Any] | None:
    """Where every production figure in this snapshot came from.

    Nested inside `energy` rather than added as a new top-level section, so
    everything that already takes `energy` whole - the PDF, the share payload,
    `recompute_for_consumption`, `validate_ready` - carries it along without
    knowing it exists. A snapshot issued before this block existed simply has
    `null` here, and still renders.

    Two angle conventions are named rather than one called "aspect": PVGIS
    counts from south, the compass from north, and a figure recorded under the
    wrong one is silently a different roof plane. `pvgisAspectDeg` is the
    request and reuse-equality key, so it is kept at full precision; the
    two-decimal human-facing copies live in `energy.facets[]`.

    The two timestamps are also distinguished. `batchCompletedAt` is when the
    four-probe batch finished; each probe carries its own `retrievedAt`.
    """
    if probes is None:
        return None

    return {
        "source": probes.data_source.value,
        "endpoint": probes.endpoint,
        "origin": probes.origin,
        "apiVersion": probes.api_version,
        "batchCompletedAt": probes.batch_completed_at.isoformat(),
        "radiationDatabase": single_radiation_database(probes),
        "request": {
            "lat": probes.request_params.get("lat"),
            "lon": probes.request_params.get("lon"),
            "peakPowerKwp": probes.request_params.get("peakpower"),
            "lossPercent": probes.request_params.get("loss"),
            "pvTechChoice": probes.request_params.get("pvtechchoice"),
            "mountingPlace": probes.request_params.get("mountingplace"),
        },
        "probes": [
            {
                "facetId": probe.facet_id,
                "compassAzimuthDeg": round(probe.compass_azimuth_deg, 6),
                "pvgisAspectDeg": round(probe.pvgis_aspect_deg, 6),
                "angleDeg": round(probe.angle_deg, 6),
                "specificYieldKwhPerKwp": round(probe.result.specific_yield_kwh_per_kwp, 4),
                "monthlySpecificYieldKwhPerKwp": [
                    round(v, 4) for v in probe.result.monthly_specific_yield_kwh_per_kwp
                ],
                "radiationDatabase": probe.result.radiation_database,
                "retrievedAt": probe.result.retrieved_at.isoformat(),
                "losses": probe.result.losses_percent,
            }
            for probe in probes.probes.values()
        ],
    }


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
            "pvgis": serialise_provenance(result.probes),
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


#: How close two angles must be to count as the same observation. Six decimal
#: degrees is under a tenth of a millimetre of arc - far tighter than any real
#: calibration change, and far looser than float noise.
ANGLE_TOLERANCE_DEG = 1e-6


def probe_set_from_snapshot(snapshot: dict[str, object]) -> PvgisProbeSet | None:
    """Rebuild the probe batch a snapshot already recorded.

    Mirrors `exchange_rate_from_snapshot`: an observation already made, carried
    forward rather than made again. Returns `None` for a snapshot written
    before provenance existed, which is then simply not reusable.
    """
    energy = _section(snapshot, "energy")
    block = energy.get("pvgis")
    if not isinstance(block, dict) or not block.get("probes"):
        return None

    request = block.get("request") or {}
    probes: dict[str, FacetProbe] = {}
    for entry in block["probes"]:
        monthly = [float(v) for v in entry["monthlySpecificYieldKwhPerKwp"]]
        specific = float(entry["specificYieldKwhPerKwp"])
        probes[str(entry["facetId"])] = FacetProbe(
            facet_id=str(entry["facetId"]),
            compass_azimuth_deg=float(entry["compassAzimuthDeg"]),
            pvgis_aspect_deg=float(entry["pvgisAspectDeg"]),
            angle_deg=float(entry["angleDeg"]),
            result=PvgisResult(
                annual_kwh=specific,
                monthly_kwh=list(monthly),
                specific_yield_kwh_per_kwp=specific,
                monthly_specific_yield_kwh_per_kwp=monthly,
                peak_power_kwp=1.0,
                aspect_deg=float(entry["pvgisAspectDeg"]),
                angle_deg=float(entry["angleDeg"]),
                radiation_database=str(entry["radiationDatabase"]),
                data_source=DataSource(str(block["source"])),
                retrieved_at=datetime.fromisoformat(str(entry["retrievedAt"])),
                losses_percent=entry.get("losses"),
            ),
        )

    return PvgisProbeSet(
        probes=probes,
        endpoint=str(block["endpoint"]),
        origin=str(block.get("origin") or ""),
        api_version=block.get("apiVersion"),
        batch_completed_at=datetime.fromisoformat(str(block["batchCompletedAt"])),
        data_source=DataSource(str(block["source"])),
        request_params={
            "lat": request.get("lat"),
            "lon": request.get("lon"),
            "peakpower": request.get("peakPowerKwp"),
            "loss": request.get("lossPercent"),
            "pvtechchoice": request.get("pvTechChoice"),
            "mountingplace": request.get("mountingPlace"),
        },
    )


def reusable_probes(
    snapshot: dict[str, object], roof: RoofModel, settings: Settings
) -> PvgisProbeSet | None:
    """The stored probes, if they still describe this roof and configuration.

    A system size change moves the layout and the production; it moves nothing
    about *where the yields came from*, because the probes are taken at 1 kWp
    before any size is chosen. So the same observation answers every size - as
    long as nothing that was sent to PVGIS has changed since.

    Returns `None` whenever reuse would be a guess, and the caller then makes a
    fresh batch. Every check below is a way the stored figures could be about
    something other than the question now being asked.
    """
    probes = probe_set_from_snapshot(snapshot)
    if probes is None:
        return None

    # Trust first. A replay observation is not proposal-grade, so carrying one
    # forward would launder it into a document. Permitted only where a test
    # environment has explicitly said so.
    if not probes.is_trusted and not settings.allow_replay_proposals:
        logger.info("stored PVGIS probes are %s, not reusable", probes.data_source.value)
        return None

    # Endpoint identity. A live observation from another origin or another API
    # version is a different service, not the same one on a new port. For a
    # permitted replay the port legitimately moves between runs, so only the
    # API version is compared.
    current = classify_endpoint(settings.pvgis_base_url)
    if probes.api_version != current.api_version:
        return None
    if probes.is_trusted and probes.origin != current.origin:
        return None

    request = probes.request_params
    if request.get("peakpower") != 1.0:
        return None
    if not _close(request.get("lat"), settings.case_resolved_latitude):
        return None
    if not _close(request.get("lon"), settings.case_resolved_longitude):
        return None
    if not _close(request.get("loss"), settings.pvgis_system_loss_percent):
        return None
    if request.get("pvtechchoice") != settings.pvgis_technology:
        return None
    if request.get("mountingplace") != settings.pvgis_mounting_place:
        return None

    # Every current facet must be covered, at the geometry it has now.
    for facet in roof.facets:
        probe = probes.probes.get(facet.id)
        if probe is None:
            return None
        if not _close(probe.angle_deg, facet.pitch_deg):
            return None
        if not _close(probe.pvgis_aspect_deg, facet.pvgis_aspect_deg):
            return None

    return probes


def _close(value: Any, expected: float) -> bool:
    if value is None:
        return False
    try:
        return abs(float(value) - expected) <= ANGLE_TOLERANCE_DEG
    except (TypeError, ValueError):
        return False


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

    # The stored observation answers every size, so a size change normally
    # costs no PVGIS traffic at all. `reusable_probes` returns None the moment
    # reuse would be a guess, and then a fresh batch is made.
    probes = probes or reusable_probes(snapshot, roof, settings)
    if probes is None:
        logger.info("stored PVGIS probes are not reusable; re-probing every facet")
        probes = await probe_facets(roof.facets, settings=settings, client=pvgis_client)

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
            probes=probes,
        )
    )
    # The rate block is carried across verbatim rather than re-serialised, so
    # `retrievedAt` keeps the instant of the original observation.
    return {**recomputed, "exchangeRate": snapshot["exchangeRate"]}
