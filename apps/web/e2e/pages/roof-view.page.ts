import { expect, type Locator, type Page } from "@playwright/test";

export type LayerKey = "satellite" | "facets" | "edges" | "measurements" | "panels";

/**
 * The roof workspace: layer toggles, facet cards and the Konva stage.
 *
 * Konva renders to a single `<canvas>`, so its contents are not addressable
 * from the DOM. Rather than pixel-sniffing, the assertions here use the DOM
 * state the workspace publishes (`aria-pressed`, `data-panel-count`) and leave
 * true geometry to `helpers/geometry.ts` working on the API's own polygons.
 */
export class RoofViewPage {
  readonly stage: Locator;
  readonly canvas: Locator;
  readonly busyOverlay: Locator;
  readonly imageryBadge: Locator;

  constructor(readonly page: Page) {
    this.stage = page.getByTestId("roof-stage");
    this.canvas = this.stage.locator("canvas").first();
    this.busyOverlay = page.getByTestId("analysis-busy");
    this.imageryBadge = page.getByTestId("imagery-source-badge");
  }

  layerToggle(key: LayerKey): Locator {
    return this.page.getByTestId(`layer-toggle-${key}`);
  }

  async isLayerOn(key: LayerKey): Promise<boolean> {
    return (await this.layerToggle(key).getAttribute("aria-pressed")) === "true";
  }

  async toggleLayer(key: LayerKey): Promise<boolean> {
    const before = await this.isLayerOn(key);
    await this.layerToggle(key).click();
    await expect(this.layerToggle(key)).toHaveAttribute(
      "aria-pressed",
      String(!before),
    );
    return !before;
  }

  facetCard(facetId: string): Locator {
    return this.page.getByTestId(`facet-card-${facetId}`);
  }

  async panelCount(facetId: string): Promise<number> {
    const value = await this.facetCard(facetId).getAttribute("data-panel-count");
    return Number(value ?? "0");
  }

  /** Every facet card's panel count, keyed by facet id. */
  async panelCounts(): Promise<Record<string, number>> {
    const cards = this.page.locator("[data-testid^='facet-card-']");
    const counts: Record<string, number> = {};
    for (const card of await cards.all()) {
      const testId = await card.getAttribute("data-testid");
      const count = await card.getAttribute("data-panel-count");
      if (testId) counts[testId.replace("facet-card-", "")] = Number(count ?? "0");
    }
    return counts;
  }

  async selectFacet(facetId: string): Promise<void> {
    await this.facetCard(facetId).click();
    await expect(this.facetCard(facetId)).toHaveAttribute("aria-pressed", "true");
  }

  /** True once Konva has drawn something other than a blank stage. */
  async stageHasContent(): Promise<boolean> {
    return this.canvas.evaluate((node) => {
      const canvas = node as HTMLCanvasElement;
      const context = canvas.getContext("2d");
      if (!context || canvas.width === 0) return false;
      const { data } = context.getImageData(0, 0, canvas.width, canvas.height);
      // Any non-transparent pixel means the layer painted.
      for (let i = 3; i < data.length; i += 4 * 97) {
        if (data[i]! > 0) return true;
      }
      return false;
    });
  }
}
