"""Build the measured roof model from committed calibration data.

The calibration file stores only what a human (or the derivation script) can
actually observe: vertex positions in source-map pixels, edge classifications
and facet membership. Everything derived - lengths, areas, azimuths, PVGIS
aspects, vertex heights - is computed here from that raw input plus the
verified raster configuration, so a change to either shows up everywhere at
once rather than drifting out of a stale duplicate.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.config import Settings, get_settings
from app.core.errors import RoofCalibrationMismatchError, RoofCalibrationMissingError
from app.domain.geometry import (
    SurfaceFrame,
    build_surface_frame,
    compass_azimuth_to_pvgis_aspect,
    facet_compass_azimuth,
    polygon_area_m2,
    projected_length_m,
    sloped_area_m2,
    source_pixel_to_metric,
    true_3d_length_m,
)
from app.domain.imagery import describe_request, request_signature
from app.domain.models import (
    Point2D,
    RoofEdge,
    RoofEdgeType,
    RoofFacet,
    RoofModel,
    RoofVertex,
    cos_pitch,
)

_DATA = Path(__file__).resolve().parents[1] / "data"

#: The calibration the runtime measures from.
#:
#: Traced against live Google Static Maps imagery. The older
#: `fixed_roof_calibration.json` was traced on an Esri raster re-projected onto
#: Google's grid; it is retained only as test-replay data, and the signature
#: guard refuses it at runtime because it records no imagery provenance.
CALIBRATION_PATH = _DATA / "google_roof_calibration.json"

#: The superseded Esri tracing. Tests only.
ESRI_CALIBRATION_PATH = _DATA / "fixed_roof_calibration.json"


def _load_calibration(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RoofCalibrationMissingError(
            f"Calibration file not found at {path}. "
            "Run scripts/derive_roof_calibration.py --write to generate it."
        )
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RoofCalibrationMissingError(f"Calibration file is not valid JSON: {exc}") from exc

    for key in ("vertices", "edges", "facets", "pitch_deg"):
        if key not in data:
            raise RoofCalibrationMissingError(f"Calibration file is missing {key!r}")
    return data


def load_calibration(
    settings: Settings | None = None, *, calibration_path: Path | None = None
) -> dict[str, Any]:
    """The raw calibration document, without building the model.

    Exposed for callers that need only its provenance - the maps endpoint asks
    which imagery it was traced on, and has no use for the geometry.
    """
    return _load_calibration(calibration_path or CALIBRATION_PATH)


def calibration_metadata(data: dict[str, Any]) -> dict[str, Any]:
    """The block binding this calibration to the imagery it was traced on."""
    meta = data.get("calibration_metadata")
    return meta if isinstance(meta, dict) else {}


def assert_calibration_matches(data: dict[str, Any], cfg: Any) -> None:
    """Refuse to measure against a raster the calibration does not describe.

    The old guard compared the raster *width* and nothing else, so a changed
    zoom, scale, centre or map type sailed through as long as the width still
    came to 1280 - and every one of those relocates or rescales the grid the
    stored pixels are expressed in. The signature covers all of them at once,
    and the error names the field that moved rather than saying "something".

    A calibration with no signature at all is refused too. Absent provenance is
    not agreement: that is exactly the state the Esri-derived file was in when
    live Google imagery was first served, and it rendered a confidently wrong
    roof rather than complaining.
    """
    meta = calibration_metadata(data)
    stored = meta.get("request_signature")
    current = request_signature(cfg)

    if not stored:
        raise RoofCalibrationMismatchError(
            "This calibration does not record which imagery configuration it was "
            "traced against, so it cannot be checked. Re-trace it with "
            "scripts/derive_roof_calibration.py, which writes the signature.",
            details={"expectedSignature": current},
        )

    if stored == current:
        return

    expected = meta.get("request", {})
    actual = describe_request(cfg)
    diverged = {
        field: {"calibration": expected.get(field), "configured": actual.get(field)}
        for field in actual
        if expected.get(field) != actual.get(field)
    }
    raise RoofCalibrationMismatchError(
        "The roof calibration was traced against a different imagery configuration: "
        + ", ".join(
            f"{f} {v['calibration']!r} -> {v['configured']!r}" for f, v in diverged.items()
        )
        + ". Re-trace the roof, or restore the configuration it was traced against.",
        details={
            "diverged": diverged,
            "calibrationSignature": stored,
            "configuredSignature": current,
        },
    )


def _ridge_height_m(short_side_m: float, pitch_deg: float) -> float:
    """Height of the ridge above the eave line for a uniform-pitch hip roof.

    The slope rises over half the short side, so ``rise = (short/2) * tan(pitch)``.
    Knowing this lets hip edges be measured with the true 3D formula instead of
    the pitch shortcut, which does not apply to them (A-GEO-1).
    """
    return (short_side_m / 2.0) * math.tan(math.radians(pitch_deg))


def build_roof_model(
    settings: Settings | None = None, *, calibration_path: Path | None = None
) -> RoofModel:
    settings = settings or get_settings()
    data = _load_calibration(calibration_path or CALIBRATION_PATH)

    cfg = settings.satellite_image_config
    m_per_px = cfg.ground_m_per_source_px
    src_w, src_h = cfg.source_width_px, cfg.source_height_px

    assert_calibration_matches(data, cfg)

    pitch = float(data["pitch_deg"])

    # --- vertices ---------------------------------------------------------
    pixels: dict[str, Point2D] = {}
    metrics: dict[str, Point2D] = {}
    for v in data["vertices"]:
        p = Point2D(x=float(v["source_pixel"]["x"]), y=float(v["source_pixel"]["y"]))
        pixels[v["id"]] = p
        metrics[v["id"]] = source_pixel_to_metric(
            p, source_width_px=src_w, source_height_px=src_h, ground_m_per_source_px=m_per_px
        )

    # --- vertex heights ---------------------------------------------------
    # Eave corners sit on the eave plane (0.0); ridge vertices sit one rise up.
    ridge_ids = {
        vid
        for e in data["edges"]
        if e["edge_type"] == RoofEdgeType.RIDGE.value
        for vid in (e["start_vertex_id"], e["end_vertex_id"])
    }
    short_side_m = float(data.get("measured", {}).get("footprint_short_m", 0.0))
    if short_side_m <= 0:
        eaves = [e for e in data["edges"] if e["edge_type"] == RoofEdgeType.EAVE.value]
        lengths = [
            projected_length_m(
                metrics[e["start_vertex_id"]],
                metrics[e["end_vertex_id"]],
                ground_m_per_source_px=1.0,
            )
            for e in eaves
        ]
        short_side_m = min(lengths) if lengths else 0.0
    rise = _ridge_height_m(short_side_m, pitch)

    heights = {vid: (rise if vid in ridge_ids else 0.0) for vid in pixels}

    vertices = [
        RoofVertex(
            id=vid,
            source_pixel=pixels[vid],
            projected_metric=metrics[vid],
            height_m=heights[vid],
        )
        for vid in pixels
    ]

    # --- edges ------------------------------------------------------------
    edges: list[RoofEdge] = []
    for e in data["edges"]:
        a, b = e["start_vertex_id"], e["end_vertex_id"]
        edge_type = RoofEdgeType(e["edge_type"])
        # Metric coordinates are already in metres, so the pixel scale is 1.
        projected = projected_length_m(metrics[a], metrics[b], ground_m_per_source_px=1.0)
        # True length uses endpoint heights. Deliberately NOT projected/cos(pitch):
        # a hip runs diagonally across the slope, not along it.
        true_len = true_3d_length_m(
            metrics[a], metrics[b], height_a_m=heights[a], height_b_m=heights[b]
        )
        edges.append(
            RoofEdge(
                id=e["id"],
                start_vertex_id=a,
                end_vertex_id=b,
                edge_type=edge_type,
                projected_length_m=projected,
                true_3d_length_m=true_len,
            )
        )

    edge_by_id = {e.id: e for e in edges}

    # --- facets -----------------------------------------------------------
    facets: list[RoofFacet] = []
    for f in data["facets"]:
        vids: list[str] = list(f["vertex_ids"])
        metric_poly = [metrics[v] for v in vids]
        pixel_poly = [pixels[v] for v in vids]

        eave_edge_id = f["eave_edge_id"]
        if eave_edge_id not in edge_by_id:
            raise RoofCalibrationMissingError(
                f"Facet {f['id']!r} references unknown eave edge {eave_edge_id!r}"
            )
        eave = edge_by_id[eave_edge_id]
        eave_a, eave_b = metrics[eave.start_vertex_id], metrics[eave.end_vertex_id]

        azimuth = facet_compass_azimuth(
            eave_start=eave_a, eave_end=eave_b, facet_polygon=metric_poly
        )
        projected_area = polygon_area_m2(metric_poly)

        facets.append(
            RoofFacet(
                id=f["id"],
                label=f["label"],
                vertex_ids=vids,
                source_pixel_polygon=pixel_poly,
                projected_metric_polygon=metric_poly,
                eave_edge_id=eave_edge_id,
                pitch_deg=pitch,
                compass_azimuth_deg=azimuth,
                pvgis_aspect_deg=compass_azimuth_to_pvgis_aspect(azimuth),
                projected_area_m2=projected_area,
                sloped_area_m2=sloped_area_m2(projected_area, pitch),
            )
        )

    total_projected = sum(f.projected_area_m2 for f in facets)

    return RoofModel(
        id=data.get("id", "case_fixed_roof"),
        vertices=vertices,
        edges=edges,
        facets=facets,
        pitch_deg=pitch,
        ground_m_per_source_px=m_per_px,
        source_width_px=src_w,
        source_height_px=src_h,
        total_projected_area_m2=total_projected,
        total_sloped_area_m2=sloped_area_m2(total_projected, pitch),
    )


def facet_surface_frame(roof: RoofModel, facet: RoofFacet) -> SurfaceFrame:
    """Surface frame for a facet, built from its assigned eave."""
    eave = roof.edge(facet.eave_edge_id)
    start = roof.vertex(eave.start_vertex_id).projected_metric
    end = roof.vertex(eave.end_vertex_id).projected_metric
    if start is None or end is None:
        raise RoofCalibrationMissingError("facet eave is missing metric coordinates")
    return build_surface_frame(
        eave_start=start,
        eave_end=end,
        facet_polygon=facet.projected_metric_polygon,
        pitch_deg=facet.pitch_deg,
    )


def facet_surface_polygon(roof: RoofModel, facet: RoofFacet) -> list[Point2D]:
    """Facet outline in its own surface coordinates (metres on the slope)."""
    return facet_surface_frame(roof, facet).polygon_to_surface(facet.projected_metric_polygon)


@lru_cache(maxsize=1)
def get_roof_model() -> RoofModel:
    """Cached roof model. The calibration is static for the case property."""
    return build_roof_model()


def roof_summary(roof: RoofModel) -> dict[str, Any]:
    """Compact, display-ready summary."""
    return {
        "id": roof.id,
        "pitchDeg": roof.pitch_deg,
        "groundMetresPerSourcePixel": round(roof.ground_m_per_source_px, 7),
        "totalProjectedAreaM2": round(roof.total_projected_area_m2, 2),
        "totalSlopedAreaM2": round(roof.total_sloped_area_m2, 2),
        "cosPitch": round(cos_pitch(roof.pitch_deg), 6),
        "facets": [
            {
                "id": f.id,
                "label": f.label,
                "shape": "trapezoid" if len(f.vertex_ids) == 4 else "triangle",
                "compassAzimuthDeg": round(f.compass_azimuth_deg, 2),
                "pvgisAspectDeg": round(f.pvgis_aspect_deg, 2),
                "projectedAreaM2": round(f.projected_area_m2, 2),
                "slopedAreaM2": round(f.sloped_area_m2, 2),
            }
            for f in roof.facets
        ],
        "edges": [
            {
                "id": e.id,
                "type": e.edge_type.value,
                "projectedLengthM": round(e.projected_length_m, 3),
                "true3dLengthM": round(e.true_3d_length_m, 3)
                if e.true_3d_length_m is not None
                else None,
            }
            for e in roof.edges
        ],
    }
