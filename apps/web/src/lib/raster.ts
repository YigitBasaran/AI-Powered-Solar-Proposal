/**
 * The one transform from source-raster pixels to screen pixels.
 *
 * Calibration lives in source-map pixels; Konva draws in stage pixels. Every
 * misalignment in this system is ultimately a disagreement about that mapping,
 * so there is exactly one implementation of it and both the satellite image and
 * the overlay are placed with it. Two implementations would drift, and the
 * drift would look like a calibration error rather than a rendering one.
 *
 * Three things this deliberately does *not* do:
 *
 * - It never derives scale from the loaded image's pixel dimensions. Ground
 *   resolution comes from Web Mercator and the verified zoom/scale, published
 *   by `/api/v1/maps/config`. An image may have been re-encoded or resized and
 *   its dimensions carry no reliable scale information.
 * - It never assumes the raster is square. `sourceHeightPx` used to be ignored
 *   entirely, with the width substituted for both axes, so a non-square raster
 *   would have been stretched on one axis while the overlay stayed isotropic -
 *   which is precisely the "translated *and* scaled" symptom.
 * - It never letterboxes silently. The scale is uniform across both axes, so
 *   the image and the overlay cannot shear relative to one another; any leftover
 *   space is explicit padding, not a hidden offset.
 */

export type RasterContract = {
  sourceWidthPx: number;
  sourceHeightPx: number;
};

export type Viewport = {
  /** Width of the box the stage is rendered into, in CSS pixels. */
  width: number;
  /** Height of that box, in CSS pixels. */
  height: number;
};

export type RasterTransform = {
  /** Uniform source-pixel -> CSS-pixel factor. */
  scale: number;
  /** Where the raster's top-left corner sits in the stage, in CSS pixels. */
  offsetX: number;
  offsetY: number;
  /** The rendered size of the whole raster, in CSS pixels. */
  renderedWidth: number;
  renderedHeight: number;
  /** Map a point in source pixels to stage pixels. */
  toScreen: (p: { x: number; y: number }) => { x: number; y: number };
  /** The inverse, for a cursor readout during calibration. */
  toSource: (p: { x: number; y: number }) => { x: number; y: number };
};

/**
 * Fit the whole raster inside the viewport, centred, preserving aspect ratio.
 *
 * `contain` rather than `cover`: cropping the raster would hide part of the
 * roof, and a roof that runs off the edge of the canvas is the kind of thing
 * that gets mistaken for a bad calibration.
 */
export function rasterTransform(raster: RasterContract, viewport: Viewport): RasterTransform {
  const sw = Math.max(raster.sourceWidthPx, 1);
  const sh = Math.max(raster.sourceHeightPx, 1);

  const scale = Math.min(viewport.width / sw, viewport.height / sh);
  const renderedWidth = sw * scale;
  const renderedHeight = sh * scale;

  const offsetX = (viewport.width - renderedWidth) / 2;
  const offsetY = (viewport.height - renderedHeight) / 2;

  return {
    scale,
    offsetX,
    offsetY,
    renderedWidth,
    renderedHeight,
    toScreen: (p) => ({ x: offsetX + p.x * scale, y: offsetY + p.y * scale }),
    toSource: (p) => ({ x: (p.x - offsetX) / scale, y: (p.y - offsetY) / scale }),
  };
}

/**
 * Does the raster that arrived match the contract the overlay is drawn against?
 *
 * Nothing checked this before, which is why a raster could differ from the
 * published contract and simply be stretched to fit - silently, and looking for
 * all the world like a calibration problem. Reported rather than corrected: a
 * mismatch means one of the two is wrong, and guessing which would be how the
 * next silent misalignment starts.
 */
export function rasterMatchesContract(
  image: { naturalWidth: number; naturalHeight: number },
  contract: RasterContract,
): { ok: boolean; detail?: string } {
  const { naturalWidth: w, naturalHeight: h } = image;
  if (w === contract.sourceWidthPx && h === contract.sourceHeightPx) return { ok: true };
  return {
    ok: false,
    detail:
      `the satellite raster is ${w}x${h}px but the published contract says ` +
      `${contract.sourceWidthPx}x${contract.sourceHeightPx}px, so measurements ` +
      `drawn on it cannot be trusted`,
  };
}

/**
 * Device pixel ratio, clamped and safe on the server.
 *
 * Konva multiplies its backing canvas by this. Left to default it follows the
 * window, so moving the browser between a retina and a non-retina display
 * re-rasterises at a different density; pinning it keeps the exported layout
 * snapshot identical wherever it was produced. Clamped because some virtual
 * displays report absurd values and a 6x backing store is a memory problem, not
 * a sharpness one.
 */
export function stagePixelRatio(): number {
  if (typeof window === "undefined") return 1;
  return Math.min(Math.max(window.devicePixelRatio || 1, 1), 3);
}
