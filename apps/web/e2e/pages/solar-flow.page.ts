import { expect, type Locator, type Page } from "@playwright/test";

/**
 * The primary customer flow: chat -> analysis -> proposal.
 *
 * Every wait here is on an observable application state (a message rendered, a
 * busy overlay gone, an element with a real value present). There are no fixed
 * sleeps: a `waitForTimeout` is a guess that either wastes time or flakes, and
 * on a slower machine it does both.
 */
export type KpiName =
  | "system-size"
  | "annual-production"
  | "annual-saving"
  | "payback"
  | "twenty-year-net";

export class SolarFlowPage {
  readonly chatInput: Locator;
  readonly sendButton: Locator;
  readonly errorBanner: Locator;
  readonly busyOverlay: Locator;
  readonly systemSizeCards: Locator;
  readonly kpiRow: Locator;
  readonly fxRow: Locator;
  readonly createProposalButton: Locator;
  readonly shareUrl: Locator;
  readonly capacityWarning: Locator;

  constructor(readonly page: Page) {
    // By role, not by label text: `getByLabel("Message")` also matches the
    // send button's "Send message" accessible name.
    this.chatInput = page.getByRole("textbox", { name: "Message" });
    this.sendButton = page.getByRole("button", { name: "Send message" });
    this.errorBanner = page.getByTestId("error-banner");
    this.busyOverlay = page.getByTestId("analysis-busy");
    this.systemSizeCards = page.getByTestId("system-size-cards");
    this.kpiRow = page.getByTestId("kpi-row");
    this.fxRow = page.getByTestId("fx-row");
    this.createProposalButton = page.getByTestId("create-proposal");
    this.shareUrl = page.getByTestId("share-url");
    this.capacityWarning = page.getByTestId("capacity-warning");
  }

  async open(): Promise<void> {
    await this.page.goto("/");
    // The session is created by the boot effect; the chat input is only usable
    // once the project exists, so this is the real "ready" signal.
    await expect(this.chatInput).toBeEnabled({ timeout: 30_000 });
    await expect(this.assistantMessages.first()).toBeVisible();
  }

  get userMessages(): Locator {
    // User bubbles are the navy right-aligned ones; assistant bubbles are
    // bordered and left-aligned. Structure, not copy.
    return this.page.locator("div.justify-end > div");
  }

  get assistantMessages(): Locator {
    return this.page.locator("div.justify-start > div").filter({ hasNotText: "Thinking…" });
  }

  get lastAssistantMessage(): Locator {
    return this.assistantMessages.last();
  }

  /**
   * Send a message and wait for the assistant to reply.
   *
   * "At least one more", not "exactly one more": the final intake answer is
   * followed immediately by the analysis summary, so on a fast machine two
   * messages can land before the assertion first settles. Requiring an exact
   * count made that a race — which is the sort of flake a suite gets blamed
   * for and the application never had.
   */
  async send(message: string): Promise<void> {
    const before = await this.assistantMessages.count();
    await this.chatInput.fill(message);
    await this.chatInput.press("Enter");
    await this.waitForReplyAfter(before);
  }

  private async waitForReplyAfter(before: number): Promise<void> {
    await expect
      .poll(() => this.assistantMessages.count(), { timeout: 30_000 })
      .toBeGreaterThan(before);
  }

  async enterLocation(location: string): Promise<void> {
    await this.send(location);
  }

  async enterMonthlyConsumption(kwh: number | string): Promise<void> {
    await this.send(typeof kwh === "number" ? `${kwh} kWh` : kwh);
  }

  /**
   * Choose a system size. Clicking a card sends the same canonical phrase a
   * user could type, so both routes exercise one backend path.
   */
  async selectSystemSize(kwp: 3.6 | 6 | 9.6, via: "card" | "chat" = "card"): Promise<void> {
    if (via === "chat") {
      await this.send(`${kwp} kWp`);
    } else {
      await expect(this.systemSizeCards).toBeVisible();
      const before = await this.assistantMessages.count();
      await this.page.getByTestId(`system-size-${kwp}`).click();
      await this.waitForReplyAfter(before);
    }
    await this.waitForAnalysis();
  }

  /** Analysis is kicked off automatically once intake completes. */
  async waitForAnalysis(): Promise<void> {
    await expect(this.busyOverlay).toBeHidden({ timeout: 90_000 });
    await expect(this.kpiRow).toBeVisible({ timeout: 90_000 });
  }

  /** Walk the whole intake in one call. */
  async completeIntake(
    options: {
      location?: string;
      consumption?: number | string;
      size?: 3.6 | 6 | 9.6;
      via?: "card" | "chat";
    } = {},
  ): Promise<void> {
    const {
      location = "-34.04658242871865, 18.46491476666948",
      consumption = 1150,
      size = 6,
      via = "card",
    } = options;
    await this.enterLocation(location);
    await this.enterMonthlyConsumption(consumption);
    await this.selectSystemSize(size, via);
  }

  /** The testid sits on the value element itself; `-note` is its subtitle. */
  kpi(name: KpiName): Locator {
    return this.page.getByTestId(`kpi-${name}`);
  }

  kpiNote(name: KpiName): Locator {
    return this.page.getByTestId(`kpi-${name}-note`);
  }

  fx(field: "capex-usd" | "capex-eur" | "rate" | "rate-date" | "provider" | "source-badge") {
    return this.page.getByTestId(`fx-${field}`);
  }

  status(kind: "parser" | "imagery" | "fx") {
    return this.page.getByTestId(`status-${kind}`);
  }

  /** Create the proposal and return its share token. */
  async finalizeProposal(): Promise<{ shareToken: string; shareUrl: string }> {
    await this.createProposalButton.click();
    await expect(this.shareUrl).toBeVisible({ timeout: 60_000 });
    const shareToken = await this.shareUrl.getAttribute("data-share-token");
    const shareUrl = (await this.shareUrl.innerText()).trim();
    if (!shareToken) throw new Error("share token attribute was missing");
    return { shareToken, shareUrl };
  }
}
