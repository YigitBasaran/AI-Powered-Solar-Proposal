import { describe, expect, it } from "vitest";

import { rasterMatchesContract, rasterTransform, stagePixelRatio } from "@/lib/raster";

/**
 * The source-pixel -> screen-pixel transform.
 *
 * This is the mapping every misalignment in this system reduces to, so it is
 * pinned directly rather than only through a rendered component. The cases are
 * the ones that actually went wrong or nearly did: a non-square raster, a
 * container that changes size, and a raster whose real dimensions disagree with
 * the published contract.
 */

const SQUARE = { sourceWidthPx: 1280, sourceHeightPx: 1280 };

describe("rasterTransform", () => {
  it("fits the whole raster inside the viewport", () => {
    const t = rasterTransform(SQUARE, { width: 640, height: 640 });
    expect(t.scale).toBe(0.5);
    expect(t.renderedWidth).toBe(640);
    expect(t.renderedHeight).toBe(640);
    expect(t.offsetX).toBe(0);
    expect(t.offsetY).toBe(0);
  });

  it("round-trips a point at any container width", () => {
    // Resizing the browser must not move the overlay relative to the imagery.
    for (const width of [320, 640, 721, 900, 1440]) {
      const t = rasterTransform(SQUARE, { width, height: 520 });
      const source = { x: 556.03, y: 597.82 };
      const back = t.toSource(t.toScreen(source));
      expect(back.x).toBeCloseTo(source.x, 6);
      expect(back.y).toBeCloseTo(source.y, 6);
    }
  });

  it("keeps the roof in the same place relative to the image as the container grows", () => {
    // The regression this exists for: the overlay used to be scaled by
    // width/sourceWidth while the image was drawn as a square of the same
    // width, so the two agreed only while the container happened to be square.
    const vertex = { x: 640, y: 320 };
    const fractions = [320, 640, 1000].map((width) => {
      const t = rasterTransform(SQUARE, { width, height: 520 });
      const screen = t.toScreen(vertex);
      return {
        x: (screen.x - t.offsetX) / t.renderedWidth,
        y: (screen.y - t.offsetY) / t.renderedHeight,
      };
    });
    for (const f of fractions) {
      expect(f.x).toBeCloseTo(0.5, 9);
      expect(f.y).toBeCloseTo(0.25, 9);
    }
  });

  it("does not stretch a non-square raster", () => {
    // One GOOGLE_MAPS_SIZE away from being real. The old code used
    // sourceWidthPx for both axes, which shears the image against the overlay.
    const t = rasterTransform({ sourceWidthPx: 1280, sourceHeightPx: 640 }, { width: 640, height: 640 });
    expect(t.scale).toBe(0.5);
    expect(t.renderedWidth).toBe(640);
    expect(t.renderedHeight).toBe(320);
    // Uniform scale on both axes is what stops a shear.
    const a = t.toScreen({ x: 0, y: 0 });
    const b = t.toScreen({ x: 100, y: 100 });
    expect(b.x - a.x).toBeCloseTo(b.y - a.y, 9);
  });

  it("centres the leftover space instead of hiding it as an offset", () => {
    const t = rasterTransform({ sourceWidthPx: 1280, sourceHeightPx: 640 }, { width: 640, height: 640 });
    expect(t.offsetY).toBe(160);
    expect(t.offsetY * 2 + t.renderedHeight).toBe(640);
    // A source point at the raster's top-left lands on the padded edge, not at 0.
    expect(t.toScreen({ x: 0, y: 0 })).toEqual({ x: 0, y: 160 });
  });

  it("survives a degenerate viewport without producing NaN", () => {
    const t = rasterTransform(SQUARE, { width: 0, height: 0 });
    expect(Number.isFinite(t.scale)).toBe(true);
    expect(Number.isFinite(t.toScreen({ x: 10, y: 10 }).x)).toBe(true);
  });
});

describe("rasterMatchesContract", () => {
  it("accepts a raster of the published dimensions", () => {
    expect(rasterMatchesContract({ naturalWidth: 1280, naturalHeight: 1280 }, SQUARE).ok).toBe(true);
  });

  it("rejects scale=1 imagery served against a scale=2 contract", () => {
    // 640x640 covers the same ground as 1280x1280 at scale 2, so it would be
    // silently upscaled and look merely blurry - while every published
    // metres-per-pixel figure quietly referred to a different grid.
    const check = rasterMatchesContract({ naturalWidth: 640, naturalHeight: 640 }, SQUARE);
    expect(check.ok).toBe(false);
    expect(check.detail).toContain("640x640");
    expect(check.detail).toContain("1280x1280");
  });

  it("rejects a non-square raster against a square contract", () => {
    expect(rasterMatchesContract({ naturalWidth: 1280, naturalHeight: 1236 }, SQUARE).ok).toBe(false);
  });
});

describe("stagePixelRatio", () => {
  it("is clamped so a virtual display cannot demand an enormous backing store", () => {
    const ratio = stagePixelRatio();
    expect(ratio).toBeGreaterThanOrEqual(1);
    expect(ratio).toBeLessThanOrEqual(3);
  });
});
