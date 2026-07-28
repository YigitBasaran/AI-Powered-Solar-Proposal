"use client";

import { CashFlowChart, MonthlyProductionChart } from "@/components/energy/Charts";
import { Callout, Card, DataTable, Kpi, SectionTitle, SourceBadge } from "@/components/ui/primitives";
import { area, dataSourceLabel, degrees, eur, kwh, number, percent, usd, years } from "@/lib/format";
import type { Analysis } from "@/types/api";

export function KpiRow({ analysis }: { analysis: Analysis }) {
  const { layout, energy, financial } = analysis;
  const short = layout.placedPanelCount < layout.requestedPanelCount;

  return (
    <div data-testid="kpi-row" className="grid grid-cols-2 gap-2.5 lg:grid-cols-5">
      <Kpi
        testId="kpi-system-size"
        label="System size"
        value={`${layout.feasibleSystemSizeKwp} kWp`}
        note={
          short
            ? `${layout.placedPanelCount} of ${layout.requestedPanelCount} panels fit`
            : `${layout.placedPanelCount} panels`
        }
        emphasis
      />
      <Kpi
        testId="kpi-annual-production"
        label="Annual production"
        value={kwh(energy.totalAnnualProductionKwh)}
        note={`${percent(financial.coveragePercent)} of usage`}
      />
      <Kpi
        testId="kpi-annual-saving"
        label="Annual saving"
        value={eur(financial.annualSavingsEur, { compact: true })}
        note={`at €${financial.electricityPriceEurPerKwh}/kWh`}
      />
      <Kpi
        testId="kpi-payback"
        label="Payback"
        value={years(financial.simplePaybackYears)}
        note={`CAPEX ${eur(financial.convertedCapex.amount, { compact: true })}`}
      />
      <Kpi
        testId="kpi-twenty-year-net"
        label="20-year net"
        value={eur(financial.twentyYearNetBenefitEur, { compact: true })}
        note="cumulative, flat tariff"
      />
    </div>
  );
}

export function FxRow({ analysis }: { analysis: Analysis }) {
  const fx = analysis.exchangeRate;
  const source = dataSourceLabel(fx.retrievalSource);
  const financial = analysis.financial;

  return (
    <Card className="px-3.5 py-3" >
      <div data-testid="fx-row" className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-baseline gap-x-5 gap-y-1 text-[12.5px]">
          <span className="text-slate-muted">
            Capital cost as quoted{" "}
            <strong data-testid="fx-capex-usd" className="tnum font-semibold text-slate-ink">
              {usd(financial.originalCapex.amount)}
            </strong>
          </span>
          <span aria-hidden className="text-slate-rule">
            →
          </span>
          <span className="text-slate-muted">
            used in analysis{" "}
            <strong data-testid="fx-capex-eur" className="tnum font-semibold text-slate-ink">
              {eur(financial.convertedCapex.amount)}
            </strong>
          </span>
          <span className="text-slate-muted">
            1 {fx.baseCurrency} ={" "}
            <span data-testid="fx-rate" className="tnum font-medium text-slate-ink">
              {fx.rate}
            </span>{" "}
            {fx.quoteCurrency}
          </span>
          <span className="text-slate-muted">
            <span data-testid="fx-provider">{fx.dataProvider}</span> ·{" "}
            <span data-testid="fx-rate-date">{fx.rateDate}</span>
          </span>
        </div>
        <SourceBadge testId="fx-source-badge" tone={source.tone} label={source.label} />
      </div>
      {fx.isFixture ? (
        <p className="mt-2 text-[11.5px] text-[#8a5210]">
          This rate is demo fixture data, not a live market rate. USD and EUR are never
          treated as equal — the conversion above is always applied.
        </p>
      ) : null}
    </Card>
  );
}

export function EnergySection({ analysis }: { analysis: Analysis }) {
  const { energy, roof } = analysis;
  const labels = new Map(roof.facets.map((f) => [f.id, f.label]));
  const source = dataSourceLabel(energy.dataSource);

  return (
    <Card className="px-3.5 py-3">
      <SectionTitle
        action={
          <SourceBadge
            testId="pvgis-source-badge"
            tone={source.tone}
            label={`PVGIS · ${source.label}`}
          />
        }
      >
        Energy production
      </SectionTitle>

      <MonthlyProductionChart monthly={energy.totalMonthlyProductionKwh} />

      <DataTable
        label="Energy production by roof facet"
        className="mt-3"
        headers={[
          { label: "Facet" },
          { label: "Panels", align: "right" },
          { label: "Capacity", align: "right" },
          { label: "Aspect", align: "right" },
          { label: "Specific yield", align: "right" },
          { label: "Annual", align: "right" },
        ]}
      >
        {energy.facets.map((facet) => (
          <tr key={facet.facetId} className="border-b border-slate-line last:border-0">
            <td className="px-2 py-1.5">{labels.get(facet.facetId) ?? facet.facetId}</td>
            <td className="tnum px-2 py-1.5 text-right">{facet.panelCount}</td>
            <td className="tnum px-2 py-1.5 text-right">{facet.installedPowerKwp.toFixed(1)} kWp</td>
            <td className="tnum px-2 py-1.5 text-right">{degrees(facet.pvgisAspectDeg)}</td>
            <td className="tnum px-2 py-1.5 text-right">
              {number(facet.specificYieldKwhPerKwp)} kWh/kWp
            </td>
            <td className="tnum px-2 py-1.5 text-right font-medium">
              {kwh(facet.annualProductionKwh)}
            </td>
          </tr>
        ))}
        <tr className="font-semibold">
          <td className="px-2 py-1.5">Total</td>
          <td className="tnum px-2 py-1.5 text-right">
            {energy.facets.reduce((sum, f) => sum + f.panelCount, 0)}
          </td>
          <td className="tnum px-2 py-1.5 text-right">{energy.installedPowerKwp.toFixed(1)} kWp</td>
          <td />
          <td />
          <td className="tnum px-2 py-1.5 text-right">{kwh(energy.totalAnnualProductionKwh)}</td>
        </tr>
      </DataTable>

      <p className="mt-2 text-[11px] text-slate-muted">
        One PVGIS request per occupied facet, using that facet&apos;s own pitch and azimuth.
        {energy.radiationDatabase ? ` Radiation database: ${energy.radiationDatabase}.` : ""}
      </p>
    </Card>
  );
}

