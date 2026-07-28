import { expect, type Locator, type Page } from "@playwright/test";

/**
 * The developer calibration tool at `/dev/roof-calibration`.
 *
 * This page exists because of a real defect: window-relative pointer
 * coordinates were once committed as source-map pixels, putting the whole
 * calibration ~410 px out. Dimensions, aspect ratios and azimuths all still
 * looked right — only the overlay's position gave it away. The page object
 * therefore exposes the *coordinate conversion* rather than a screenshot.
 */
export class CalibrationPage {
  readonly canvas: Locator;
  readonly cursor: Locator;
  readonly scaleBadge: Locator;
  readonly validation: Locator;

  constructor(readonly page: Page) {
    this.canvas = page.getByTestId("calibration-canvas");
    this.cursor = page.getByTestId("calibration-cursor");
    this.scaleBadge = page.getByTestId("calibration-scale");
    this.validation = page.getByTestId("calibration-validation");
  }

  async open(): Promise<void> {
    await this.page.goto("/dev/roof-calibration");
    await expect(this.canvas).toBeVisible({ timeout: 30_000 });
    // The committed calibration loads asynchronously; a vertex row proves it
    // arrived and was parsed, not merely that the shell rendered.
    await expect(this.page.locator("[data-testid^='vertex-row-']").first()).toBeVisible({
      timeout: 30_000,
    });
  }

  async sourceWidthPx(): Promise<number> {
    return Number(await this.scaleBadge.getAttribute("data-source-width-px"));
  }

  async metresPerPixel(): Promise<number> {
    return Number(await this.scaleBadge.getAttribute("data-metres-per-pixel"));
  }

  async issueCount(): Promise<number> {
    return Number(await this.validation.getAttribute("data-issue-count"));
  }

  vertexRow(id: string): Locator {
    return this.page.getByTestId(`vertex-row-${id}`);
  }

  /** Committed source-pixel coordinates of one vertex, straight from the DOM. */
  async vertex(id: string): Promise<{ x: number; y: number }> {
    const row = this.vertexRow(id);
    return {
      x: Number(await row.getAttribute("data-x")),
      y: Number(await row.getAttribute("data-y")),
    };
  }

  async vertexIds(): Promise<string[]> {
    const rows = this.page.locator("[data-testid^='vertex-row-']");
    const ids: string[] = [];
    for (const row of await rows.all()) {
      const testId = await row.getAttribute("data-testid");
      if (testId) ids.push(testId.replace("vertex-row-", ""));
    }
    return ids;
  }

  /**
   * Hover a point given in **source-map pixels** and report both what the page
   * says the cursor is over and what it should say.
   *
   * The browser dispatches pointer events at whole device pixels, and the
   * canvas origin is rarely on one, so the mouse lands a fraction away from
   * where it was aimed. `expected` is therefore derived from the *integer*
   * position actually used — which removes that noise entirely and makes the
   * comparison stricter, not looser. A correct transform round-trips exactly;
   * the 410 px bug would return the value plus the canvas origin.
   */
  async probeSourcePoint(source: { x: number; y: number }): Promise<{
    reported: { x: number; y: number };
    expected: { x: number; y: number };
  }> {
    const box = await this.canvas.boundingBox();
    if (!box) throw new Error("calibration canvas has no bounding box");
    const width = await this.sourceWidthPx();
    // View starts at scale 1, offset 0, and the stage is laid out at
    // (container width / source width). Independent of the component's maths:
    // this is just "the image fills the container".
    const base = box.width / width;

    const mouseX = Math.round(box.x + source.x * base);
    const mouseY = Math.round(box.y + source.y * base);

    const viewport = this.page.viewportSize();
    if (viewport && (mouseY > viewport.height || mouseX > viewport.width)) {
      throw new Error(
        `source point (${source.x}, ${source.y}) maps to (${mouseX}, ${mouseY}), ` +
          `outside the ${viewport.width}x${viewport.height} viewport — pick a point nearer the centre`,
      );
    }

    // Nudge away first, so a repeat probe cannot read a stale value.
    await this.page.mouse.move(box.x + 1, box.y + 1);
    await this.page.mouse.move(mouseX, mouseY);
    await expect(this.cursor).not.toHaveAttribute("data-x", "");

    const x = await this.cursor.getAttribute("data-x");
    const y = await this.cursor.getAttribute("data-y");
    if (!x || !y) throw new Error("the cursor readout reported nothing");

    return {
      reported: { x: Number(x), y: Number(y) },
      expected: { x: (mouseX - box.x) / base, y: (mouseY - box.y) / base },
    };
  }
}
