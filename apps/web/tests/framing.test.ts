/**
 * Framing the roof for the proposal image.
 *
 * The defect this exists to prevent shipped a proposal to a customer showing
 * bare satellite imagery with no panels on it. The overlay was drawn correctly
 * — just outside the picture — because the framing maths multiplied source
 * pixels by the display scale and dropped the raster's letterbox offset. Then
 * the zoom multiplied the error.
 *
 * That is invisible to any assertion about *what* is drawn, and it is why
 * these tests are about where a known point *lands* rather than about the
 * numbers the function returns.
 */

import { describe, expect, it } from "vitest";

import { frameSourcePoints, toStagePixel } from "@/lib/framing";
import { rasterTransform } from "@/lib/raster";

const RASTER = { sourceWidthPx: 1280, sourceHeightPx: 1280 };

/** A wide stage, so the square raster is letterboxed and offsetX is non-zero. */
const WIDE = { width: 700, height: 520 };

/** The case roof: a rotated rectangle near the centre of the raster. */
const ROOF = [
  { x: 560, y: 600 },
  { x: 740, y: 570 },
  { x: 760, y: 690 },
  { x: 580, y: 720 },
];

function centreOf(points: { x: number; y: number }[]) {
  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  return {
    x: (Math.min(...xs) + Math.max(...xs)) / 2,
    y: (Math.min(...ys) + Math.max(...ys)) / 2,
  };
}

describe("frameSourcePoints", () => {
  it("puts the roof's centre at the centre of the frame", () => {
    const transform = rasterTransform(RASTER, WIDE);
    const view = frameSourcePoints({
      points: ROOF,
      transform,
      viewport: WIDE,
      padRatio: 0.03,
      maxZoom: 12,
    })!;

    const landed = ROOF.map((p) => toStagePixel(p, transform, view));
    const centre = centreOf(landed);

    expect(centre.x).toBeCloseTo(WIDE.width / 2, 6);
    expect(centre.y).toBeCloseTo(WIDE.height / 2, 6);
  });

  it("survives a letterboxed raster, which is the case that broke", () => {
    // A square raster in a wide stage renders 520 wide with offsetX = 90.
    const transform = rasterTransform(RASTER, WIDE);
    expect(transform.offsetX).toBeGreaterThan(0);

    const view = frameSourcePoints({
      points: ROOF,
      transform,
      viewport: WIDE,
      padRatio: 0.03,
      maxZoom: 12,
    })!;

    // Every corner has to be inside the frame. Dropping the offset put them
    // hundreds of pixels outside it once the zoom was applied.
    for (const corner of ROOF) {
      const { x, y } = toStagePixel(corner, transform, view);
      expect(x).toBeGreaterThanOrEqual(0);
      expect(x).toBeLessThanOrEqual(WIDE.width);
      expect(y).toBeGreaterThanOrEqual(0);
      expect(y).toBeLessThanOrEqual(WIDE.height);
    }
  });

  it("centres correctly on a tall stage too, where the offset is vertical", () => {
    const tall = { width: 420, height: 900 };
    const transform = rasterTransform(RASTER, tall);
    expect(transform.offsetY).toBeGreaterThan(0);

    const view = frameSourcePoints({
      points: ROOF,
      transform,
      viewport: tall,
      padRatio: 0.03,
      maxZoom: 12,
    })!;

    const centre = centreOf(ROOF.map((p) => toStagePixel(p, transform, view)));
    expect(centre.x).toBeCloseTo(tall.width / 2, 6);
    expect(centre.y).toBeCloseTo(tall.height / 2, 6);
  });

  it("fills the frame rather than leaving the roof small", () => {
    const transform = rasterTransform(RASTER, WIDE);
    const view = frameSourcePoints({
      points: ROOF,
      transform,
      viewport: WIDE,
      padRatio: 0.03,
      maxZoom: 12,
    })!;

    const landed = ROOF.map((p) => toStagePixel(p, transform, view));
    const spanX = Math.max(...landed.map((p) => p.x)) - Math.min(...landed.map((p) => p.x));
    const spanY = Math.max(...landed.map((p) => p.y)) - Math.min(...landed.map((p) => p.y));

    // At least 85% of one axis: the roof is the subject, not a detail in it.
    const filled = Math.max(spanX / WIDE.width, spanY / WIDE.height);
    expect(filled).toBeGreaterThan(0.85);
  });

  it("the capture frames tighter than the on-screen button", () => {
    const transform = rasterTransform(RASTER, WIDE);
    const onScreen = frameSourcePoints({
      points: ROOF,
      transform,
      viewport: WIDE,
      padRatio: 0.08,
      maxZoom: 6,
    })!;
    const forExport = frameSourcePoints({
      points: ROOF,
      transform,
      viewport: WIDE,
      padRatio: 0.03,
      maxZoom: 12,
    })!;

    expect(forExport.scale).toBeGreaterThan(onScreen.scale);
  });

  it("returns null rather than a nonsense view when there is no roof", () => {
    const transform = rasterTransform(RASTER, WIDE);
    expect(
      frameSourcePoints({
        points: [],
        transform,
        viewport: WIDE,
        padRatio: 0.03,
        maxZoom: 12,
      }),
    ).toBeNull();
  });
});
