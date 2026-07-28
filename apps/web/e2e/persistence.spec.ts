import { expect, test } from "./fixtures/proposal";
import { CASE_INPUTS, EXPECTED_FX } from "./fixtures/expected-values";

/**
 * What survives a reload, and what is fetched twice.
 *
 * The workspace deliberately starts a *new* project on every load — there is no
 * resume, and the case does not ask for one. What must survive is the thing a
 * customer was given a link to.
 */

test.describe("@p1 persistence and caching", () => {
  test("a shared proposal outlives the session that made it", async ({
    solarFlow,
    proposalPage,
    page,
  }) => {
    await solarFlow.open();
    await solarFlow.completeIntake({ size: 6 });
    const { shareToken } = await solarFlow.finalizeProposal();

    // Wipe every trace of the session and come back cold.
    await page.context().clearCookies();
    await page.evaluate(() => {
      localStorage.clear();
      sessionStorage.clear();
    });

    await proposalPage.open(shareToken);
    await expect(proposalPage.kpi("system-size")).toHaveText("6 kWp");
    await expect(proposalPage.fx("rate")).toHaveText(EXPECTED_FX.rate);
  });

  test("reloading the workspace starts a fresh project, and says so", async ({
    solarFlow,
    api,
  }) => {
    await solarFlow.open();
    await solarFlow.enterLocation(CASE_INPUTS.locationInput);
    await expect(solarFlow.assistantMessages).toHaveCount(2);

    await solarFlow.page.reload();
    await solarFlow.open();

    // Back at the beginning: one welcome message, no answers carried over.
    await expect(solarFlow.assistantMessages).toHaveCount(1);
    await expect(solarFlow.userMessages).toHaveCount(0);
    void api;
  });

  test("the exchange rate is fetched once and reused within a run", async ({ api }) => {
    // Two analyses, one rate. The point is not speed — it is that both
    // proposals cite the same rate and the same date, so two customers quoted
    // minutes apart are quoted consistently.
    const [first, second] = await Promise.all([
      api.analysedProject("3.6 kWp"),
      api.analysedProject("9.6 kWp"),
    ]);

    expect(second.analysis.exchangeRate.rate).toBe(first.analysis.exchangeRate.rate);
    expect(second.analysis.exchangeRate.rateDate).toBe(first.analysis.exchangeRate.rateDate);
    expect(second.analysis.exchangeRate.retrievalSource).toBe(
      first.analysis.exchangeRate.retrievalSource,
    );
  });

  test("PVGIS results are reused across projects with the same geometry", async ({ api }) => {
    const first = await api.analysedProject("6 kWp");
    const started = Date.now();
    const second = await api.analysedProject("6 kWp");
    const elapsed = Date.now() - started;

    // Same facets, same aspects, same answers.
    expect(second.analysis.energy.totalAnnualProductionKwh).toBe(
      first.analysis.energy.totalAnnualProductionKwh,
    );
    for (const [index, facet] of second.analysis.energy.facets.entries()) {
      expect(facet.specificYieldKwhPerKwp).toBe(
        first.analysis.energy.facets[index]!.specificYieldKwhPerKwp,
      );
    }
    // A generous ceiling: this only fails if the second run is doing real work
    // it should have cached.
    expect(elapsed, `second analysis took ${elapsed}ms`).toBeLessThan(15_000);
  });

  test("a proposal keeps its own snapshot when the project is analysed again", async ({
    api,
    request,
  }) => {
    const { projectId } = await api.analysedProject("3.6 kWp");
    const finalised = await api.finalize(projectId);
    const token = finalised.body.shareToken as string;
    const before = (await api.proposal(token)).body;

    // Re-run the analysis on the same project.
    const rerun = await request.post(`/api/v1/projects/${projectId}/run-analysis`);
    expect(rerun.status()).toBe(200);

    const after = (await api.proposal(token)).body;
    expect(after.layout).toEqual(before.layout);
    expect(after.financial).toEqual(before.financial);
    expect(after.energy).toEqual(before.energy);
  });
});
