import { expect, test } from "./fixtures/proposal";
import { EXPECTED_ROOF } from "./fixtures/expected-values";

/**
 * The developer calibration tool, and the coordinate-space regression.
 *
 * ## The bug this exists for
 *
 * Window-relative pointer coordinates were once recorded as source-map pixels.
 * Every derived measurement still looked right — dimensions, aspect ratios and
 * azimuths are all *relative*, so a constant translation leaves them untouched.
 * Only the drawn overlay showed the roof sitting ~410 px away from the house.
 *
 * A screenshot diff would catch it, expensively and unreliably. Asserting the
 * transform directly catches it for what it is: a coordinate-space error.
 */

test.describe("@p0 roof calibration tool", () => {
  test("loads the committed calibration and reports it valid", async ({ calibration }) => {
    await calibration.open();

    expect(await calibration.issueCount()).toBe(0);
    expect(await calibration.sourceWidthPx()).toBe(EXPECTED_ROOF.sourceWidthPx);
    expect(await calibration.metresPerPixel()).toBeCloseTo(
      EXPECTED_ROOF.groundMetresPerSourcePixel,
      5,
    );

    const ids = await calibration.vertexIds();
    // Four eave corners plus two ridge ends: a hipped roof, not a gable.
    expect(ids).toHaveLength(6);
    expect(ids.filter((id) => id.includes("ridge"))).toHaveLength(2);
  });

  test("committed vertices sit inside the raster, near its centre", async ({ calibration }) => {
    await calibration.open();
    const size = await calibration.sourceWidthPx();

    for (const id of await calibration.vertexIds()) {
      const { x, y } = await calibration.vertex(id);
      expect(x, `${id}.x`).toBeGreaterThan(0);
      expect(x, `${id}.x`).toBeLessThan(size);
      expect(y, `${id}.y`).toBeGreaterThan(0);
      expect(y, `${id}.y`).toBeLessThan(size);

      // The raster is centred on the case coordinate, so the roof is near the
      // middle. The 410 px offset bug pushed the whole set toward one corner —
      // still "inside the image", which is why a bounds check alone missed it.
      expect(Math.hypot(x - size / 2, y - size / 2), `${id} is far from centre`).toBeLessThan(
        size * 0.25,
      );
    }
  });

  test("410 px regression: a pointer position round-trips to source pixels", async ({
    calibration,
  }) => {
    await calibration.open();

    // Hover a known source-map point. The readout must report that same point.
    // If window or container offsets leaked into the conversion, the readout
    // would differ by the canvas origin — which is exactly what happened.
    for (const probe of [
      { x: 640, y: 640 },
      { x: 556, y: 598 },
      { x: 900, y: 400 },
    ]) {
      const { reported, expected } = await calibration.probeSourcePoint(probe);
      const where = JSON.stringify(probe);

      // Exact: the conversion is pure arithmetic on the position used.
      expect(reported.x, `x for ${where}`).toBeCloseTo(expected.x, 3);
      expect(reported.y, `y for ${where}`).toBeCloseTo(expected.y, 3);

      // And the position used really was the point aimed at. A whole-pixel
      // mouse event on a canvas whose origin is at a fractional offset can
      // land up to half a device pixel out, which is ~0.7 source pixels here.
      expect(Math.abs(expected.x - probe.x), `aim x for ${where}`).toBeLessThan(1.5);
      expect(Math.abs(expected.y - probe.y), `aim y for ${where}`).toBeLessThan(1.5);
    }
  });

  test("410 px regression: resizing changes the display, never the data", async ({
    calibration,
    page,
  }) => {
    await calibration.open();

    const before: Record<string, { x: number; y: number }> = {};
    for (const id of await calibration.vertexIds()) before[id] = await calibration.vertex(id);

    const widthBefore = (await calibration.canvas.boundingBox())!.width;
    await page.setViewportSize({ width: 1024, height: 900 });
    await expect
      .poll(async () => (await calibration.canvas.boundingBox())!.width)
      .not.toBe(widthBefore);

    // Committed coordinates are source-map pixels, so a different viewport must
    // leave them untouched.
    for (const id of await calibration.vertexIds()) {
      expect(await calibration.vertex(id), `${id} moved when the window resized`).toEqual(
        before[id],
      );
    }

    // And the transform still round-trips at the new size.
    const { reported, expected } = await calibration.probeSourcePoint({ x: 640, y: 640 });
    expect(reported.x).toBeCloseTo(expected.x, 3);
    expect(reported.y).toBeCloseTo(expected.y, 3);
    expect(Math.abs(expected.x - 640)).toBeLessThan(1.5);
    expect(Math.abs(expected.y - 640)).toBeLessThan(1.5);
  });

  test("the committed geometry reproduces the published roof measurements", async ({
    calibration,
    api,
  }) => {
    await calibration.open();
    const mpp = await calibration.metresPerPixel();

    const corners = ["v_corner_a", "v_corner_b", "v_corner_c", "v_corner_d"];
    const ids = await calibration.vertexIds();
    for (const id of corners) expect(ids, `${id} missing`).toContain(id);

    const a = await calibration.vertex("v_corner_a");
    const b = await calibration.vertex("v_corner_b");
    const c = await calibration.vertex("v_corner_c");

    // The long and short footprint sides, measured from the DOM values and
    // converted with the published scale — no production code involved.
    const long = Math.hypot(b.x - a.x, b.y - a.y) * mpp;
    const short = Math.hypot(c.x - b.x, c.y - b.y) * mpp;
    expect(long).toBeCloseTo(EXPECTED_ROOF.footprintLongM, 2);
    expect(short).toBeCloseTo(EXPECTED_ROOF.footprintShortM, 2);

    // ...and the API publishes the same edges.
    const roof = await api.roofModel();
    const lengths = roof.edges
      .filter((e: { type: string }) => e.type === "eave")
      .map((e: { projectedLengthM: number }) => e.projectedLengthM)
      .sort((a: number, b: number) => a - b);
    expect(lengths[0]).toBeCloseTo(EXPECTED_ROOF.footprintShortM, 2);
    expect(lengths[3]).toBeCloseTo(EXPECTED_ROOF.footprintLongM, 2);
  });
});
