import { expect, test } from "./fixtures/proposal";
import { CASE_INPUTS, EXPECTED, EXPECTED_FACET_YIELD, EXPECTED_FX, EXPECTED_ROOF } from "./fixtures/expected-values";
import { expectMoney, expectSameMoneyString } from "./helpers/assertions";

/**
 * Energy modelling and currency conversion.
 *
 * Two things the brief is emphatic about, and which a superficial test would
 * miss: production must be modelled per facet with that facet's own aspect,
 * and USD/EUR parity must be impossible — not merely absent from the default
 * configuration.
 */

test.describe("@p0 PVGIS energy", () => {
  test("one call per occupied facet, each with its own aspect and yield", async ({ api }) => {
    const { analysis } = await api.analysedProject("6 kWp");

    // Only occupied facets are modelled; an empty facet produces nothing and
    // should not cost a request. Since the chimney took three bays off north,
    // 6 kWp reaches all four facets - so the assertion is derived from where
    // panels actually landed rather than naming three facets outright.
    const modelled = analysis.energy.facets.map((f) => f.facetId).sort();
    const occupied = analysis.layout.facets
      .filter((f) => f.panelCount > 0)
      .map((f) => f.facetId)
      .sort();
    expect(modelled).toEqual(occupied);

    for (const facet of analysis.energy.facets) {
      const yieldValue = EXPECTED_FACET_YIELD[facet.facetId as keyof typeof EXPECTED_FACET_YIELD];
      expect(facet.specificYieldKwhPerKwp, `${facet.facetId} yield`).toBeCloseTo(yieldValue, 2);
      expect(facet.pvgisAspectDeg).toBeCloseTo(
        EXPECTED_ROOF.facetAspects[facet.facetId as keyof typeof EXPECTED_ROOF.facetAspects],
        2,
      );
      // production = capacity x specific yield, to the API's 2 dp rounding.
      expect(facet.annualProductionKwh).toBeCloseTo(facet.installedPowerKwp * yieldValue, 1);
      expect(facet.monthlyProductionKwh).toHaveLength(12);
    }

    expect(analysis.energy.totalAnnualProductionKwh).toBeCloseTo(
      EXPECTED["6"].annualProductionKwh,
      2,
    );
    expect(analysis.energy.radiationDatabase).toBe(EXPECTED_ROOF.radiationDatabase);
  });

  test("the southern hemisphere is modelled, not assumed away", async ({ api }) => {
    const { analysis } = await api.analysedProject("9.6 kWp");
    const byId = new Map(analysis.energy.facets.map((f) => [f.facetId, f]));

    const north = byId.get("facet_n")!;
    const south = byId.get("facet_s")!;

    // Same pitch, opposite aspect. At -34° latitude the north-facing facet must
    // out-produce the south-facing one by roughly half again — a
    // northern-hemisphere model would have this the other way round.
    //
    // Capacity is no longer equal: the chimney costs north three bays, so south
    // now carries MORE installed power than north and still produces less. That
    // makes the yield comparison a stronger statement than it was, not a weaker
    // one, so it is asserted per kWp rather than on totals.
    expect(south.installedPowerKwp).toBeGreaterThan(north.installedPowerKwp);
    expect(north.specificYieldKwhPerKwp).toBeGreaterThan(south.specificYieldKwhPerKwp);
    expect(north.specificYieldKwhPerKwp / south.specificYieldKwhPerKwp).toBeGreaterThan(1.4);
  });

  test("provenance is labelled everywhere it is shown", async ({ solarFlow, page }) => {
    await solarFlow.open();
    await solarFlow.completeIntake();

    // PVGIS is a real HTTP call now, answered here by the local replay stub.
    // "Replayed capture" is a distinct label from both "Live" and "Demo
    // fixture", so a replayed figure can never read as a live observation -
    // and this assertion is what would catch the stack quietly falling back to
    // reading captures off disk.
    await expect(page.getByTestId("pvgis-source-badge")).toHaveAttribute("data-tone", "replay");
    await expect(page.getByTestId("pvgis-source-badge")).toContainText("Replayed capture");
    await expect(page.getByTestId("imagery-source-badge")).toHaveAttribute("data-tone", "fixture");
    await expect(solarFlow.fx("source-badge")).toHaveAttribute("data-tone", "fixture");
    // The label is text, not colour alone.
    await expect(solarFlow.fx("source-badge")).toContainText("Demo fixture");
  });
});

