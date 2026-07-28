import type { Point } from "../fixtures/api";

/**
 * Polygon geometry for panel-placement assertions.
 *
 * Deliberately implemented here rather than imported from the backend: a test
 * that reuses the production containment routine proves only that the routine
 * agrees with itself. These are independent, textbook implementations
 * (ray casting, SAT) working on the polygons the backend actually produced.
 */

const EPSILON = 1e-9;

export function polygonArea(polygon: Point[]): number {
  let total = 0;
  for (let i = 0; i < polygon.length; i += 1) {
    const a = polygon[i]!;
    const b = polygon[(i + 1) % polygon.length]!;
    total += a.x * b.y - b.x * a.y;
  }
  return Math.abs(total) / 2;
}

export function centroid(polygon: Point[]): Point {
  const sum = polygon.reduce((acc, p) => ({ x: acc.x + p.x, y: acc.y + p.y }), { x: 0, y: 0 });
  return { x: sum.x / polygon.length, y: sum.y / polygon.length };
}

/**
 * Ray casting, with a point within `edgeTolerance` of an edge counted as
 * inside.
 *
 * The tolerance is not slack in the assertion — it is the API's own coordinate
 * precision. Polygons are published rounded to 2 decimal places of a source
 * pixel, so a panel placed exactly flush with a facet edge can land up to
 * ~0.005 px outside it. Measured worst case on the real payload: 0.0037 px,
 * which is 0.23 mm of roof. The default below is five times that, and still
 * five orders of magnitude tighter than any placement error that matters.
 */
export const ROUNDING_TOLERANCE_PX = 0.02;

export function pointInPolygon(
  point: Point,
  polygon: Point[],
  edgeTolerance = ROUNDING_TOLERANCE_PX,
): boolean {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i, i += 1) {
    const a = polygon[i]!;
    const b = polygon[j]!;

    if (distanceToSegment(point, a, b) <= edgeTolerance) return true;

    const intersects =
      a.y > point.y !== b.y > point.y &&
      point.x < ((b.x - a.x) * (point.y - a.y)) / (b.y - a.y) + a.x;
    if (intersects) inside = !inside;
  }
  return inside;
}

export function distanceToSegment(p: Point, a: Point, b: Point): number {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const lengthSq = dx * dx + dy * dy;
  if (lengthSq < EPSILON) return Math.hypot(p.x - a.x, p.y - a.y);
  let t = ((p.x - a.x) * dx + (p.y - a.y) * dy) / lengthSq;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy));
}

/**
 * Every vertex of `inner` inside `outer`.
 *
 * Vertices alone would miss a panel bulging across a concave hip, so the edge
 * midpoints are checked too — enough for the convex facets this roof has,
 * without pulling in a full clipping library.
 */
export function polygonContains(
  outer: Point[],
  inner: Point[],
  edgeTolerance = ROUNDING_TOLERANCE_PX,
): boolean {
  const probes: Point[] = [...inner];
  for (let i = 0; i < inner.length; i += 1) {
    const a = inner[i]!;
    const b = inner[(i + 1) % inner.length]!;
    probes.push({ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 });
  }
  return probes.every((p) => pointInPolygon(p, outer, edgeTolerance));
}

/** How far outside `outer` the worst point of `inner` sits (0 when contained). */
export function escapeDistance(outer: Point[], inner: Point[]): number {
  let worst = 0;
  const probes: Point[] = [...inner];
  for (let i = 0; i < inner.length; i += 1) {
    const a = inner[i]!;
    const b = inner[(i + 1) % inner.length]!;
    probes.push({ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 });
  }
  for (const p of probes) {
    if (pointInPolygon(p, outer, 0)) continue;
    let nearest = Infinity;
    for (let i = 0; i < outer.length; i += 1) {
      nearest = Math.min(nearest, distanceToSegment(p, outer[i]!, outer[(i + 1) % outer.length]!));
    }
    worst = Math.max(worst, nearest);
  }
  return worst;
}

/** Separating-axis overlap test for two convex polygons. */
export function polygonsOverlap(a: Point[], b: Point[], tolerance = 1e-6): boolean {
  for (const polygon of [a, b]) {
    for (let i = 0; i < polygon.length; i += 1) {
      const p1 = polygon[i]!;
      const p2 = polygon[(i + 1) % polygon.length]!;
      const axis = { x: -(p2.y - p1.y), y: p2.x - p1.x };
      const length = Math.hypot(axis.x, axis.y);
      if (length < EPSILON) continue;
      const normal = { x: axis.x / length, y: axis.y / length };

      const projA = project(a, normal);
      const projB = project(b, normal);
      // A gap on any axis proves separation.
      if (projA.max <= projB.min + tolerance || projB.max <= projA.min + tolerance) {
        return false;
      }
    }
  }
  return true;
}

function project(polygon: Point[], axis: Point): { min: number; max: number } {
  let min = Infinity;
  let max = -Infinity;
  for (const p of polygon) {
    const value = p.x * axis.x + p.y * axis.y;
    min = Math.min(min, value);
    max = Math.max(max, value);
  }
  return { min, max };
}

/** Shortest distance between two non-overlapping polygons. */
export function polygonDistance(a: Point[], b: Point[]): number {
  let best = Infinity;
  for (const p of a) {
    for (let i = 0; i < b.length; i += 1) {
      best = Math.min(best, distanceToSegment(p, b[i]!, b[(i + 1) % b.length]!));
    }
  }
  for (const p of b) {
    for (let i = 0; i < a.length; i += 1) {
      best = Math.min(best, distanceToSegment(p, a[i]!, a[(i + 1) % a.length]!));
    }
  }
  return best;
}

/** Stable serialisation for determinism comparison. */
export function serialiseLayout(
  facets: { facetId: string; orientation: string; panels: { sourcePixelPolygon: Point[] }[] }[],
): string {
  return JSON.stringify(
    [...facets]
      .sort((x, y) => x.facetId.localeCompare(y.facetId))
      .map((facet) => ({
        facetId: facet.facetId,
        orientation: facet.orientation,
        panels: facet.panels.map((panel) =>
          panel.sourcePixelPolygon.map((p) => [round(p.x), round(p.y)]),
        ),
      })),
  );
}

function round(value: number): number {
  return Math.round(value * 100) / 100;
}
