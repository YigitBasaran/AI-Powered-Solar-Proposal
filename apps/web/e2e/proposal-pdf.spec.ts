import { expect, test } from "./fixtures/proposal";
import { CASE_INPUTS, EXPECTED, EXPECTED_FX, EXPECTED_ROOF } from "./fixtures/expected-values";
import { containsNumber, extractPdf, looksLikePdf, normalise } from "./helpers/pdf";

/**
 * The PDF, checked by reading it.
 *
 * Asserting the status code, the content type and a `%PDF-` header would prove
 * a PDF exists — not that it says anything true. `pdfjs-dist` (a dev-only
 * dependency) extracts the text so the numbers a customer would read are the
 * ones actually asserted.
 */

test.describe("@p0 proposal PDF", () => {
  test("is served as a downloadable PDF", async ({ api }) => {
    const { shareToken } = await api.finalisedProposal("6 kWp");
    const pdf = await api.proposalPdf(shareToken);

    expect(pdf.status).toBe(200);
    expect(pdf.contentType).toContain("application/pdf");
    expect(pdf.disposition).toContain("attachment");
    expect(pdf.disposition).toMatch(/filename="solarvis-proposal-[^"]+\.pdf"/);
    expect(looksLikePdf(pdf.body)).toBe(true);
    expect(pdf.body.length).toBeGreaterThan(20_000);
  });

  test("contains every figure the customer needs to check", async ({ api }) => {
    const { shareToken } = await api.finalisedProposal("6 kWp");
    const expected = EXPECTED["6"];

    const pdf = await api.proposalPdf(shareToken);
    const { text, pageCount } = await extractPdf(pdf.body);

    expect(pageCount).toBeGreaterThanOrEqual(3);

    // Location: both what was entered and what was analysed.
    expect(text).toContain(CASE_INPUTS.locationInput);
    expect(text).toContain("-34.046582");

    // System and production.
    expect(containsNumber(text, expected.actualPanelCount)).toBe(true);
    expect(text).toMatch(/6\.0 kWp/);
    expect(containsNumber(text, Math.round(expected.annualProductionKwh))).toBe(true);
    expect(containsNumber(text, expected.annualConsumptionKwh)).toBe(true);

    // Currency: rate, date, source, and both sides of the conversion.
    expect(text).toContain(EXPECTED_FX.rate);
    expect(text).toContain(EXPECTED_FX.rateDate);
    expect(text).toContain(EXPECTED_FX.provider);
    expect(text).toContain(EXPECTED_FX.sourceApi);
    expect(containsNumber(text, expected.capexUsd)).toBe(true);
    expect(containsNumber(text, expected.capexEur)).toBe(true);

    // Financial outcomes.
    expect(containsNumber(text, expected.annualSavingsEur)).toBe(true);
    expect(text).toMatch(new RegExp(`${expected.paybackYears.toFixed(2)}\\s*years`));
    expect(containsNumber(text, expected.twentyYearNetBenefitEur)).toBe(true);

    // Provenance is stated, so fixture data cannot read as live.
    expect(normalise(text).toLowerCase()).toContain("fixture");
  });

  test("prints the roof reconstruction with correct hip geometry", async ({ api }) => {
    const { shareToken } = await api.finalisedProposal("6 kWp");
    const { text } = await extractPdf((await api.proposalPdf(shareToken)).body);

    expect(text).toContain("Roof reconstruction");
    expect(text).toContain("Edge measurements");
    expect(text).toContain(`${EXPECTED_ROOF.hipProjectedLengthM.toFixed(3)} m`);
    expect(text).toContain(`${EXPECTED_ROOF.hipTrue3dLengthM.toFixed(3)} m`);
    // The naive plan/cos(pitch) value must appear nowhere.
    expect(text).not.toContain(`${EXPECTED_ROOF.hipNaiveWrongLengthM.toFixed(3)} m`);

    // The southern-hemisphere reasoning is explained, not left implicit.
    expect(text.toLowerCase()).toContain("southern hemisphere");
  });

  test("prints the full twenty-year cash flow, reconciling row by row", async ({ api }) => {
    const { shareToken } = await api.finalisedProposal("6 kWp");
    const expected = EXPECTED["6"];
    const { text } = await extractPdf((await api.proposalPdf(shareToken)).body);

    expect(text).toContain("Twenty-year cash flow");
    // Year 20's cumulative figure is the headline 20-year result: the table and
    // the summary cannot disagree.
    expect(containsNumber(text, expected.twentyYearNetBenefitEur)).toBe(true);

    const savings = Number(expected.annualSavingsEur);
    const capex = Number(expected.capexEur);
    for (const year of [1, 5, 10, 20]) {
      const cumulative = (savings * year - capex).toFixed(2);
      expect(containsNumber(text, cumulative), `cash-flow year ${year}`).toBe(true);
    }
  });

  test("the PDF and the share page state the same numbers", async ({ api, proposalPage }) => {
    const { shareToken } = await api.finalisedProposal("9.6 kWp");

    await proposalPage.open(shareToken);
    const page = await proposalPage.readHeadlineFigures();

    const { text } = await extractPdf((await api.proposalPdf(shareToken)).body);

    // Both read one immutable snapshot, so every figure must be present in
    // both. Formatting differs (compact euro on screen, cents in the PDF), so
    // the comparison is on the numeric content.
    expect(text).toContain(page.fxRate!);
    expect(text).toContain(page.fxRateDate!);
    expect(containsNumber(text, page.capexUsd!.replace(/[$,\s]/g, ""))).toBe(true);
    expect(containsNumber(text, page.capexEur!.replace(/[€,\s]/g, ""))).toBe(true);
    expect(text).toContain("9.6 kWp");

    const expected = EXPECTED["9.6"];
    expect(containsNumber(text, expected.annualSavingsEur)).toBe(true);
    expect(containsNumber(text, expected.twentyYearNetBenefitEur)).toBe(true);
  });

  test("a PDF for an unknown token is refused", async ({ api }) => {
    const pdf = await api.proposalPdf("not-a-real-token");
    expect(pdf.status).toBe(404);
    expect(pdf.contentType).not.toContain("application/pdf");
  });
});
