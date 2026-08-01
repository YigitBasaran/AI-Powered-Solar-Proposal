import { expect, test } from "./fixtures/proposal";
import {
  CASE_INPUTS,
  EXPECTED,
  EXPECTED_ROOF,
  INVARIANTS,
  expectedFor,
} from "./fixtures/expected-values";
import { expectCompactMoney, expectRounded, parseDisplayedNumber } from "./helpers/assertions";

/**
 * The three complete customer journeys.
 *
 * Chat -> analysis -> proposal, driven through the browser exactly as a
 * customer would, and checked against golden values that were derived by hand
 * from the committed fixtures.
 */

test.describe("@p0 end-to-end workflow", () => {
  test("6 kWp: the full journey produces the reviewed figures", async ({
    solarFlow,
    roofView,
    consoleCapture,
  }) => {
    const expected = EXPECTED["6"];

    await solarFlow.open();
    await solarFlow.enterLocation(CASE_INPUTS.locationInput);
    await solarFlow.enterMonthlyConsumption(CASE_INPUTS.monthlyConsumptionKwh);
    await solarFlow.selectSystemSize(6);

    await expect(solarFlow.kpi("system-size")).toHaveText(`${expected.actualCapacityKwp} kWp`);
    await expect(solarFlow.kpiNote("system-size")).toHaveText(
      `${expected.actualPanelCount} panels`,
    );
    await expectRounded(solarFlow.kpi("annual-production"), expected.annualProductionKwh, 0);
    await expectCompactMoney(solarFlow.kpi("annual-saving"), expected.annualSavingsEur);
    await expect(solarFlow.kpi("payback")).toHaveText(
      `${expected.paybackYears.toFixed(1)} yr`,
    );
    await expectCompactMoney(
      solarFlow.kpi("twenty-year-net"),
      expected.twentyYearNetBenefitEur,
    );

    // Panels land where production is highest, not where area is largest.
    const counts = await roofView.panelCounts();
    for (const [facetId, panels] of Object.entries(expected.panelsByFacet)) {
      expect(counts[facetId], `panels on ${facetId}`).toBe(panels);
    }
    // South is the largest facet and has the most free bays, yet it is filled
    // last and least: at -34° latitude it is the weakest producer.
    expect(counts["facet_s"], "south takes only the leftovers").toBe(
      Math.min(...Object.values(expected.panelsByFacet)),
    );

    // 15 panels still fit on a 21-panel roof, so no shortfall warning.
    await expect(solarFlow.capacityWarning).toBeHidden();
    expect(consoleCapture.significant()).toEqual([]);
  });

  test("3.6 kWp: chosen by typing rather than clicking a card", async ({ solarFlow, roofView }) => {
    const expected = EXPECTED["3.6"];

    await solarFlow.open();
    await solarFlow.enterLocation(CASE_INPUTS.locationInput);
    await solarFlow.enterMonthlyConsumption(CASE_INPUTS.monthlyConsumptionKwh);
    // A typed message and a card click send the same canonical phrase, so this
    // proves the two entry points do not drift apart.
    await solarFlow.selectSystemSize(3.6, "chat");

    await expect(solarFlow.kpi("system-size")).toHaveText("3.6 kWp");
    await expectRounded(solarFlow.kpi("annual-production"), expected.annualProductionKwh, 0);

    const counts = await roofView.panelCounts();
    for (const facetId of ["facet_n", "facet_s", "facet_e", "facet_w"]) {
      expect(counts[facetId], `panels on ${facetId}`).toBe(
        expected.panelsByFacet[facetId] ?? 0,
      );
    }
  });

  test("9.6 kWp: asks for more than the roof holds, and says so", async ({
    solarFlow,
    roofView,
  }) => {
    const expected = EXPECTED["9.6"];

    await solarFlow.open();
    await solarFlow.completeIntake({ size: 9.6 });

    // The KPI reports what will be installed, not what was asked for.
    await expect(solarFlow.kpi("system-size")).toHaveText(
      `${expected.actualCapacityKwp} kWp`,
    );
    await expectRounded(solarFlow.kpi("annual-production"), expected.annualProductionKwh, 0);

    const counts = await roofView.panelCounts();
    expect(Object.values(counts).reduce((a, b) => a + b, 0)).toBe(
      expected.actualPanelCount,
    );

    // 24 requested, 21 placed: the customer must be told, not quietly served a
    // smaller system priced as if it were the one they chose.
    await expect(solarFlow.capacityWarning).toBeVisible();
  });
});

test.describe("@p0 workflow invariants", () => {
  for (const size of [3.6, 6, 9.6] as const) {
    test(`${size} kWp satisfies the case invariants`, async ({ api }) => {
      const { analysis } = await api.analysedProject(`${size} kWp` as "6 kWp");
      const expected = expectedFor(size);

      // Each invariant restates a *rule*, not the calculation under test.
      expect(analysis.layout.placedPanelCount).toBe(expected.actualPanelCount);
      expect(analysis.layout.feasibleSystemSizeKwp).toBeCloseTo(
        INVARIANTS.capacityFromCount(analysis.layout.placedPanelCount),
        6,
      );
      expect(analysis.financial.coveragePercent).toBeLessThanOrEqual(
        INVARIANTS.maxCoveragePercent,
      );
      expect(Number(analysis.financial.annualSavingsEur)).toBeLessThanOrEqual(
        INVARIANTS.maxAnnualSavingsEur,
      );
      expect(analysis.financial.cashFlow).toHaveLength(INVARIANTS.cashFlowEntries);
      expect(Number(analysis.financial.cashFlow[0]!.cumulativeCashFlowEur)).toBeLessThan(0);
      expect(analysis.financial.cashFlow.at(-1)!.cumulativeCashFlowEur).toBe(
        analysis.financial.twentyYearNetBenefitEur,
      );
      // Savings are capped at consumption: the case grants no export payment.
      expect(analysis.financial.coveredEnergyKwh).toBeLessThanOrEqual(
        analysis.financial.annualConsumptionKwh,
      );
    });
  }

  test("the roof workspace layers can be toggled independently", async ({
    solarFlow,
    roofView,
  }) => {
    await solarFlow.open();
    await solarFlow.completeIntake();

    for (const layer of ["satellite", "facets", "edges", "measurements", "panels"] as const) {
      expect(await roofView.isLayerOn(layer)).toBe(true);
      expect(await roofView.toggleLayer(layer)).toBe(false);
      expect(await roofView.toggleLayer(layer)).toBe(true);
    }
    // The stage keeps rendering throughout.
    expect(await roofView.stageHasContent()).toBe(true);
  });

  test("selecting a facet shows its measurements", async ({ solarFlow, roofView, page }) => {
    await solarFlow.open();
    await solarFlow.completeIntake();

    await roofView.selectFacet("facet_n");
    const detail = page.getByText(/PVGIS aspect/);
    await expect(detail).toBeVisible();
    // Close to ±180 is a north-facing roof in PVGIS's south-is-zero convention.
    await expect(detail).toContainText(
      `${EXPECTED_ROOF.facetAspects.facet_n.toFixed(1)}°`,
    );
  });

  test("the KPI note reconciles with the coverage figure", async ({ solarFlow }) => {
    await solarFlow.open();
    await solarFlow.completeIntake({ size: 6 });

    const note = await solarFlow.kpiNote("annual-production").innerText();
    expect(parseDisplayedNumber(note)).toBeCloseTo(EXPECTED["6"].coveragePercent, 1);
  });
});