export function FinancialSection({ analysis }: { analysis: Analysis }) {
  const { financial } = analysis;

  return (
    <Card className="px-3.5 py-3">
      <SectionTitle>Twenty-year cash flow</SectionTitle>
      <CashFlowChart
        cashFlow={financial.cashFlow}
        paybackYears={financial.simplePaybackYears}
      />

      <div className="mt-3 grid gap-x-8 gap-y-1 text-[12.5px] sm:grid-cols-2">
        <Row label="Annual production" value={kwh(financial.annualProductionKwh)} />
        <Row label="Annual consumption" value={kwh(financial.annualConsumptionKwh)} />
        <Row label="Energy covered" value={kwh(financial.coveredEnergyKwh)} />
        <Row label="Coverage" value={percent(financial.coveragePercent)} />
        <Row label="Annual saving" value={eur(financial.annualSavingsEur)} />
        <Row label="Simple payback" value={years(financial.simplePaybackYears)} />
        <Row label="Capital cost (as quoted)" value={usd(financial.originalCapex.amount)} />
        <Row label="Capital cost (converted)" value={eur(financial.convertedCapex.amount)} />
      </div>

      <p className="mt-2 text-[11px] text-slate-muted">
        Savings are capped at consumption: the case grants no export compensation. Flat
        tariff, no degradation, inflation, maintenance or financing cost.
      </p>
    </Card>
  );
}

export function RoofSection({ analysis }: { analysis: Analysis }) {
  const { roof, layout } = analysis;
  const panels = new Map(layout.facets.map((f) => [f.facetId, f.panelCount]));

  return (
    <Card className="px-3.5 py-3">
      <SectionTitle
        action={
          <span className="text-[11.5px] text-slate-muted">
            {area(roof.totalProjectedAreaM2)} plan · {area(roof.totalSlopedAreaM2)} sloped
          </span>
        }
      >
        Roof measurements
      </SectionTitle>

      <DataTable
        label="Roof facet measurements"
        headers={[
          { label: "Facet" },
          { label: "Azimuth", align: "right" },
          { label: "Plan area", align: "right" },
          { label: "Sloped area", align: "right" },
          { label: "Panels", align: "right" },
        ]}
      >
        {roof.facets.map((facet) => (
          <tr key={facet.id} className="border-b border-slate-line last:border-0">
            <td className="px-2 py-1.5">{facet.label}</td>
            <td className="tnum px-2 py-1.5 text-right">{degrees(facet.compassAzimuthDeg)}</td>
            <td className="tnum px-2 py-1.5 text-right">{area(facet.projectedAreaM2)}</td>
            <td className="tnum px-2 py-1.5 text-right">{area(facet.slopedAreaM2)}</td>
            <td className="tnum px-2 py-1.5 text-right">{panels.get(facet.id) ?? 0}</td>
          </tr>
        ))}
      </DataTable>

      <SectionTitle>
        <span className="mt-3 block">Edge measurements</span>
      </SectionTitle>
      <DataTable
        label="Roof edge measurements"
        headers={[
          { label: "Edge" },
          { label: "Type" },
          { label: "Plan length", align: "right" },
          { label: "True length", align: "right" },
        ]}
      >
        {roof.edges.map((edge) => (
          <tr key={edge.id} className="border-b border-slate-line last:border-0">
            <td className="px-2 py-1.5">{edge.id}</td>
            <td className="px-2 py-1.5 capitalize text-slate-body">{edge.type}</td>
            <td className="tnum px-2 py-1.5 text-right">{edge.projectedLengthM.toFixed(3)} m</td>
            <td className="tnum px-2 py-1.5 text-right">
              {edge.true3dLengthM !== null ? `${edge.true3dLengthM.toFixed(3)} m` : "—"}
            </td>
          </tr>
        ))}
      </DataTable>

      <p className="mt-2 text-[11px] text-slate-muted">
        Hip lengths use true 3-D endpoint geometry. They are deliberately not the plan
        length divided by cos(pitch), which applies only to an edge running straight up
        the slope.
      </p>
    </Card>
  );
}

export function CapacityWarning({ warning }: { warning: string | null }) {
  if (!warning) return null;
  return (
    <Callout testId="capacity-warning" title="Capacity note.">
      {warning}
    </Callout>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-4 border-b border-slate-line py-1 last:border-0">
      <span className="text-slate-muted">{label}</span>
      <span className="tnum font-medium text-slate-ink">{value}</span>
    </div>
  );
}
