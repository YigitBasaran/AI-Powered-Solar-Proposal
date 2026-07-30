import { expect, test } from "./fixtures/proposal";

/**
 * The project tariff, through the browser.
 *
 * The backend suite proves the value is stored, used and frozen. This proves
 * the two things only a browser can: that the shared proposal page shows the
 * price the customer actually set rather than the configured default, and that
 * the saving beside it is the one derived from that price.
 *
 * A tariff is the cheapest input to get subtly wrong, because nothing about it
 * looks broken — a payback quoted at the wrong price is still a plausible
 * number in a well-formatted card.
 */

const CASE_COORD = "-34.04658242871865, 18.46491476666948";
const TARIFF = 0.31;

test.describe("@p1 project tariff", () => {
  test("the shared proposal shows the customer's tariff, not the case default", async ({
    api,
    page,
  }) => {
    const { projectId } = await api.createProject();
    await api.chat(projectId, CASE_COORD);
    await api.chat(projectId, "1150 kWh");
    await api.chat(projectId, "6 kWp");
    await api.chat(projectId, `My tariff is actually ${TARIFF} EUR/kWh`);
    await api.runAnalysis(projectId);

    const finalised = await api.finalize(projectId);
    expect(finalised.status).toBe(200);
    const shareToken = finalised.body.shareToken as string;
    const proposal = (await api.proposal(shareToken)).body;

    // The stored figure first, so a UI assertion cannot pass against the wrong value.
    expect(Number(proposal.financial.electricityPriceEurPerKwh)).toBeCloseTo(TARIFF, 4);

    await page.goto(`/proposal/${shareToken}`);
    await expect(page.getByTestId("proposal-title")).toBeVisible();

    // The saving's own note names the price it was computed at, so the figure
    // and the rate it came from are checked together rather than separately.
    await expect(page.getByTestId("kpi-annual-saving")).toBeVisible();
    await expect(page.getByTestId("kpi-annual-saving-note")).toContainText(`${TARIFF}`);

    // And the derived figure really is derived: covered energy times the tariff.
    const expectedSaving =
      Number(proposal.financial.coveredEnergyKwh) * TARIFF;
    expect(Number(proposal.financial.annualSavingsEur)).toBeCloseTo(expectedSaving, 0);
  });

  test("a later tariff change leaves the issued proposal alone", async ({ api, page }) => {
    const { projectId } = await api.createProject();
    await api.chat(projectId, CASE_COORD);
    await api.chat(projectId, "1150 kWh");
    await api.chat(projectId, "6 kWp");
    await api.chat(projectId, `My tariff is actually ${TARIFF} EUR/kWh`);
    await api.runAnalysis(projectId);

    const finalised = await api.finalize(projectId);
    expect(finalised.status).toBe(200);
    const shareToken = finalised.body.shareToken as string;
    const issued = (await api.proposal(shareToken)).body;

    await api.chat(projectId, "Change my tariff to 0.45 EUR/kWh");

    const after = (await api.proposal(shareToken)).body;
    expect(after.financial).toEqual(issued.financial);

    // And the page a customer already has open still renders the issued figures.
    await page.goto(`/proposal/${shareToken}`);
    await expect(page.getByTestId("kpi-annual-saving-note")).toContainText(`${TARIFF}`);
  });
});
