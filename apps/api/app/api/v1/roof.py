"""The calibrated roof model."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings
from app.services.roof import build_roof_model, roof_summary

router = APIRouter(prefix="/roof", tags=["roof"])


@router.get("/fixed-model")
async def fixed_model(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """Full roof geometry in source-map pixels plus derived measurements.

    Source pixels are published, not viewport coordinates: the client applies
    its own display transform, so the same payload renders correctly at any
    canvas size.
    """
    roof = build_roof_model(settings)
    summary = roof_summary(roof)

    return {
        **summary,
        "sourceWidthPx": roof.source_width_px,
        "sourceHeightPx": roof.source_height_px,
        "vertices": [
            {
                "id": v.id,
                "sourcePixel": {"x": v.source_pixel.x, "y": v.source_pixel.y},
                "heightM": v.height_m,
            }
            for v in roof.vertices
        ],
        "edgeGeometry": [
            {
                "id": e.id,
                "type": e.edge_type.value,
                "startVertexId": e.start_vertex_id,
                "endVertexId": e.end_vertex_id,
                "projectedLengthM": round(e.projected_length_m, 3),
                "true3dLengthM": round(e.true_3d_length_m, 3)
                if e.true_3d_length_m is not None
                else None,
            }
            for e in roof.edges
        ],
        "facetGeometry": [
            {
                "id": f.id,
                "label": f.label,
                "vertexIds": f.vertex_ids,
                "eaveEdgeId": f.eave_edge_id,
                "sourcePixelPolygon": [
                    {"x": round(p.x, 2), "y": round(p.y, 2)} for p in f.source_pixel_polygon
                ],
            }
            for f in roof.facets
        ],
        "obstructionGeometry": [
            {
                "id": o.id,
                "label": o.label,
                "kind": o.kind,
                "facetId": o.facet_id,
                "sourcePixelPolygon": [
                    {"x": round(p.x, 2), "y": round(p.y, 2)} for p in o.source_pixel_polygon
                ],
            }
            for o in roof.obstructions
        ],
    }
