import { expect, type Locator, type Page } from "@playwright/test";

import type { KpiName } from "./solar-flow.page";

/**
 * The public share route, `/proposal/<token>`.
 *
 * It reads one immutable snapshot and recalculates nothing, so the assertions
 * here are the same ones used against the workspace — a difference between the
 * two would mean the snapshot is not the single source of truth it claims to be.
 */
export class ProposalPage {
  readonly title: Locator;
  readonly notFound: Locator;
  readonly viewCount: Locator;
  readonly pdfLink: Locator;
  readonly layoutSnapshot: Locator;
  readonly kpiRow: Locator;
  readonly fxRow: Locator;

  constructor(readonly page: Page) {
    this.title = page.getByTestId("proposal-title");
    this.notFound = page.getByTestId("proposal-not-found");
    this.viewCount = page.getByTestId("view-count");
    this.pdfLink = page.getByTestId("pdf-link");
    this.layoutSnapshot = page.getByTestId("layout-snapshot");
    this.kpiRow = page.getByTestId("kpi-row");
    this.fxRow = page.getByTestId("fx-row");
  }

  async open(token: string): Promise<void> {
    await this.page.goto(`/proposal/${token}`);
    await expect(this.title).toBeVisible({ timeout: 30_000 });
    await expect(this.kpiRow).toBeVisible();
  }

  /** Open a token expected to be rejected — no wait for content that never comes. */
  async openExpectingFailure(token: string): Promise<void> {
    await this.page.goto(`/proposal/${token}`);
    await expect(this.notFound).toBeVisible({ timeout: 30_000 });
  }

  kpi(name: KpiName): Locator {
    return this.page.getByTestId(`kpi-${name}`);
  }

  fx(field: "capex-usd" | "capex-eur" | "rate" | "rate-date" | "provider" | "source-badge"): Locator {
    return this.page.getByTestId(`fx-${field}`);
  }

  async views(): Promise<number> {
    const value = await this.viewCount.getAttribute("data-count");
    return Number(value ?? "0");
  }

  /** Every figure a test compares between page, PDF and snapshot. */
  async readHeadlineFigures(): Promise<Record<string, string>> {
    const fields: [string, Locator][] = [
      ["systemSize", this.kpi("system-size")],
      ["annualProduction", this.kpi("annual-production")],
      ["annualSaving", this.kpi("annual-saving")],
      ["payback", this.kpi("payback")],
      ["twentyYearNet", this.kpi("twenty-year-net")],
      ["capexUsd", this.fx("capex-usd")],
      ["capexEur", this.fx("capex-eur")],
      ["fxRate", this.fx("rate")],
      ["fxRateDate", this.fx("rate-date")],
      ["fxProvider", this.fx("provider")],
    ];
    const result: Record<string, string> = {};
    for (const [key, locator] of fields) {
      result[key] = (await locator.innerText()).trim();
    }
    return result;
  }
}
