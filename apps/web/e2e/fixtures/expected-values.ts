/**
 * Golden expected values for deterministic fixture mode.
 *
 * ## These are reviewed literals, not computed values
 *
 * Nothing in this file is derived at test time. The suite must never:
 *
 *  - import a production function to build an expectation,
 *  - read the current analysis response and assert it equals itself,
 *  - recompute a figure using the same formula the backend uses.
 *
 * Any of those would make the test agree with the implementation *by
 * construction* — it would pass just as happily if the implementation were
 * wrong.
 *
 * ## How these numbers were established
 *
 * Every figure below was worked out by hand from the **committed fixtures**
 * and the **case methodology as written in the brief**, before being compared
 * with the application. The derivation is reproduced in
 * `docs/testing.md#golden-value-derivation` so a reviewer can repeat it with a
 * calculator. Where the hand derivation and the application disagreed, the
 * hand derivation won and the file records it.
 *
 * Underlying committed fixtures:
 *   fixtures/pvgis/pvcalc*.json                     (PVGIS 5.3 PVcalc, PVGIS-SARAH3)
 *   fixtures/exchange-rates/usd-eur-ecb.json        (ECB via Frankfurter, 2026-07-24)
 *   apps/api/app/data/google_roof_calibration.json  (hip roof, 25°, one chimney)
 *
 * Re-derived on 2026-08-01, when the operator corrected `v_corner_a` and
 * `v_ridge_0` against the raster and the chimney on the north facet was
 * modelled. Both change output: the footprint is no longer a rectangle, and the
 * largest offered system no longer fits on the roof.
 *
 * Changing application output must require a visible, reviewable edit here.
 */

export type ExpectedProposal = {
  requestedSystemKwp: number;
  requestedPanelCount: number;
  actualPanelCount: number;
  actualCapacityKwp: number;
  annualConsumptionKwh: number;
  /** API precision: the unrounded total, rounded to 2 dp. */
  annualProductionKwh: number;
  coveragePercent: number;
  annualSavingsEur: string;
  capexUsd: string;
  fxRate: string;
  fxRateDate: string;
  capexEur: string;
  /** API precision: full float. Displayed to 1 dp. */
  paybackYears: number;
  twentyYearNetBenefitEur: string;
  /** facet id -> panels placed. An empty facet is a deliberate result. */
  panelsByFacet: Record<string, number>;
  /** True when the request exceeds what the roof can physically take. */
  capacityWarning: boolean;
};

/** The case brief's fixed inputs. */
export const CASE_INPUTS = {
  monthlyConsumptionKwh: 1150,
  annualConsumptionKwh: 13800,
  electricityPriceEurPerKwh: "0.25",
  capexUsd: "10000.00",
  panelWatts: 400,
  panelKwp: 0.4,
  pitchDeg: 25,
  /** The brief prints a positive latitude; that point is open sea. */
  rawLatitude: 34.04658242871865,
  resolvedLatitude: -34.04658242871865,
  longitude: 18.46491476666948,
  locationInput: "-34.04658242871865, 18.46491476666948",
} as const;

/** Committed FX fixture — one rate, one date, for every deterministic run. */
export const EXPECTED_FX = {
  rate: "0.87897",
  rateDate: "2026-07-24",
  provider: "ECB",
  sourceApi: "Frankfurter",
  baseCurrency: "USD",
  quoteCurrency: "EUR",
  retrievalSource: "fixture",
} as const;

/**
 * Per-facet 1 kWp annual yield, read straight off the committed PVcalc
 * captures (`outputs.totals.fixed.E_y`).
 *
 * The ordering is the whole point: at −34° latitude the **north**-facing
 * facet is the strongest and the south-facing one is the weakest, by 50%.
 * Any suite written for the northern hemisphere would have these reversed.
 */
export const EXPECTED_FACET_YIELD = {
  facet_n: 1678.77,
  facet_w: 1503.89,
  facet_e: 1367.24,
  facet_s: 1114.85,
} as const;

/** Roof geometry, recomputed by hand from the committed calibration. */
export const EXPECTED_ROOF = {
  facetCount: 4,
  eaveCount: 4,
  hipCount: 4,
  ridgeCount: 1,
  totalProjectedAreaM2: 79.69,
  totalSlopedAreaM2: 87.93,
  /** The chimney on the north facet, in plan. No panel may stand on it. */
  totalObstructedAreaM2: 2.99,
  obstructionCount: 1,
  sourceWidthPx: 1280,
  sourceHeightPx: 1280,
  groundMetresPerSourcePixel: 0.06185,
  /**
   * The footprint is a general quadrilateral, not a rectangle: the operator
   * moved `v_corner_a`, so opposite eaves no longer match. All four are listed
   * because no single "long" and "short" is true any more.
   */
  eaveLengthsM: { eave_0: 11.36, eave_1: 7.143, eave_2: 11.216, eave_3: 6.979 },
  ridgeLengthM: 4.374,
  /**
   * A-GEO-1. A hip does not run up the slope, so its true length is *not* the
   * plan length divided by cos(pitch). Taking `hip_2`, which the correction did
   * not touch:
   *
   *   plan            5.051 m
   *   true 3-D        5.312 m   ( √(5.051² + 1.653²), ridge 1.653 m above eave )
   *   naive /cos(25°) 5.573 m   ← wrong by 26 cm, and wrong in the same
   *                               direction for every hip on the roof
   *
   * The four hips are no longer equal: `hip_0` and `hip_1` run to the moved
   * ridge end and are shorter.
   */
  hipProjectedLengthM: 5.051,
  hipTrue3dLengthM: 5.312,
  hipNaiveWrongLengthM: 5.573,
  /** Compass bearing each facet faces. PVGIS aspect = compass − 180. */
  facetAzimuths: { facet_n: 10.53, facet_e: 100.62, facet_s: 187.59, facet_w: 278.04 },
  facetAspects: { facet_n: -169.47, facet_e: -79.38, facet_s: 7.59, facet_w: 98.04 },
  /**
   * Panels each facet can physically hold (1 × 2 m, 2 cm gap, on the slope).
   *
   * North holds 6, not 9: the chimney sits inside a row and costs three bays
   * for 2.99 m² of area. East holds 4, not 3, because its array is turned 45°
   * — the only facet where rotating beats lying parallel to the eave. Total
   * capacity is 22, still below the 24 the largest offer needs.
   */
  facetCapacity: { facet_n: 6, facet_s: 9, facet_w: 3, facet_e: 4 },
  totalCapacityPanels: 22,
  /** Array rotation chosen per facet, degrees from that facet's eave. */
  facetArrayRotationDeg: { facet_n: 0, facet_s: 0, facet_w: 0, facet_e: 45 },
  radiationDatabase: "PVGIS-SARAH3",
} as const;

