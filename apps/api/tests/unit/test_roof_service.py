"""Roof model tests against the committed calibration.

These run on the real calibration file rather than a synthetic fixture: the
point is to catch a bad calibration, not only bad arithmetic.
"""

from __future__ import annotations

import math

import pytest

from app.domain.geometry import polygon_area_m2
from app.domain.models import RoofEdgeType
from app.services.roof import build_roof_model, facet_surface_polygon, roof_summary

PITCH = 25.0
COS_PITCH = math.cos(math.radians(PITCH))


@pytest.fixture(scope="module")
def roof():
    return build_roof_model()


# ---------------------------------------------------------------------------
# Topology
# ---------------------------------------------------------------------------


def test_roof_has_six_vertices(roof) -> None:
    assert len(roof.vertices) == 6


def test_roof_has_four_facets(roof) -> None:
    assert len(roof.facets) == 4


def test_roof_has_every_required_edge(roof) -> None:
    counts: dict[RoofEdgeType, int] = {}
    for e in roof.edges:
        counts[e.edge_type] = counts.get(e.edge_type, 0) + 1
    assert counts[RoofEdgeType.EAVE] == 4, "every outer eave must be present"
    assert counts[RoofEdgeType.HIP] == 4, "every internal hip must be present"
    assert counts[RoofEdgeType.RIDGE] == 1, "the central ridge must be present"
    assert len(roof.edges) == 9


def test_facet_shapes_are_two_trapezoids_and_two_triangles(roof) -> None:
    sizes = sorted(len(f.vertex_ids) for f in roof.facets)
    assert sizes == [3, 3, 4, 4]


def test_every_facet_references_a_real_eave_edge(roof) -> None:
    for f in roof.facets:
        edge = roof.edge(f.eave_edge_id)
        assert edge.edge_type is RoofEdgeType.EAVE


def test_every_edge_endpoint_resolves(roof) -> None:
    for e in roof.edges:
        assert roof.vertex(e.start_vertex_id) is not None
        assert roof.vertex(e.end_vertex_id) is not None


# ---------------------------------------------------------------------------
# Measurements
# ---------------------------------------------------------------------------


def test_footprint_matches_a_suburban_dwelling(roof) -> None:
    assert roof.total_projected_area_m2 == pytest.approx(80.1, abs=0.5)


def test_sloped_area_is_projected_over_cos_pitch(roof) -> None:
    assert roof.total_sloped_area_m2 == pytest.approx(
        roof.total_projected_area_m2 / COS_PITCH, rel=1e-9
    )
    assert roof.total_sloped_area_m2 == pytest.approx(88.4, abs=0.5)


def test_facet_areas_sum_to_the_roof_total(roof) -> None:
    assert sum(f.projected_area_m2 for f in roof.facets) == pytest.approx(
        roof.total_projected_area_m2, rel=1e-9
    )


def test_each_facet_sloped_area_uses_the_pitch(roof) -> None:
    for f in roof.facets:
        assert f.sloped_area_m2 == pytest.approx(f.projected_area_m2 / COS_PITCH, rel=1e-9)
        assert f.sloped_area_m2 > f.projected_area_m2


def test_trapezoids_are_larger_than_triangles(roof) -> None:
    traps = [f.projected_area_m2 for f in roof.facets if len(f.vertex_ids) == 4]
    tris = [f.projected_area_m2 for f in roof.facets if len(f.vertex_ids) == 3]
    assert min(traps) > max(tris)


def test_opposing_facets_have_equal_area(roof) -> None:
    """A symmetric hip roof: N/S match, E/W match.

    Tolerance is 1e-3 rather than exact because the calibration stores pixel
    coordinates to two decimal places - 0.01 px is 0.6 mm on the ground, which
    perturbs the areas in the fifth significant figure. Anything looser than
    this would stop catching a genuinely lopsided calibration.
    """
    by_id = {f.id: f.projected_area_m2 for f in roof.facets}
    assert by_id["facet_n"] == pytest.approx(by_id["facet_s"], rel=1e-3)
    assert by_id["facet_e"] == pytest.approx(by_id["facet_w"], rel=1e-3)


# ---------------------------------------------------------------------------
# A-GEO-1 on real data
# ---------------------------------------------------------------------------


def test_eaves_are_level_so_true_length_equals_projected(roof) -> None:
    for e in roof.edges:
        if e.edge_type is RoofEdgeType.EAVE:
            assert e.true_3d_length_m == pytest.approx(e.projected_length_m, rel=1e-9)


