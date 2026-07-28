import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures/proposal";

/**
 * Accessibility.
 *
 * ## What this does and does not prove
 *
 * `axe` is an **automated safety net**. It catches contrast, naming, landmark
 * and ARIA-shape problems reliably, and it catches perhaps a third of WCAG
 * failures overall. A clean axe run is **not** a claim of WCAG compliance, and
 * this suite does not make one. The manual checks below cover a few things axe
 * cannot see at all: that the flow is completable from the keyboard, that focus
 * is visible, and that a status change is announced.
 *
 * `@axe-core/playwright` is a **devDependency** and is injected into the page
 * by the test runner. It is not part of the application and never ships.
 */

const WCAG = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"];

/**
 * One line per offending node, naming the element and the measurement.
 *
 * "3 nodes failed color-contrast" is not actionable; a selector and a ratio
 * is. A failure here should be fixable without re-running anything.
 */
function describeViolations(
  violations: { id: string; help: string; nodes: { target: unknown[]; html?: string; any: { message: string }[] }[] }[],
): string[] {
  return violations.flatMap((violation) =>
    violation.nodes.map(
      (node) =>
        `${violation.id} @ ${JSON.stringify(node.target)} — ` +
        `${node.any.map((check) => check.message).join("; ")} — ${(node.html ?? "").slice(0, 120)}`,
    ),
  );
}

/**
 * Wait for every running animation to finish before measuring.
 *
 * Chat bubbles fade in over 180 ms. Scanned mid-fade, a partly transparent
 * bubble composites to #78797a on white and axe correctly reports 4.21:1 —
 * a real measurement of a state that exists for a sixth of a second and is
 * exempt from the contrast requirement anyway. The result depended on machine
 * load, which is the worst kind of test: right sometimes, for no visible
 * reason. Measure the settled page instead.
 */
async function settle(page: Page): Promise<void> {
  await page.waitForFunction(
    () => document.getAnimations().every((animation) => animation.playState !== "running"),
    undefined,
    { timeout: 10_000 },
  );
}

test.describe("@p1 accessibility", () => {
  test("the intake screen has no automatically detectable violations", async ({
    solarFlow,
    page,
  }) => {
    await solarFlow.open();
    // Scan a settled page. The provenance chips arrive on their own request,
    // so scanning before they land would silently skip them — and scanning
    // mid-flight makes the result depend on machine load.
    await expect(solarFlow.status("parser")).toBeVisible();
    await expect(solarFlow.status("imagery")).toBeVisible();
    await settle(page);

    const results = await new AxeBuilder({ page }).withTags(WCAG).analyze();
    expect(describeViolations(results.violations)).toEqual([]);
  });

  test("the completed analysis has no automatically detectable violations", async ({
    solarFlow,
    page,
  }) => {
    await solarFlow.open();
    await solarFlow.completeIntake({ size: 6 });
    await settle(page);

    const results = await new AxeBuilder({ page })
      .withTags(WCAG)
      // The Konva stage is a single <canvas>; its contents are described by the
      // facet table beside it, which is what a screen reader should read.
      .exclude("[data-testid='roof-stage']")
      .analyze();
    expect(describeViolations(results.violations)).toEqual([]);
  });

  test("the public proposal has no automatically detectable violations", async ({
    api,
    proposalPage,
    page,
  }) => {
    const { shareToken } = await api.finalisedProposal("6 kWp");
    await proposalPage.open(shareToken);
    await settle(page);

    const results = await new AxeBuilder({ page }).withTags(WCAG).analyze();
    expect(describeViolations(results.violations)).toEqual([]);
  });

  test("the whole intake is completable from the keyboard alone", async ({
    solarFlow,
    page,
  }) => {
    await solarFlow.open();

    // Reach the composer by tabbing, not by clicking.
    await page.keyboard.press("Tab");
    for (let i = 0; i < 12; i += 1) {
      const id = await page.evaluate(() => document.activeElement?.id ?? "");
      if (id === "chat-input") break;
      await page.keyboard.press("Tab");
    }
    expect(await page.evaluate(() => document.activeElement?.id)).toBe("chat-input");

    await page.keyboard.type("-34.04658242871865, 18.46491476666948");
    await page.keyboard.press("Enter");
    await expect(solarFlow.assistantMessages).toHaveCount(2);

    await page.keyboard.type("1150 kWh");
    await page.keyboard.press("Enter");
    await expect(solarFlow.systemSizeCards).toBeVisible();

    // The size cards are real buttons, so they are reachable and activatable.
    await solarFlow.page.getByTestId("system-size-6").focus();
    await expect(solarFlow.page.getByTestId("system-size-6")).toBeFocused();
    await page.keyboard.press("Enter");
    await solarFlow.waitForAnalysis();
    await expect(solarFlow.kpi("system-size")).toHaveText("6 kWp");
  });

  test("progress and errors are announced, not only coloured", async ({ solarFlow, page }) => {
    await solarFlow.open();

    // The progress rail is a list with an accessible name, and the active step
    // is marked with aria-current rather than by colour alone.
    const rail = page.getByRole("list", { name: "Progress" });
    await expect(rail).toBeVisible();
    await expect(rail.locator("[aria-current='step']")).toHaveCount(1);

    await solarFlow.enterLocation("-34.04658242871865, 18.46491476666948");
    await expect(rail.locator("[aria-current='step']")).toHaveText("Usage");

    // A failure reaches assistive technology immediately.
    await page.route("**/api/v1/projects/*/chat", (route) =>
      route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ error: { code: "INTERNAL", message: "Simulated" } }),
      }),
    );
    await solarFlow.chatInput.fill("1150 kWh");
    await solarFlow.chatInput.press("Enter");
    // Addressed by test id, not by role: Next injects its own route-announcer
    // with role="alert", so the role alone is ambiguous on every page.
    await expect(solarFlow.errorBanner).toBeVisible();
    await expect(solarFlow.errorBanner).toHaveAttribute("role", "alert");
  });

  test("provenance is never carried by colour alone", async ({ solarFlow, page }) => {
    await solarFlow.open();
    await solarFlow.completeIntake({ size: 6 });

    // Every provenance badge states its meaning in words. Someone who cannot
    // distinguish amber from green must still be able to tell demo data from
    // live data — which is the single most important thing on the page.
    for (const testId of ["fx-source-badge", "pvgis-source-badge", "imagery-source-badge"]) {
      const badge = page.getByTestId(testId);
      await expect(badge).toBeVisible();
      expect((await badge.innerText()).trim().length).toBeGreaterThan(3);
    }
  });

  test("the proposal page has one h1 and an ordered heading structure", async ({
    api,
    proposalPage,
    page,
  }) => {
    const { shareToken } = await api.finalisedProposal("6 kWp");
    await proposalPage.open(shareToken);

    await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);

    const levels = await page
      .locator("h1, h2, h3, h4, h5, h6")
      .evaluateAll((nodes) => nodes.map((n) => Number(n.tagName[1])));
    expect(levels[0]).toBe(1);
    for (let i = 1; i < levels.length; i += 1) {
      // A heading may never skip a level going down (h2 -> h4).
      expect(levels[i]! - levels[i - 1]!, `heading ${i} jumped a level`).toBeLessThanOrEqual(1);
    }
  });
});