export const EXPECTED: Record<"3.6" | "6" | "9.6", ExpectedProposal> = {
  "3.6": {
    requestedSystemKwp: 3.6,
    requestedPanelCount: 9,
    actualPanelCount: 9,
    actualCapacityKwp: 3.6,
    annualConsumptionKwh: 13800,
    // 2.4 × 1678.77 + 1.2 × 1503.89 = 5833.716 -> 5833.72
    annualProductionKwh: 5833.72,
    // 5833.716 / 13800 × 100
    coveragePercent: 42.27,
    // round(5833.716 × 0.25) to cents
    annualSavingsEur: "1458.43",
    capexUsd: "10000.00",
    fxRate: "0.87897",
    fxRateDate: "2026-07-24",
    // round(10000 × 0.87897) to cents
    capexEur: "8789.70",
    // 8789.70 / 1458.43
    paybackYears: 6.026823364851244,
    // −8789.70 + 20 × 1458.43
    twentyYearNetBenefitEur: "20378.90",
    // The smallest system no longer fits on north alone: the chimney leaves it
    // 6 bays, so the last 3 go to west - the next-best yield, not the next
    // biggest facet.
    panelsByFacet: { facet_n: 6, facet_w: 3 },
    capacityWarning: false,
  },

  "6": {
    requestedSystemKwp: 6,
    requestedPanelCount: 15,
    actualPanelCount: 15,
    actualCapacityKwp: 6,
    annualConsumptionKwh: 13800,
    // 2.4 × 1678.77 + 1.2 × 1503.89 + 1.6 × 1367.24 + 0.8 × 1114.85 = 8913.180
    annualProductionKwh: 8913.18,
    coveragePercent: 64.59,
    annualSavingsEur: "2228.30",
    capexUsd: "10000.00",
    fxRate: "0.87897",
    fxRateDate: "2026-07-24",
    capexEur: "8789.70",
    paybackYears: 3.9445765830453707,
    twentyYearNetBenefitEur: "35776.30",
    // Still the load-bearing expectation of the suite, and now a harder one.
    // South is the LARGEST facet (31.0 m² to north's 30.0) and holds 9 bays to
    // north's 6, so an allocator ranking by area or by capacity would fill it
    // first. It gets 2 panels — the leftovers — because at −34° latitude north
    // (1679), west (1504) and east (1367) all out-produce it (1115), and east
    // now takes 4 rather than 3 because its array is turned 45°.
    panelsByFacet: { facet_n: 6, facet_w: 3, facet_e: 4, facet_s: 2 },
    capacityWarning: false,
  },

  "9.6": {
    requestedSystemKwp: 9.6,
    requestedPanelCount: 24,
    // The roof holds 22. This is the case the capacity warning exists for, and
    // every figure below is computed from the 8.8 kWp that fits, never the
    // 9.6 kWp that was asked for.
    actualPanelCount: 22,
    actualCapacityKwp: 8.8,
    annualConsumptionKwh: 13800,
    // 2.4 × 1678.77 + 1.2 × 1503.89 + 1.6 × 1367.24 + 3.6 × 1114.85 = 12034.760
    annualProductionKwh: 12034.76,
    coveragePercent: 87.21,
    annualSavingsEur: "3008.69",
    capexUsd: "10000.00",
    fxRate: "0.87897",
    fxRateDate: "2026-07-24",
    capexEur: "8789.70",
    paybackYears: 2.9214375691746244,
    twentyYearNetBenefitEur: "51384.10",
    // Every facet full: 6 + 9 + 3 + 4 = 22, the whole roof.
    panelsByFacet: { facet_n: 6, facet_s: 9, facet_w: 3, facet_e: 4 },
    capacityWarning: true,
  },
};

/**
 * Invariants that are safe to compute, because each restates a *rule* rather
 * than re-running the calculation under test.
 */
export const INVARIANTS = {
  capacityFromCount: (panelCount: number) =>
    Number((panelCount * CASE_INPUTS.panelKwp).toFixed(6)),
  maxCoveragePercent: 100,
  analysisYears: 20,
  cashFlowEntries: 21,
  /** Savings can never exceed what the household would have paid. */
  maxAnnualSavingsEur:
    CASE_INPUTS.annualConsumptionKwh * Number(CASE_INPUTS.electricityPriceEurPerKwh),
} as const;

export function expectedFor(size: 3.6 | 6 | 9.6): ExpectedProposal {
  return EXPECTED[String(size) as "3.6" | "6" | "9.6"];
}

export const SIZES = [3.6, 6, 9.6] as const;