test.describe("@p0 currency conversion", () => {
  test("the stored rate is applied, and displayed with its provenance", async ({ solarFlow }) => {
    await solarFlow.open();
    await solarFlow.completeIntake();

    await expect(solarFlow.fx("rate")).toHaveText(EXPECTED_FX.rate);
    await expect(solarFlow.fx("rate-date")).toHaveText(EXPECTED_FX.rateDate);
    await expect(solarFlow.fx("provider")).toHaveText(EXPECTED_FX.provider);
    await expectMoney(solarFlow.fx("capex-usd"), CASE_INPUTS.capexUsd);
    await expectMoney(solarFlow.fx("capex-eur"), EXPECTED["6"].capexEur);
  });

  test("USD and EUR are never treated as equal", async ({ api }) => {
    const { analysis } = await api.analysedProject("6 kWp");
    const fx = analysis.exchangeRate;

    expect(fx.rate).toBe(EXPECTED_FX.rate);
    expect(Number(fx.rate)).not.toBe(1);
    expect(fx.baseCurrency).toBe("USD");
    expect(fx.quoteCurrency).toBe("EUR");
    expect(fx.isFixture).toBe(true);
    expect(fx.isLive).toBe(false);

    // The converted figure is genuinely different from the quoted one.
    const usd = Number(analysis.financial.originalCapex.amount);
    const eur = Number(analysis.financial.convertedCapex.amount);
    expect(eur).not.toBeCloseTo(usd, 2);
    expectSameMoneyString(
      analysis.financial.convertedCapex.amount,
      EXPECTED["6"].capexEur,
      "CAPEX conversion",
    );
    expect(analysis.financial.originalCapex.currency).toBe("USD");
    expect(analysis.financial.convertedCapex.currency).toBe("EUR");
  });

  test("payback compares euro with euro", async ({ api }) => {
    const { analysis } = await api.analysedProject("6 kWp");
    const fin = analysis.financial;

    // payback = converted CAPEX / annual savings, both in EUR. Using the
    // unconverted USD figure would give 4.21 years instead of 3.70.
    const expected = Number(fin.convertedCapex.amount) / Number(fin.annualSavingsEur);
    expect(fin.simplePaybackYears!).toBeCloseTo(expected, 6);
    expect(fin.simplePaybackYears!).toBeCloseTo(EXPECTED["6"].paybackYears, 6);

    const wrong = Number(fin.originalCapex.amount) / Number(fin.annualSavingsEur);
    expect(fin.simplePaybackYears!).not.toBeCloseTo(wrong, 2);
  });

  test("the cash-flow table reconciles exactly with the printed annual saving", async ({
    api,
  }) => {
    const { analysis } = await api.analysedProject("6 kWp");
    const { cashFlow, annualSavingsEur, convertedCapex } = analysis.financial;

    expectSameMoneyString(cashFlow[0]!.cumulativeCashFlowEur, `-${convertedCapex.amount}`);

    // Every year must add exactly the printed saving. Carrying full precision
    // and rounding per row instead makes consecutive rows differ by a cent, so
    // a customer adding up the table cannot reconcile it.
    for (let year = 1; year <= 20; year += 1) {
      const row = cashFlow[year]!;
      expect(row.year).toBe(year);
      expect(row.annualSavingsEur).toBe(annualSavingsEur);
      const expected =
        Math.round(Number(convertedCapex.amount) * -100) +
        year * Math.round(Number(annualSavingsEur) * 100);
      expect(Math.round(Number(row.cumulativeCashFlowEur) * 100)).toBe(expected);
    }
  });
});
