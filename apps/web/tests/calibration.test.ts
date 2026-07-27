import { describe, expect, it } from "vitest";

import {
  type CalVertex,
  type CalibrationState,
  edgeLengthM,
  facetAreaM2,
  fromCalibrationJson,
  toCalibrationJson,
  validate,
} from "@/components/roof/calibrationTypes";

const M_PER_PX = 0.06185;

function vertex(id: string, x: number, y: number): CalVertex {
  return { id, x, y };
}

/** A minimal but valid hipped roof, in source-map pixels. */
const ROOF: CalibrationState = {
  vertices: [
    vertex("v_corner_a", 0, 0),
    vertex("v_corner_b", 200, 0),
    vertex("v_corner_c", 200, 100),
    vertex("v_corner_d", 0, 100),
    vertex("v_ridge_0", 50, 50),
    vertex("v_ridge_1", 150, 50),
  ],
  edges: [
    { id: "eave_0", startVertexId: "v_corner_a", endVertexId: "v_corner_b", edgeType: "eave" },
    { id: "eave_1", startVertexId: "v_corner_b", endVertexId: "v_corner_c", edgeType: "eave" },
    { id: "eave_2", startVertexId: "v_corner_c", endVertexId: "v_corner_d", edgeType: "eave" },
    { id: "eave_3", startVertexId: "v_corner_d", endVertexId: "v_corner_a", edgeType: "eave" },
    { id: "hip_0", startVertexId: "v_corner_a", endVertexId: "v_ridge_0", edgeType: "hip" },
    { id: "ridge_0", startVertexId: "v_ridge_0", endVertexId: "v_ridge_1", edgeType: "ridge" },
  ],
  facets: [
    { id: "facet_n", label: "North", vertexIds: ["v_corner_a", "v_corner_b", "v_ridge_1", "v_ridge_0"], eaveEdgeId: "eave_0" },
    { id: "facet_e", label: "East", vertexIds: ["v_corner_b", "v_corner_c", "v_ridge_1"], eaveEdgeId: "eave_1" },
    { id: "facet_s", label: "South", vertexIds: ["v_corner_c", "v_corner_d", "v_ridge_0", "v_ridge_1"], eaveEdgeId: "eave_2" },
    { id: "facet_w", label: "West", vertexIds: ["v_corner_d", "v_corner_a", "v_ridge_0"], eaveEdgeId: "eave_3" },
  ],
};

describe("measurements", () => {
  it("converts pixel distance to metres using the published scale", () => {
    const a = vertex("a", 0, 0);
    const b = vertex("b", 100, 0);
    expect(edgeLengthM(a, b, M_PER_PX)).toBeCloseTo(6.185, 3);
  });

  it("measures a diagonal correctly", () => {
    expect(edgeLengthM(vertex("a", 0, 0), vertex("b", 3, 4), M_PER_PX)).toBeCloseTo(5 * M_PER_PX, 6);
  });

  it("returns null rather than guessing when a vertex is missing", () => {
    expect(edgeLengthM(undefined, vertex("b", 1, 1), M_PER_PX)).toBeNull();
  });

  it("computes facet area by the shoelace formula", () => {
    const vertices = new Map(ROOF.vertices.map((v) => [v.id, v]));
    // North facet: trapezoid with parallel sides 200 and 100 px, height 50 px
    // => (200 + 100) / 2 * 50 = 7,500 px².
    const expected = 7500 * M_PER_PX * M_PER_PX;
    expect(facetAreaM2(ROOF.facets[0]!, vertices, M_PER_PX)).toBeCloseTo(expected, 6);
  });

  it("returns null for a degenerate facet", () => {
    const vertices = new Map(ROOF.vertices.map((v) => [v.id, v]));
    const bad = { id: "x", label: "x", vertexIds: ["v_corner_a"], eaveEdgeId: null };
    expect(facetAreaM2(bad, vertices, M_PER_PX)).toBeNull();
  });
});

