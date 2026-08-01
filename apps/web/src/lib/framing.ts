import type { Point } from "@/types/api";
import type { RasterTransform } from "@/lib/raster";

export type StageView = { scale: number; x: number; y: number };

export type Viewport = { width: number; height: number };

/**
 * The stage view that centres a set of source-map points and zooms them to
 * fill the frame.
 *
 * **It projects the points itself, and that is the whole point of the
 * function.** The version this replaced multiplied source pixels by the
 * raster's display scale and forgot the letterbox offset: a 1280×1280 raster
 * in a 700×520 stage renders 520 wide with `offsetX = 90`, so every computed
 * centre was 90 stage-pixels out — and then multiplied by the zoom. At ~5.6×
 * the roof left the frame entirely, and the proposal image went out showing
 * bare satellite imagery with the panels drawn just outside the picture.
 *
 * Taking a `RasterTransform` rather than a scale makes that mistake
 * unavailable: there is no way to pass the wrong coordinate space.
 */
export function frameSourcePoints({
  points,
  transform,
  viewport,
  padRatio,
  maxZoom,
  minZoom = 0.4,
}: {
  points: Point[];
  transform: RasterTransform;
  viewport: Viewport;
  /** Breathing room, as a fraction of the shorter viewport edge. */
  padRatio: number;
  maxZoom: number;
  minZoom?: number;
}): StageView | null {
  if (points.length === 0) return null;

  const projected = points.map((p) => transform.toScreen(p));
  const xs = projected.map((p) => p.x);
  const ys = projected.map((p) => p.y);

  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);

  const pad = Math.min(viewport.width, viewport.height) * padRatio;
  const scale = Math.min(
    (viewport.width - pad * 2) / Math.max(maxX - minX, 1),
    (viewport.height - pad * 2) / Math.max(maxY - minY, 1),
  );
  const clamped = Math.min(Math.max(scale, minZoom), maxZoom);

  return {
    scale: clamped,
    x: viewport.width / 2 - ((minX + maxX) / 2) * clamped,
    y: viewport.height / 2 - ((minY + maxY) / 2) * clamped,
  };
}

/** Where a source-map point lands on screen under a transform and a view. */
export function toStagePixel(
  point: Point,
  transform: RasterTransform,
  view: StageView,
): Point {
  const projected = transform.toScreen(point);
  return { x: view.x + projected.x * view.scale, y: view.y + projected.y * view.scale };
}
