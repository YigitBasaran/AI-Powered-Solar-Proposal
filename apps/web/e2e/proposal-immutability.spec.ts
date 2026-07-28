import { expect, test } from "./fixtures/proposal";
import { EXPECTED_FX } from "./fixtures/expected-values";
import { extractPdf } from "./helpers/pdf";

/**
 * A finalised proposal never moves.
 *
 * The exchange rate is a *stored input*, not a live lookup. Once a customer has
 * been sent a link, reopening it must not quietly restate the offer at a newer
 * rate — the figures are contractual in tone even though the document says it
 * is an estimate.
 *
 * The stack is in fixture mode, so the rate cannot drift on its own. What is
 * tested here is the structural guarantee: page, PDF and API all read one
 * snapshot, and repeated reads over time return the identical bytes.
 */

test.describe("@p0 proposal immutability", () => {
  test("the snapshot is identical on every read", async ({ api }) => {
    const { shareToken } = await api.finalisedProposal("6 kWp");

    const first = await api.proposal(shareToken);
    const second = await api.proposal(shareToken);

    // `views` is the one field that legitimately changes, so it is excluded.
    const strip = (body: Record<string, unknown>) => {
      const { views: _views, ...rest } = body;
      return JSON.stringify(rest);
    };
    expect(strip(second.body)).toBe(strip(first.body));
  });

  test("later analyses do not change an existing proposal", async ({ api }) => {
    const { shareToken } = await api.finalisedProposal("3.6 kWp");
    const before = await api.proposal(shareToken);

    // Run a completely different analysis afterwards. If any part of the
    // proposal were recomputed on read — or shared cached state leaked between
    // projects — this would move it.
    await api.analysedProject("9.6 kWp");
    await api.finalisedProposal("6 kWp");

    const after = await api.proposal(shareToken);
    expect(after.body.financial).toEqual(before.body.financial);
    expect(after.body.layout).toEqual(before.body.layout);
    expect(after.body.energy).toEqual(before.body.energy);
    expect(after.body.exchangeRate).toEqual(before.body.exchangeRate);
    expect(after.body.createdAt).toBe(before.body.createdAt);
  });

  test("the page, the PDF and the API agree, and keep agreeing", async ({
    api,
    proposalPage,
  }) => {
    const { shareToken } = await api.finalisedProposal("6 kWp");

    const snapshot = (await api.proposal(shareToken)).body;
    await proposalPage.open(shareToken);
    const firstPage = await proposalPage.readHeadlineFigures();
    const firstPdf = await extractPdf((await api.proposalPdf(shareToken)).body);

    expect(firstPage.fxRate).toBe(snapshot.exchangeRate.rate);
    expect(firstPage.fxRateDate).toBe(snapshot.exchangeRate.rateDate);
    expect(firstPdf.text).toContain(snapshot.exchangeRate.rate);
    expect(firstPdf.text).toContain(snapshot.financial.annualSavingsEur);

    // Reload everything. Same token, same numbers, no drift.
    await proposalPage.page.reload();
    await expect(proposalPage.kpiRow).toBeVisible();
    const secondPage = await proposalPage.readHeadlineFigures();
    const secondPdf = await extractPdf((await api.proposalPdf(shareToken)).body);

    expect(secondPage).toEqual(firstPage);
    expect(secondPdf.text).toBe(firstPdf.text);
    // And still the committed fixture rate, never substituted for parity.
    expect(secondPage.fxRate).toBe(EXPECTED_FX.rate);
    expect(Number(secondPage.fxRate)).not.toBe(1);
  });

  test("the stored assumptions say the rate was fixed at creation", async ({
    api,
    proposalPage,
  }) => {
    const { shareToken } = await api.finalisedProposal("6 kWp");
    await proposalPage.open(shareToken);

    // The page must tell the reader that reopening it will not re-price the
    // offer. Otherwise "immutable" is an implementation detail nobody knows.
    await expect(proposalPage.page.getByText(/will not recalculate/i)).toBeVisible();

    const { text } = await extractPdf((await api.proposalPdf(shareToken)).body);
    expect(text.toLowerCase()).toContain("will not recalculate these figures at a newer rate");
  });
});