describe("validation", () => {
  it("accepts a complete roof", () => {
    expect(validate(ROOF)).toEqual([]);
  });

  it("rejects a roof without four facets", () => {
    const problems = validate({ ...ROOF, facets: ROOF.facets.slice(0, 2) });
    expect(problems.some((p) => p.includes("4 facets"))).toBe(true);
  });

  it("rejects a facet with no eave assigned", () => {
    const facets = ROOF.facets.map((f, i) => (i === 0 ? { ...f, eaveEdgeId: null } : f));
    expect(validate({ ...ROOF, facets }).some((p) => p.includes("no eave edge"))).toBe(true);
  });

  it("rejects a facet whose eave edge does not exist", () => {
    const facets = ROOF.facets.map((f, i) => (i === 0 ? { ...f, eaveEdgeId: "nope" } : f));
    expect(validate({ ...ROOF, facets }).some((p) => p.includes("missing eave edge"))).toBe(true);
  });

  it("rejects a duplicate vertex id", () => {
    const state = { ...ROOF, vertices: [...ROOF.vertices, vertex("v_corner_a", 9, 9)] };
    expect(validate(state).some((p) => p.includes("Duplicate vertex"))).toBe(true);
  });

  it("requires a ridge", () => {
    const edges = ROOF.edges.filter((e) => e.edgeType !== "ridge");
    expect(validate({ ...ROOF, edges }).some((p) => p.includes("No ridge"))).toBe(true);
  });
});

describe("JSON round-trip", () => {
  const meta = {
    sourceWidthPx: 1280,
    sourceHeightPx: 1280,
    metresPerPixel: M_PER_PX,
    pitchDeg: 25,
  };

  it("exports the committed calibration shape", () => {
    const json = toCalibrationJson(ROOF, meta) as Record<string, any>;
    expect(json.pitch_deg).toBe(25);
    expect(json.source_raster.width_px).toBe(1280);
    expect(json.vertices[0]).toEqual({
      id: "v_corner_a",
      source_pixel: { x: 0, y: 0 },
    });
    expect(json.edges[0].edge_type).toBe("eave");
    expect(json.facets[0].eave_edge_id).toBe("eave_0");
  });

  it("survives an export/import round trip unchanged", () => {
    const restored = fromCalibrationJson(toCalibrationJson(ROOF, meta));
    expect(restored.vertices).toEqual(ROOF.vertices);
    expect(restored.edges).toEqual(ROOF.edges);
    expect(restored.facets.map((f) => f.id)).toEqual(ROOF.facets.map((f) => f.id));
    expect(restored.facets[0]!.eaveEdgeId).toBe("eave_0");
  });

  it("rejects a document with no vertices", () => {
    expect(() => fromCalibrationJson({})).toThrow(/no vertices/i);
  });

  it("rejects an edge referencing a vertex that does not exist", () => {
    expect(() =>
      fromCalibrationJson({
        vertices: [{ id: "a", source_pixel: { x: 0, y: 0 } }],
        edges: [{ id: "e", start_vertex_id: "a", end_vertex_id: "ghost", edge_type: "eave" }],
      }),
    ).toThrow(/does not exist/);
  });

  it("rejects an unknown edge type", () => {
    expect(() =>
      fromCalibrationJson({
        vertices: [
          { id: "a", source_pixel: { x: 0, y: 0 } },
          { id: "b", source_pixel: { x: 1, y: 1 } },
        ],
        edges: [{ id: "e", start_vertex_id: "a", end_vertex_id: "b", edge_type: "gutter" }],
      }),
    ).toThrow(/unknown type/);
  });

  it("rejects a vertex without source_pixel coordinates", () => {
    expect(() => fromCalibrationJson({ vertices: [{ id: "a" }] })).toThrow(/source_pixel/);
  });

  it("exported coordinates are source-map pixels, not viewport pixels", () => {
    // The regression this whole coordinate discipline exists for: an export
    // must never depend on pan, zoom or canvas size.
    const json = toCalibrationJson(ROOF, meta) as Record<string, any>;
    const xs = json.vertices.map((v: any) => v.source_pixel.x);
    expect(Math.max(...xs)).toBe(200);
    expect(Math.min(...xs)).toBe(0);
  });
});
