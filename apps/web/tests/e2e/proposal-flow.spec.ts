import { expect, test, type Page } from "@playwright/test";

/**
 * The acceptance flow from the brief, driven through the real UI against the
 * real API in fixture mode.
 */

const CASE_COORD = "-34.04658242871865, 18.46491476666948";

async function say(page: Page, message: string) {
  await page.fill("#chat-input", message);
  await page.press("#chat-input", "Enter");
  await page.waitForTimeout(900);
}

async function intake(page: Page, sizeReply: string) {
  await page.goto("/");
  await expect(page.getByText("solarVis AI").first()).toBeVisible();
  await say(page, CASE_COORD);
  await say(page, "1,150 kWh");
  await say(page, sizeReply);
  // The analysis kicks off automatically once the size is accepted.
  await expect(page.getByText("Analysis complete.")).toBeVisible({ timeout: 60_000 });
}

test("welcomes the user and asks for a location", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText(/Welcome to solarVis AI/)).toBeVisible();
  await expect(page.getByText(/latitude and longitude/)).toBeVisible();
  await expect(page.getByText("Location")).toBeVisible();
});

test("resolves the location and explains the coordinate correction", async ({ page }) => {
  await page.goto("/");
  await say(page, CASE_COORD);
  await expect(page.getByText(/Cape Town/)).toBeVisible();
  await expect(page.getByText(/without a minus sign/)).toBeVisible();
});

test("multiplies consumption out and offers exactly three sizes", async ({ page }) => {
  await page.goto("/");
  await say(page, CASE_COORD);
  await say(page, "1,150 kWh");

  await expect(page.getByText(/13,800 kWh\/year/)).toBeVisible();
  await expect(page.getByText(/3\.6 kWp\s+\(9 panels/)).toBeVisible();
  await expect(page.getByText(/6 kWp\s+\(15 panels/)).toBeVisible();
  await expect(page.getByText(/9\.6 kWp\s+\(24 panels/)).toBeVisible();
});

test("completes the 6 kWp happy path end to end", async ({ page }) => {
  await intake(page, "the middle option");

  // Panels land on the good facets and skip the poor one.
  await expect(page.getByText("Placed 15 panels (6 kWp)")).toBeVisible();
  await expect(page.getByRole("button", { name: /North facet/ })).toContainText("9 panels");
  await expect(page.getByRole("button", { name: /South facet/ })).toContainText("No panels");

  // CAPEX is converted, and the rate is never parity. Both figures appear in
  // the FX row and again in the chat summary — which is correct — so scope the
  // assertion to the row rather than loosening it.
  const fxRow = page.getByText("Capital cost as quoted").locator("..");
  await expect(fxRow.getByText("$10,000.00")).toBeVisible();
  await expect(fxRow.getByText("€8,789.70")).toBeVisible();
  await expect(fxRow.getByText("0.87897")).toBeVisible();

  // Create the proposal and open the share route in a new tab.
  await page.getByRole("button", { name: "Create proposal" }).click();
  await expect(page.getByText("Proposal created")).toBeVisible({ timeout: 30_000 });

  const shareUrl = await page.locator("code").first().innerText();
  expect(shareUrl).toContain("/proposal/");

  const token = shareUrl.split("/").pop() ?? "";
  await page.goto(`/proposal/${token}`);

  await expect(page.getByText("Solar Feasibility Proposal")).toBeVisible();
  await expect(page.getByText("6 kWp").first()).toBeVisible();
  await expect(page.getByText("9,502 kWh").first()).toBeVisible();
  await expect(page.getByRole("link", { name: /Download PDF/ })).toBeVisible();

  // The share page must show the same converted CAPEX as the workspace did.
  await expect(page.getByText("€8,789.70").first()).toBeVisible();
});

test("the smallest system fits on the best facet alone", async ({ page }) => {
  await intake(page, "smallest");
  await expect(page.getByText("Placed 9 panels (3.6 kWp)")).toBeVisible();
  await expect(page.getByRole("button", { name: /North facet/ })).toContainText("9 panels");
});

test("the largest system uses all four facets", async ({ page }) => {
  await intake(page, "twenty-four panels");
  await expect(page.getByText("Placed 24 panels (9.6 kWp)")).toBeVisible();
  await expect(page.getByRole("button", { name: /South facet/ })).toContainText("9 panels");
});

test("refuses a system size that is not offered", async ({ page }) => {
  await page.goto("/");
  await say(page, CASE_COORD);
  await say(page, "1,150 kWh");
  await say(page, "5 kWp");
  await expect(page.getByText(/choose one of the three available sizes/i)).toBeVisible();
});

test("labels demo data as demo data", async ({ page }) => {
  await intake(page, "the middle option");
  await expect(page.getByText("Demo fixture").first()).toBeVisible();
  await expect(page.getByText(/not a live market rate/)).toBeVisible();
});

test("roof layers can be toggled", async ({ page }) => {
  await intake(page, "the middle option");
  // Exact match: the facet cards also mention "panels" in their labels.
  const panels = page.getByRole("button", { name: "Panels", exact: true });
  await expect(panels).toHaveAttribute("aria-pressed", "true");
  await panels.click();
  await expect(panels).toHaveAttribute("aria-pressed", "false");
});

test("an unknown proposal token shows a friendly error", async ({ page }) => {
  await page.goto(`/proposal/${"z".repeat(40)}`);
  await expect(page.getByText("Proposal not found")).toBeVisible();
});
