import { expect, test } from "./fixtures/proposal";
import { EXPECTED, EXPECTED_FX } from "./fixtures/expected-values";
import { expectCompactMoney, expectMoney } from "./helpers/assertions";

/**
 * Creating and sharing a proposal.
 *
 * The share page is public and read-only. It must work for someone who has
 * never visited the workspace — a different browser context, no cookies, no
 * local storage — and it must show the same numbers, because both read one
 * immutable snapshot rather than recalculating.
 */

test.describe("@p0 proposal creation and sharing", () => {
  /** Figures that must be identical wherever they appear. */
  const SHARED_FIGURE_TESTIDS = [
    "kpi-system-size",
    "kpi-annual-production",
    "kpi-annual-saving",
    "kpi-payback",
    "kpi-twenty-year-net",
    "fx-capex-usd",
    "fx-capex-eur",
    "fx-rate",
    "fx-rate-date",
  ] as const;

  test("finalising produces a working link with identical figures", async ({
    solarFlow,
    page,
    browser,
  }) => {
    await solarFlow.open();
    await solarFlow.completeIntake({ size: 6 });

    const workspace: Record<string, string> = {};
    for (const testId of SHARED_FIGURE_TESTIDS) {
      workspace[testId] = (await page.getByTestId(testId).innerText()).trim();
    }

    const { shareToken, shareUrl } = await solarFlow.finalizeProposal();
    expect(shareToken).toMatch(/^[A-Za-z0-9_-]{16,}$/);
    expect(shareUrl).toContain(shareToken);

    // A brand-new context: no cookies, no storage, nothing carried over. This
    // is the recipient's experience, not the author's — and the link followed
    // is the absolute one the backend actually hands out.
    const freshContext = await browser.newContext();
    const freshPage = await freshContext.newPage();
    try {
      await freshPage.goto(shareUrl);
      await expect(freshPage.getByTestId("proposal-title")).toBeVisible({ timeout: 30_000 });

      for (const testId of SHARED_FIGURE_TESTIDS) {
        await expect(
          freshPage.getByTestId(testId),
          `${testId} differs between the workspace and the share page`,
        ).toHaveText(workspace[testId]!);
      }
    } finally {
      await freshContext.close();
    }
  });

  test("the share page shows the reviewed golden figures", async ({ api, proposalPage }) => {
    const { shareToken } = await api.finalisedProposal("6 kWp");
    const expected = EXPECTED["6"];

    await proposalPage.open(shareToken);

    await expect(proposalPage.kpi("system-size")).toHaveText(`${expected.actualCapacityKwp} kWp`);
    await expect(proposalPage.fx("rate")).toHaveText(EXPECTED_FX.rate);
    await expect(proposalPage.fx("rate-date")).toHaveText(EXPECTED_FX.rateDate);
    await expectMoney(proposalPage.fx("capex-eur"), expected.capexEur);
    await expectCompactMoney(proposalPage.kpi("annual-saving"), expected.annualSavingsEur);
    await expectCompactMoney(
      proposalPage.kpi("twenty-year-net"),
      expected.twentyYearNetBenefitEur,
    );
    await expect(proposalPage.pdfLink).toHaveAttribute(
      "href",
      `/api/v1/proposals/${shareToken}/pdf`,
    );
  });

  test("an unknown token is refused, not half-rendered", async ({ proposalPage, api }) => {
    const response = await api.proposal("this-token-does-not-exist");
    expect(response.status).toBe(404);
    expect(response.body.error.code).toBe("NOT_FOUND");

    await proposalPage.openExpectingFailure("this-token-does-not-exist");
    await expect(proposalPage.kpiRow).toHaveCount(0);
    await expect(proposalPage.notFound).toContainText("not found");
  });

  test("opening the proposal is counted", async ({ api, proposalPage }) => {
    const { shareToken } = await api.finalisedProposal("3.6 kWp");

    const before = await api.proposal(shareToken);
    expect(before.body.views.viewCount).toBe(0);

    await proposalPage.open(shareToken);
    // The page records the view itself, so poll the API rather than the badge:
    // the badge renders the count from *before* the increment.
    await expect
      .poll(async () => (await api.proposal(shareToken)).body.views.viewCount, {
        timeout: 15_000,
      })
      .toBeGreaterThanOrEqual(1);
  });

  test("finalising before an analysis exists is refused", async ({ api }) => {
    const { projectId } = await api.createProject();

    const premature = await api.finalize(projectId);
    expect(premature.status).toBe(409);
    expect(premature.body.error.code).toBe("PROPOSAL_INCOMPLETE");
  });

  test("running an analysis before intake is complete is refused", async ({ api, request }) => {
    const { projectId } = await api.createProject();

    const response = await request.post(`/api/v1/projects/${projectId}/run-analysis`);
    // The project exists — it is the workflow step that is wrong, so this is a
    // conflict rather than a missing resource.
    expect(response.status()).toBe(409);
    expect((await response.json()).error.code).toBe("INVALID_STEP_TRANSITION");
  });

  test("the proposal carries a deterministic written summary", async ({ api }) => {
    const { shareToken } = await api.finalisedProposal("6 kWp");
    const { body } = await api.proposal(shareToken);

    const summary: string = body.aiSummary;
    expect(summary.length).toBeGreaterThan(80);

    // Whatever writes the prose, the numbers in it are the ones the backend
    // calculated. A figure that appears nowhere in the analysis would mean the
    // summary invented it.
    expect(summary).toContain(`${EXPECTED["6"].actualPanelCount} panels`);
    expect(summary).toContain(
      `${Math.round(EXPECTED["6"].annualProductionKwh).toLocaleString("en-US")} kWh`,
    );
    expect(summary).toContain("0.87897");
    expect(summary).toContain("8,789.70");
    expect(summary).not.toMatch(/\b1\.0+\b\s*(exchange|rate)/i);
  });
});
