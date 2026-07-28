import { expect, test } from "./fixtures/proposal";
import { CASE_INPUTS, EXPECTED, INVARIANTS, expectedFor } from "./fixtures/expected-values";
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
    expect(counts["facet_s"], "south stays empty at -34° latitude").toBe(0);

    // The system size fits exactly, so no shortfall warning is shown.
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
    expect(counts["facet_n"]).toBe(9);
    expect(counts["facet_s"]).toBe(0);
    expect(counts["facet_e"]).toBe(0);
    expect(counts["facet_w"]).toBe(0);
  });

  test("9.6 kWp: fills the roof exactly", async ({ solarFlow, roofView }) => {
    const expected = EXPECTED["9.6"];

    await solarFlow.open();
    await solarFlow.completeIntake({ size: 9.6 });

    await expect(solarFlow.kpi("system-size")).toHaveText("9.6 kWp");
    await expectRounded(solarFlow.kpi("annual-production"), expected.annualProductionKwh, 0);

    const counts = await roofView.panelCounts();
    expect(Object.values(counts).reduce((a, b) => a + b, 0)).toBe(24);
    await expect(solarFlow.capacityWarning).toBeHidden();
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
    // -169.4° is a north-facing roof in PVGIS's south-is-zero convention.
    await expect(detail).toContainText("-169.4°");
  });

  test("the KPI note reconciles with the coverage figure", async ({ solarFlow }) => {
    await solarFlow.open();
    await solarFlow.completeIntake({ size: 6 });

    const note = await solarFlow.kpiNote("annual-production").innerText();
    expect(parseDisplayedNumber(note)).toBeCloseTo(EXPECTED["6"].coveragePercent, 1);
  });
});
