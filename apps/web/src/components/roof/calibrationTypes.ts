/**
 * Calibration editing types.
 *
 * Coordinates are SOURCE-MAP pixels throughout — the canonical raster defined
 * by the verified centre, zoom, size and scale. Nothing here ever holds a
 * viewport coordinate.
 */

export type EdgeType = "eave" | "hip" | "ridge";

export type Mode = "select" | "add" | "edge" | "facet" | "pan";

export type CalVertex = { id: string; x: number; y: number };

export type CalEdge = {
  id: string;
  startVertexId: string;
  endVertexId: string;
  edgeType: EdgeType;
};

export type CalFacet = {
  id: string;
  label: string;
  vertexIds: string[];
  eaveEdgeId: string | null;
};

export type CalibrationState = {
  vertices: CalVertex[];
  edges: CalEdge[];
  facets: CalFacet[];
};

export const EDGE_COLOUR: Record<EdgeType, string> = {
  eave: "#ffffff",
  hip: "#6ed7ff",
  ridge: "#ffc337",
};

export const MODE_HELP: Record<Mode, string> = {
  select: "Click a vertex to select it. Drag to move it.",
  add: "Click the image to place a vertex.",
  edge: "Click two vertices to connect them with the chosen edge type.",
  facet: "Click vertices in order around the facet, then Close facet.",
  pan: "Drag to pan. Scroll to zoom.",
};

/** Plan-view distance between two vertices, in metres. */
export function edgeLengthM(
  a: CalVertex | undefined,
  b: CalVertex | undefined,
  metresPerPixel: number,
): number | null {
  if (!a || !b) return null;
  return Math.hypot(b.x - a.x, b.y - a.y) * metresPerPixel;
}

/** Shoelace area of a facet, in square metres (plan view). */
export function facetAreaM2(
  facet: CalFacet,
  vertices: Map<string, CalVertex>,
  metresPerPixel: number,
): number | null {
  const points = facet.vertexIds
    .map((id) => vertices.get(id))
    .filter((v): v is CalVertex => Boolean(v));
  if (points.length < 3) return null;

  let total = 0;
  for (let i = 0; i < points.length; i += 1) {
    const p = points[i]!;
    const q = points[(i + 1) % points.length]!;
    total += p.x * q.y - q.x * p.y;
  }
  return (Math.abs(total) / 2) * metresPerPixel * metresPerPixel;
}

/**
 * Serialise to the committed calibration format.
 *
 * Deliberately the same shape `apps/api/app/data/fixed_roof_calibration.json`
 * uses, so an export can be dropped straight in.
 */
export function toCalibrationJson(
  state: CalibrationState,
  meta: { sourceWidthPx: number; sourceHeightPx: number; metresPerPixel: number; pitchDeg: number },
): unknown {
  return {
    id: "case_fixed_roof",
    description:
      "Hipped roof of the fixed case property. Coordinates are SOURCE-MAP " +
      "pixels on the source raster - never viewport or fixture-crop pixels.",
    derivation: "Edited in /dev/roof-calibration.",
    source_raster: {
      width_px: meta.sourceWidthPx,
      height_px: meta.sourceHeightPx,
      ground_m_per_source_px: meta.metresPerPixel,
    },
    pitch_deg: meta.pitchDeg,
    vertices: state.vertices.map((v) => ({
      id: v.id,
      source_pixel: { x: Number(v.x.toFixed(2)), y: Number(v.y.toFixed(2)) },
    })),
    edges: state.edges.map((e) => ({
      id: e.id,
      start_vertex_id: e.startVertexId,
      end_vertex_id: e.endVertexId,
      edge_type: e.edgeType,
    })),
    facets: state.facets.map((f) => ({
      id: f.id,
      label: f.label,
      vertex_ids: f.vertexIds,
      eave_edge_id: f.eaveEdgeId,
    })),
  };
}

type RawCalibration = {
  vertices?: { id: string; source_pixel: { x: number; y: number } }[];
  edges?: {
    id: string;
    start_vertex_id: string;
    end_vertex_id: string;
    edge_type: string;
  }[];
  facets?: { id: string; label?: string; vertex_ids: string[]; eave_edge_id?: string | null }[];
};

/** Parse committed-format JSON back into editable state. Throws on nonsense. */
export function fromCalibrationJson(raw: unknown): CalibrationState {
  const data = raw as RawCalibration;
  if (!data || typeof data !== "object" || !Array.isArray(data.vertices)) {
    throw new Error("Not a calibration document: no vertices array.");
  }

  const vertices: CalVertex[] = data.vertices.map((v) => {
    if (!v?.id || typeof v.source_pixel?.x !== "number" || typeof v.source_pixel?.y !== "number") {
      throw new Error(`Vertex ${String(v?.id)} is missing source_pixel coordinates.`);
    }
    return { id: v.id, x: v.source_pixel.x, y: v.source_pixel.y };
  });

  const known = new Set(vertices.map((v) => v.id));
  const edges: CalEdge[] = (data.edges ?? []).map((e) => {
    if (!known.has(e.start_vertex_id) || !known.has(e.end_vertex_id)) {
      throw new Error(`Edge ${e.id} references a vertex that does not exist.`);
    }
    if (!["eave", "hip", "ridge"].includes(e.edge_type)) {
      throw new Error(`Edge ${e.id} has unknown type "${e.edge_type}".`);
    }
    return {
      id: e.id,
      startVertexId: e.start_vertex_id,
      endVertexId: e.end_vertex_id,
      edgeType: e.edge_type as EdgeType,
    };
  });

  const facets: CalFacet[] = (data.facets ?? []).map((f) => ({
    id: f.id,
    label: f.label ?? f.id,
    vertexIds: f.vertex_ids ?? [],
    eaveEdgeId: f.eave_edge_id ?? null,
  }));

  return { vertices, edges, facets };
}

/** Problems that would make a calibration unusable downstream. */
export function validate(state: CalibrationState): string[] {
  const problems: string[] = [];
  const ids = new Set<string>();
  for (const vertex of state.vertices) {
    if (ids.has(vertex.id)) problems.push(`Duplicate vertex id "${vertex.id}".`);
    ids.add(vertex.id);
  }

  const edgeIds = new Set(state.edges.map((e) => e.id));
  const counts = { eave: 0, hip: 0, ridge: 0 };
  for (const edge of state.edges) counts[edge.edgeType] += 1;

  if (state.facets.length !== 4) {
    problems.push(`Expected 4 facets, found ${state.facets.length}.`);
  }
  if (counts.eave < 3) problems.push(`Expected at least 3 eave edges, found ${counts.eave}.`);
  if (counts.ridge < 1) problems.push("No ridge edge defined.");

  for (const facet of state.facets) {
    if (facet.vertexIds.length < 3) {
      problems.push(`Facet "${facet.id}" has fewer than 3 vertices.`);
    }
    if (!facet.eaveEdgeId) {
      problems.push(`Facet "${facet.id}" has no eave edge assigned.`);
    } else if (!edgeIds.has(facet.eaveEdgeId)) {
      problems.push(`Facet "${facet.id}" references a missing eave edge.`);
    }
  }

  return problems;
}
