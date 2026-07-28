import { expect, test } from "./fixtures/proposal";
import { EXPECTED } from "./fixtures/expected-values";
import { extractPdf } from "./helpers/pdf";
import { serialiseLayout } from "./helpers/geometry";

/**
 * Several things happening at once.
 *
 * One API process serves every worker through a single SQLite file, so this is
 * also the test that the WAL + busy-timeout configuration actually holds:
 * before it, concurrent writers failed instantly with "database is locked"
 * rather than waiting their turn.
 */

test.describe("@p1 concurrency", () => {
  test("several proposals can be built at the same time", async ({ api }) => {
    const sizes = ["3.6 kWp", "6 kWp", "9.6 kWp", "6 kWp", "3.6 kWp"] as const;

    const results = await Promise.all(sizes.map((size) => api.finalisedProposal(size)));

    const tokens = new Set(results.map((r) => r.shareToken));
    expect(tokens.size, "share tokens collided under concurrency").toBe(sizes.length);

    // Each proposal is the one its own inputs called for — no cross-talk.
    for (const [index, result] of results.entries()) {
      const key = sizes[index]!.replace(" kWp", "") as "3.6" | "6" | "9.6";
      expect(result.analysis.layout.placedPanelCount).toBe(EXPECTED[key].actualPanelCount);
      expect(result.analysis.financial.annualSavingsEur).toBe(EXPECTED[key].annualSavingsEur);
    }
  });

  test("concurrent analyses of the same size produce identical geometry", async ({ api }) => {
    const runs = await Promise.all([
      api.analysedProject("6 kWp"),
      api.analysedProject("6 kWp"),
      api.analysedProject("6 kWp"),
    ]);

    const serialised = runs.map((r) => serialiseLayout(r.analysis.layout.facets));
    expect(serialised[1]).toBe(serialised[0]);
    expect(serialised[2]).toBe(serialised[0]);
  });

  test("the same PDF can be downloaded several times at once", async ({ api }) => {
    const { shareToken } = await api.finalisedProposal("6 kWp");

    const downloads = await Promise.all([
      api.proposalPdf(shareToken),
      api.proposalPdf(shareToken),
      api.proposalPdf(shareToken),
      api.proposalPdf(shareToken),
    ]);

    for (const pdf of downloads) {
      expect(pdf.status).toBe(200);
      expect(pdf.contentType).toContain("application/pdf");
    }

    // Same document every time: the PDF is rendered from one stored snapshot,
    // so concurrent renders cannot disagree.
    const texts = await Promise.all(downloads.map((d) => extractPdf(d.body)));
    for (const extracted of texts.slice(1)) {
      expect(extracted.text).toBe(texts[0]!.text);
    }
  });

  test("view counting survives simultaneous opens", async ({ api }) => {
    const { shareToken } = await api.finalisedProposal("3.6 kWp");

    await Promise.all(Array.from({ length: 6 }, () => api.recordView(shareToken)));

    const { body } = await api.proposal(shareToken);
    // Every write landed: a lost update here would mean a lost write anywhere.
    expect(body.views.viewCount).toBe(6);
  });

  test("two browser sessions do not see each other's project", async ({ browser }) => {
    const contexts = await Promise.all([browser.newContext(), browser.newContext()]);
    try {
      const pages = await Promise.all(contexts.map((c) => c.newPage()));
      const [pageA, pageB] = pages as [(typeof pages)[0], (typeof pages)[0]];

      const { SolarFlowPage } = await import("./pages/solar-flow.page");
      const flowA = new SolarFlowPage(pageA);
      const flowB = new SolarFlowPage(pageB);

      await Promise.all([flowA.open(), flowB.open()]);
      await Promise.all([
        flowA.completeIntake({ size: 3.6 }),
        flowB.completeIntake({ size: 9.6 }),
      ]);

      await expect(flowA.kpi("system-size")).toHaveText("3.6 kWp");
      await expect(flowB.kpi("system-size")).toHaveText("9.6 kWp");
    } finally {
      await Promise.all(contexts.map((c) => c.close()));
    }
  });
});
