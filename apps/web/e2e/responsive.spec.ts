import { expect, test } from "./fixtures/proposal";
import { EXPECTED } from "./fixtures/expected-values";

/**
 * Mobile smoke.
 *
 * Deliberately a handful of tests, not the whole suite re-run at a second
 * viewport. What matters on a phone is that the flow is completable and that
 * nothing overflows the screen; the calculations are the same code either way.
 */

test.describe("@p0 @mobile responsive", () => {
  test("the whole flow completes on a phone-sized viewport", async ({ solarFlow, page }) => {
    await solarFlow.open();
    await solarFlow.completeIntake({ size: 6 });

    await expect(solarFlow.kpi("system-size")).toHaveText("6 kWp");
    const { shareToken } = await solarFlow.finalizeProposal();
    expect(shareToken.length).toBeGreaterThan(15);

    // Nothing may scroll sideways: a horizontal scrollbar on a phone is the
    // classic sign of a fixed-width table or chart escaping its container.
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow, "the page scrolls horizontally").toBeLessThanOrEqual(1);
  });

  test("the proposal page is readable on a phone", async ({ api, proposalPage, page }) => {
    const { shareToken } = await api.finalisedProposal("6 kWp");
    await proposalPage.open(shareToken);

    await expect(proposalPage.kpi("system-size")).toBeVisible();
    await expect(proposalPage.fx("rate")).toHaveText(EXPECTED["6"].fxRate);

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow, "the proposal page scrolls horizontally").toBeLessThanOrEqual(1);

    // Tap targets on the header actions must be reachable, not clipped.
    const box = await proposalPage.pdfLink.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.height).toBeGreaterThanOrEqual(24);
  });
});
