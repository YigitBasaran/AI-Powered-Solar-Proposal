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
 *   fixtures/pvgis/pvcalc*.json                    (PVGIS 5.3 PVcalc, PVGIS-SARAH3)
 *   fixtures/exchange-rates/usd-eur-ecb.json       (ECB via Frankfurter, 2026-07-24)
 *   apps/api/app/data/fixed_roof_calibration.json  (11.216 × 7.143 m hip roof, 25°)
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
  facet_n: 1678.66,
  facet_w: 1515.28,
  facet_e: 1367.24,
  facet_s: 1119.82,
} as const;

/** Roof geometry, recomputed by hand from the committed calibration. */
export const EXPECTED_ROOF = {
  facetCount: 4,
  eaveCount: 4,
  hipCount: 4,
  ridgeCount: 1,
  totalProjectedAreaM2: 80.12,
  totalSlopedAreaM2: 88.4,
  sourceWidthPx: 1280,
  sourceHeightPx: 1280,
  groundMetresPerSourcePixel: 0.06185,
  footprintLongM: 11.216,
  footprintShortM: 7.143,
  ridgeLengthM: 4.073,
  /**
   * A-GEO-1. A hip does not run up the slope, so its true length is *not* the
   * plan length divided by cos(pitch):
   *
   *   plan            5.051 m
   *   true 3-D        5.319 m   ( √(5.051² + 1.665²), ridge 1.665 m above eave )
   *   naive /cos(25°) 5.573 m   ← wrong by 25 cm, and wrong in the same
   *                               direction for every hip on the roof
   */
  hipProjectedLengthM: 5.051,
  hipTrue3dLengthM: 5.319,
  hipNaiveWrongLengthM: 5.573,
  /** Compass bearing each facet faces. PVGIS aspect = compass − 180. */
  facetAzimuths: { facet_n: 10.62, facet_e: 100.62, facet_s: 190.63, facet_w: 280.62 },
  facetAspects: { facet_n: -169.38, facet_e: -79.38, facet_s: 10.63, facet_w: 100.62 },
  /** Panels each facet can physically hold (1 × 2 m, 2 cm gap, on the slope). */
  facetCapacity: { facet_n: 9, facet_s: 9, facet_w: 3, facet_e: 3 },
  radiationDatabase: "PVGIS-SARAH3",
} as const;

export const EXPECTED: Record<"3.6" | "6" | "9.6", ExpectedProposal> = {
  "3.6": {
    requestedSystemKwp: 3.6,
    requestedPanelCount: 9,
    actualPanelCount: 9,
    actualCapacityKwp: 3.6,
    annualConsumptionKwh: 13800,
    // 9 × 0.4 kWp × 1678.66 = 6043.176 -> 6043.18
    annualProductionKwh: 6043.18,
    // 6043.176 / 13800 × 100
    coveragePercent: 43.79,
    // round(6043.176 × 0.25) to cents
    annualSavingsEur: "1510.79",
    capexUsd: "10000.00",
    fxRate: "0.87897",
    fxRateDate: "2026-07-24",
    // round(10000 × 0.87897) to cents
    capexEur: "8789.70",
    // 8789.70 / 1510.79
    paybackYears: 5.817949549573402,
    // −8789.70 + 20 × 1510.79
    twentyYearNetBenefitEur: "21426.10",
    // The whole system fits on the single best-producing facet.
    panelsByFacet: { facet_n: 9 },
  },

  "6": {
    requestedSystemKwp: 6,
    requestedPanelCount: 15,
    actualPanelCount: 15,
    actualCapacityKwp: 6,
    annualConsumptionKwh: 13800,
    // 3.6 × 1678.66 + 1.2 × 1515.28 + 1.2 × 1367.24 = 9502.20
    annualProductionKwh: 9502.2,
    coveragePercent: 68.86,
    annualSavingsEur: "2375.55",
    capexUsd: "10000.00",
    fxRate: "0.87897",
    fxRateDate: "2026-07-24",
    capexEur: "8789.70",
    paybackYears: 3.7000694575992927,
    twentyYearNetBenefitEur: "38721.30",
    // The load-bearing expectation of the whole suite: north and south are the
    // SAME SIZE and each hold 9 panels, yet the remaining 6 go on the two small
    // east/west triangles and south stays EMPTY — because at −34° latitude west
    // (1515) and east (1367) out-produce south (1120). An allocator ranking by
    // area would fill south and this assertion would catch it.
    panelsByFacet: { facet_n: 9, facet_w: 3, facet_e: 3 },
  },

  "9.6": {
    requestedSystemKwp: 9.6,
    requestedPanelCount: 24,
    actualPanelCount: 24,
    actualCapacityKwp: 9.6,
    annualConsumptionKwh: 13800,
    // 3.6 × 1678.66 + 3.6 × 1119.82 + 1.2 × 1515.28 + 1.2 × 1367.24 = 13533.552
    annualProductionKwh: 13533.55,
    coveragePercent: 98.07,
    annualSavingsEur: "3383.39",
    capexUsd: "10000.00",
    fxRate: "0.87897",
    fxRateDate: "2026-07-24",
    capexEur: "8789.70",
    paybackYears: 2.597897375117855,
    twentyYearNetBenefitEur: "58878.10",
    // Exactly fills the roof: capacity is 24 and 24 are requested.
    panelsByFacet: { facet_n: 9, facet_s: 9, facet_w: 3, facet_e: 3 },
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
