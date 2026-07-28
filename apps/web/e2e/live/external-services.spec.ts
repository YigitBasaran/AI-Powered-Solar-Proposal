import { expect, test } from "../fixtures/proposal";
import { SKIP } from "../fixtures/environment";
import { EXPECTED_ROOF } from "../fixtures/expected-values";

/**
 * Tier C — opt-in, against the real internet.
 *
 * Run with `npx playwright test --grep @live` on a stack configured for live
 * PVGIS and FX. Against the default deterministic stack these skip themselves
 * with a reason, because a `@live` test that quietly passes on fixtures is
 * worse than no test at all.
 *
 * Live values move, so these assert **invariants and ranges**, never exact
 * numbers. Exact figures belong to the fixture tier, where they are stable.
 */

test.describe("@live external services", () => {
  test("live PVGIS returns plausible yields, north-favoured", async ({ api, stack }) => {
    test.skip(stack.pvgis !== "live", SKIP.notLive("PVGIS", stack.pvgis));

    const { analysis } = await api.analysedProject("9.6 kWp");
    expect(analysis.energy.dataSource).toBe("live");

    const byId = new Map(analysis.energy.facets.map((f) => [f.facetId, f]));
    const north = byId.get("facet_n")!;
    const south = byId.get("facet_s")!;

    // Cape Town, 25° pitch: a well-oriented kWp yields roughly 1,300–1,900 kWh
    // a year. Anything outside that is a unit or aspect error, not weather.
    expect(north.specificYieldKwhPerKwp).toBeGreaterThan(1300);
    expect(north.specificYieldKwhPerKwp).toBeLessThan(1900);

    // The load-bearing invariant of the whole southern-hemisphere story.
    expect(
      north.specificYieldKwhPerKwp,
      "north must out-produce south at -34° latitude",
    ).toBeGreaterThan(south.specificYieldKwhPerKwp);
    expect(north.specificYieldKwhPerKwp / south.specificYieldKwhPerKwp).toBeGreaterThan(1.2);

    // Southern-hemisphere seasonality: December beats June, comfortably.
    const december = north.monthlyProductionKwh[11]!;
    const june = north.monthlyProductionKwh[5]!;
    expect(december).toBeGreaterThan(june * 1.5);

    // Sanity anchor only — the all-north figure from the brief's reference.
    expect(analysis.energy.totalAnnualProductionKwh).toBeGreaterThan(9_000);
    expect(analysis.energy.totalAnnualProductionKwh).toBeLessThan(20_000);
  });

  test("live FX returns a real rate, never parity", async ({ api, stack }) => {
    test.skip(stack.fx !== "live", SKIP.notLive("FX", stack.fx));

    const { analysis } = await api.analysedProject("6 kWp");
    const fx = analysis.exchangeRate;

    expect(fx.isLive).toBe(true);
    expect(fx.isFixture).toBe(false);
    expect(fx.baseCurrency).toBe("USD");
    expect(fx.quoteCurrency).toBe("EUR");

    // USD/EUR has stayed within this band for decades. A rate outside it means
    // an inverted quote or a wrong pair, not a market event.
    const rate = Number(fx.rate);
    expect(rate).toBeGreaterThan(0.5);
    expect(rate).toBeLessThan(1.5);
    expect(rate).not.toBe(1);

    // ISO date, and not from the future.
    expect(fx.rateDate).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(new Date(fx.rateDate).getTime()).toBeLessThanOrEqual(Date.now());

    // The conversion is genuinely applied.
    const usd = Number(analysis.financial.originalCapex.amount);
    const eur = Number(analysis.financial.convertedCapex.amount);
    expect(eur).toBeCloseTo(usd * rate, 0);
  });

  test("live imagery keeps the calibration valid", async ({ api, stack }) => {
    test.skip(stack.maps !== "live", SKIP.notLive("Maps", stack.maps));

    const config = await api.mapConfig();
    // Calibration is stored in source-map pixels, so it only carries over if
    // the live raster is on the same grid the fixture was drawn on.
    expect(config.zoom).toBe(20);
    expect(config.scale).toBe(2);
    expect(config.sourceWidthPx).toBe(EXPECTED_ROOF.sourceWidthPx);

    const roof = await api.roofModel();
    expect(roof.totalProjectedAreaM2).toBeCloseTo(EXPECTED_ROOF.totalProjectedAreaM2, 2);
  });
});