def test_ridge_is_horizontal_so_true_length_equals_projected(roof) -> None:
    ridge = next(e for e in roof.edges if e.edge_type is RoofEdgeType.RIDGE)
    assert ridge.true_3d_length_m == pytest.approx(ridge.projected_length_m, rel=1e-9)


def test_hips_are_longer_than_their_plan_length_but_not_by_cos_pitch(roof) -> None:
    """The regression guard, on the actual roof rather than a synthetic case.

    A hip runs diagonally across the slope. Its true length exceeds its plan
    length, but by less than the maximum-slope factor. Using projected/cos(pitch)
    here overstates each hip by roughly 4.8%.
    """
    hips = [e for e in roof.edges if e.edge_type is RoofEdgeType.HIP]
    assert len(hips) == 4

    for hip in hips:
        naive = hip.projected_length_m / COS_PITCH
        assert hip.true_3d_length_m > hip.projected_length_m
        assert hip.true_3d_length_m < naive
        assert hip.true_3d_length_m != pytest.approx(naive, rel=1e-3)

    overstatement = (hips[0].projected_length_m / COS_PITCH) / hips[0].true_3d_length_m - 1.0
    assert overstatement == pytest.approx(0.048, abs=0.01)


def test_all_hips_are_equal_on_a_symmetric_roof(roof) -> None:
    hips = [e.true_3d_length_m for e in roof.edges if e.edge_type is RoofEdgeType.HIP]
    assert max(hips) - min(hips) < 0.01


def test_surface_polygon_area_equals_sloped_area(roof) -> None:
    """The surface frame must be an isometry of the sloped plane."""
    for f in roof.facets:
        surface_area = polygon_area_m2(facet_surface_polygon(roof, f))
        assert surface_area == pytest.approx(f.sloped_area_m2, rel=1e-9)


def test_surface_polygon_has_non_negative_v(roof) -> None:
    """`v` points into the facet, so the whole outline sits at v >= 0."""
    for f in roof.facets:
        poly = facet_surface_polygon(roof, f)
        assert min(p.y for p in poly) == pytest.approx(0.0, abs=1e-6)
        assert max(p.y for p in poly) > 0.0


# ---------------------------------------------------------------------------
# Orientation - southern hemisphere
# ---------------------------------------------------------------------------


def test_facets_face_four_distinct_directions(roof) -> None:
    azimuths = sorted(f.compass_azimuth_deg for f in roof.facets)
    gaps = [(azimuths[(i + 1) % 4] - azimuths[i]) % 360.0 for i in range(4)]
    for gap in gaps:
        assert gap == pytest.approx(90.0, abs=1.0)


def test_north_facet_maps_to_a_near_180_pvgis_aspect(roof) -> None:
    """Southern hemisphere: the good facet is north, i.e. aspect near +/-180."""
    north = roof.facet("facet_n")
    assert abs(north.pvgis_aspect_deg) > 150.0
    assert north.compass_azimuth_deg == pytest.approx(10.6, abs=1.0)


def test_south_facet_maps_to_a_near_zero_pvgis_aspect(roof) -> None:
    south = roof.facet("facet_s")
    assert abs(south.pvgis_aspect_deg) < 30.0


def test_the_largest_facet_faces_north(roof) -> None:
    """Fortunate for this property, and the reason the allocator has room to work."""
    largest = max(roof.facets, key=lambda f: f.projected_area_m2)
    assert abs(largest.pvgis_aspect_deg) > 150.0


def test_east_and_west_are_opposite(roof) -> None:
    e = roof.facet("facet_e").compass_azimuth_deg
    w = roof.facet("facet_w").compass_azimuth_deg
    assert abs(((e - w + 180.0) % 360.0) - 180.0) == pytest.approx(180.0, abs=1.0)


# ---------------------------------------------------------------------------
# Summary shape
# ---------------------------------------------------------------------------


def test_summary_exposes_every_edge_and_facet(roof) -> None:
    s = roof_summary(roof)
    assert len(s["edges"]) == 9
    assert len(s["facets"]) == 4
    assert s["groundMetresPerSourcePixel"] == pytest.approx(0.06185, abs=1e-5)


def test_summary_reports_measurements_for_every_edge(roof) -> None:
    """Every edge must carry a metric label; the brief requires it."""
    for e in roof_summary(roof)["edges"]:
        assert e["projectedLengthM"] > 0
        assert e["true3dLengthM"] is not None
