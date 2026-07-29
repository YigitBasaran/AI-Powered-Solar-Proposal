import { expect, test } from "./fixtures/proposal";

/**
 * What the customer sees when the analysis cannot be produced.
 *
 * The failure is injected in the *browser*, on the `run-analysis` request,
 * which is legitimate here in a way that mocking PVGIS would not be: the
 * browser genuinely makes this request, and the response being intercepted is
 * exactly the one the backend produces when PVGIS is unreachable. The backend
 * side of that is covered against a stack whose PVGIS really never answers, in
 * `pvgis-failure.spec.ts`.
 *
 * The thing worth guarding is that nothing is shown rather than something
 * plausible. A blank KPI row, or worse a partially-populated one, would be read
 * as figures.
 */

const FAILURE = {
  status: 502,
  contentType: "application/json",
  body: JSON.stringify({
    error: {
      code: "PVGIS_UNAVAILABLE",
      message: "Solar production data could not be retrieved from PVGIS.",
      details: {},
    },
  }),
};

test.describe("@p1 analysis failure", () => {
  test("says what failed, shows no figures, and offers a retry", async ({ solarFlow, page }) => {
    await page.route("**/api/v1/projects/*/run-analysis", (route) => route.fulfill(FAILURE));

    await solarFlow.open();
    await solarFlow.completeIntakeExpectingNoFigures();

    const banner = page.getByTestId("failed-analysis");
    await expect(banner).toBeVisible();
    await expect(banner).toContainText(/could not be retrieved/i);
    await expect(banner).toContainText(/PVGIS/);

    // The whole point: no numbers at all, rather than numbers from somewhere else.
    await expect(solarFlow.kpiRow).toHaveCount(0);
    await expect(solarFlow.createProposalButton).toHaveCount(0);

    await expect(page.getByTestId("retry-analysis")).toBeEnabled();
  });

  test("a retry that succeeds clears the banner and produces figures", async ({
    solarFlow,
    page,
  }) => {
    // Fails once, then gets out of the way - the outage is a state, not a
    // sentence, and a banner that survived a successful retry would be worse
    // than no banner.
    let attempts = 0;
    await page.route("**/api/v1/projects/*/run-analysis", async (route) => {
      attempts += 1;
      if (attempts === 1) return route.fulfill(FAILURE);
      return route.fallback();
    });

    await solarFlow.open();
    await solarFlow.completeIntakeExpectingNoFigures();
    await expect(page.getByTestId("failed-analysis")).toBeVisible();

    await page.getByTestId("retry-analysis").click();

    await expect(page.getByTestId("failed-analysis")).toHaveCount(0);
    await expect(solarFlow.kpiRow).toBeVisible();
  });
});
