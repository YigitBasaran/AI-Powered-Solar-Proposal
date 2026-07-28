import { expect, test } from "./fixtures/proposal";
import {
  CASE_INPUTS,
  EXPECTED,
  EXPECTED_FACET_YIELD,
  EXPECTED_ROOF,
  INVARIANTS,
} from "./fixtures/expected-values";
import {
  escapeDistance,
  polygonArea,
  polygonContains,
  polygonDistance,
  polygonsOverlap,
  serialiseLayout,
} from "./helpers/geometry";

/**
 * Panel placement, checked as geometry rather than as pixels.
 *
 * The analysis payload publishes every panel's polygon in source-map pixel
 * coordinates. Containment and overlap are therefore answered with real
 * polygon maths on the backend's own output — no screenshot heuristics, and no
 * reuse of the production containment routine, which would only prove that it
 * agrees with itself.
 */

test.describe("@p0 panel placement", () => {
  test("every panel lies inside its facet", async ({ api }) => {
    const { analysis } = await api.analysedProject("9.6 kWp");
    const geometry = new Map(
      (analysis.roof.facetGeometry ?? []).map((f) => [f.id, f.sourcePixelPolygon]),
    );
    expect(geometry.size, "facet geometry must be published").toBe(EXPECTED_ROOF.facetCount);

    let checked = 0;
    let worstEscape = 0;
    for (const facet of analysis.layout.facets) {
      const outline = geometry.get(facet.facetId);
      expect(outline, `no outline for ${facet.facetId}`).toBeDefined();
      for (const panel of facet.panels) {
        const escape = escapeDistance(outline!, panel.sourcePixelPolygon);
        worstEscape = Math.max(worstEscape, escape);
        expect(
          polygonContains(outline!, panel.sourcePixelPolygon),
          `${panel.id} escapes ${facet.facetId} by ${escape.toFixed(4)} px`,
        ).toBe(true);
        checked += 1;
      }
    }
    expect(checked).toBe(EXPECTED["9.6"].actualPanelCount);

    // The only permitted excursion is the API's 2 dp coordinate rounding. If
    // that ever grows, the placement — not the tolerance — is what changed.
    expect(worstEscape * analysis.roof.groundMetresPerSourcePixel * 1000).toBeLessThan(1);
  });

  test("no two panels overlap, and each keeps its gap", async ({ api }) => {
    const { analysis } = await api.analysedProject("9.6 kWp");
    const panels = analysis.layout.facets.flatMap((f) =>
      f.panels.map((p) => ({ ...p, facetId: f.facetId })),
    );
    expect(panels.length).toBe(24);

    for (let i = 0; i < panels.length; i += 1) {
      for (let j = i + 1; j < panels.length; j += 1) {
        const a = panels[i]!;
        const b = panels[j]!;
        expect(
          polygonsOverlap(a.sourcePixelPolygon, b.sourcePixelPolygon),
          `${a.id} overlaps ${b.id}`,
        ).toBe(false);

        // Neighbours on the same facet must also respect the 2 cm gap. In
        // source pixels at 0.06185 m/px that is ~0.32 px; allow for the 2 dp
        // rounding the API applies to coordinates.
        if (a.facetId === b.facetId) {
          expect(
            polygonDistance(a.sourcePixelPolygon, b.sourcePixelPolygon),
            `${a.id} and ${b.id} are touching`,
          ).toBeGreaterThan(0.05);
        }
      }
    }
  });

  test("panel polygons are the right physical size", async ({ api }) => {
    const { analysis } = await api.analysedProject("6 kWp");
    const mpp = analysis.roof.groundMetresPerSourcePixel;

    for (const facet of analysis.layout.facets) {
      for (const panel of facet.panels) {
        // A panel is 1 x 2 m on the slope. Seen from above it is foreshortened
        // along the slope direction by cos(pitch), so its plan area is
        // 2 m² x cos(25°) = 1.813 m².
        const planAreaM2 = polygonArea(panel.sourcePixelPolygon) * mpp * mpp;
        const expectedPlan = 2 * Math.cos(CASE_INPUTS.pitchDeg * (Math.PI / 180));
        expect(planAreaM2, `${panel.id} plan area`).toBeCloseTo(expectedPlan, 1);
      }
    }
  });

  test("the same request twice produces byte-identical geometry", async ({ api }) => {
    const first = await api.analysedProject("6 kWp");
    const second = await api.analysedProject("6 kWp");

    // Two independent projects, same inputs. Anything non-deterministic in
    // tiling, allocation or ordering shows up here as a diff.
    expect(serialiseLayout(second.analysis.layout.facets)).toBe(
      serialiseLayout(first.analysis.layout.facets),
    );
    expect(second.analysis.energy.totalAnnualProductionKwh).toBe(
      first.analysis.energy.totalAnnualProductionKwh,
    );
    expect(second.analysis.financial.annualSavingsEur).toBe(
      first.analysis.financial.annualSavingsEur,
    );
  });

  test("allocation follows production, not roof area", async ({ api }) => {
    const { analysis } = await api.analysedProject("6 kWp");
    const counts = new Map(analysis.layout.facets.map((f) => [f.facetId, f.panelCount]));

    // North and south are the same size to within a square centimetre...
    const areas = new Map(analysis.roof.facets.map((f) => [f.id, f.slopedAreaM2]));
    expect(areas.get("facet_n")).toBeCloseTo(areas.get("facet_s")!, 2);

    // ...yet 15 panels go 9 / 3 / 3 with south empty, because at -34° latitude
    // north (1679) > west (1515) > east (1367) > south (1120) kWh/kWp.
    expect(counts.get("facet_n")).toBe(9);
    expect(counts.get("facet_w")).toBe(3);
    expect(counts.get("facet_e")).toBe(3);
    expect(counts.get("facet_s") ?? 0).toBe(0);

    // Stated as a rule: no facet may hold panels while a strictly
    // better-yielding facet is below its own capacity.
    const yields = EXPECTED_FACET_YIELD as Record<string, number>;
    const capacity = EXPECTED_ROOF.facetCapacity as Record<string, number>;
    for (const [facetId, count] of counts) {
      if (count === 0) continue;
      for (const [otherId, otherYield] of Object.entries(yields)) {
        if (otherYield <= yields[facetId]!) continue;
        expect(
          counts.get(otherId) ?? 0,
          `${facetId} was filled while ${otherId} (higher yield) had room`,
        ).toBe(capacity[otherId]);
      }
    }
  });

  test("capacity and panel count stay consistent for every size", async ({ api }) => {
    for (const size of ["3.6 kWp", "6 kWp", "9.6 kWp"] as const) {
      const { analysis } = await api.analysedProject(size);
      const placed = analysis.layout.facets.reduce((sum, f) => sum + f.panelCount, 0);

      expect(placed).toBe(analysis.layout.placedPanelCount);
      expect(analysis.layout.facets.reduce((sum, f) => sum + f.panels.length, 0)).toBe(placed);
      expect(analysis.layout.feasibleSystemSizeKwp).toBeCloseTo(
        INVARIANTS.capacityFromCount(placed),
        6,
      );
      expect(analysis.energy.installedPowerKwp).toBeCloseTo(
        INVARIANTS.capacityFromCount(placed),
        6,
      );
    }
  });

  test("the roof measurements match the committed calibration", async ({ api }) => {
    const roof = await api.roofModel();

    expect(roof.facets).toHaveLength(EXPECTED_ROOF.facetCount);
    expect(roof.totalProjectedAreaM2).toBeCloseTo(EXPECTED_ROOF.totalProjectedAreaM2, 2);
    expect(roof.totalSlopedAreaM2).toBeCloseTo(EXPECTED_ROOF.totalSlopedAreaM2, 2);
    expect(roof.groundMetresPerSourcePixel).toBeCloseTo(
      EXPECTED_ROOF.groundMetresPerSourcePixel,
      5,
    );

    for (const [id, azimuth] of Object.entries(EXPECTED_ROOF.facetAzimuths)) {
      const facet = roof.facets.find((f: { id: string }) => f.id === id);
      expect(facet, `facet ${id} is missing`).toBeDefined();
      expect(facet.compassAzimuthDeg).toBeCloseTo(azimuth, 2);
    }
  });

  test("A-GEO-1: a hip is not the plan length divided by cos(pitch)", async ({ api }) => {
    const { analysis } = await api.analysedProject("6 kWp");
    const edges = analysis.roof.edges;

    expect(edges.filter((e) => e.type === "eave")).toHaveLength(EXPECTED_ROOF.eaveCount);
    expect(edges.filter((e) => e.type === "hip")).toHaveLength(EXPECTED_ROOF.hipCount);
    expect(edges.filter((e) => e.type === "ridge")).toHaveLength(EXPECTED_ROOF.ridgeCount);

    // Eaves and the ridge are level, so plan length *is* true length.
    for (const edge of edges.filter((e) => e.type !== "hip")) {
      expect(edge.true3dLengthM).toBeCloseTo(edge.projectedLengthM, 3);
    }

    for (const hip of edges.filter((e) => e.type === "hip")) {
      expect(hip.projectedLengthM).toBeCloseTo(EXPECTED_ROOF.hipProjectedLengthM, 2);
      // True 3-D endpoint geometry: 5.319 m, from √(5.051² + 1.665²).
      expect(hip.true3dLengthM!).toBeCloseTo(EXPECTED_ROOF.hipTrue3dLengthM, 2);
      // Not 5.573 m. The pitch correction applies only to an edge running
      // straight up the slope, which a hip does not.
      expect(hip.true3dLengthM!).not.toBeCloseTo(EXPECTED_ROOF.hipNaiveWrongLengthM, 2);
    }
  });
});
