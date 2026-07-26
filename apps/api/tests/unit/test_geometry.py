"""Geometry engine tests.

The measurement layer is where a silent error is most expensive: a wrong scale
or a flipped sign still renders a plausible-looking roof. These tests pin the
numbers rather than the shapes.
"""

from __future__ import annotations

import math

import pytest

from app.core.config import meters_per_source_pixel
from app.domain.geometry import (
    build_surface_frame,
    compass_azimuth_to_pvgis_aspect,
    edge_type_supports_slope_correction,
    facet_compass_azimuth,
    metric_to_source_pixel,
    pixel_distance,
    polygon_area_m2,
    polygon_centroid,
    projected_length_m,
    responsive_scale,
    slope_edge_true_length_m,
    sloped_area_m2,
    source_pixel_to_metric,
    true_3d_length_m,
    validate_level_eave,
)
from app.domain.models import Point2D, RoofEdgeType

CASE_LAT = -34.04658242871865
ZOOM = 20
SCALE = 2
SRC_PX = 1280
# Web Mercator ground resolution at the resolved case coordinate.
EXPECTED_M_PER_SRC_PX = 0.0618500

P = Point2D


# ---------------------------------------------------------------------------
# Pixel scale
# ---------------------------------------------------------------------------


def test_meters_per_source_pixel_matches_web_mercator_at_case_site() -> None:
    got = meters_per_source_pixel(CASE_LAT, ZOOM, SCALE)
    assert got == pytest.approx(EXPECTED_M_PER_SRC_PX, rel=1e-4)


def test_raster_ground_span_is_79_metres() -> None:
    span = SRC_PX * meters_per_source_pixel(CASE_LAT, ZOOM, SCALE)
    assert span == pytest.approx(79.17, abs=0.05)


def test_scale_2_halves_metres_per_source_pixel() -> None:
    one = meters_per_source_pixel(CASE_LAT, ZOOM, 1)
    two = meters_per_source_pixel(CASE_LAT, ZOOM, 2)
    assert two == pytest.approx(one / 2.0, rel=1e-12)


def test_latitude_sign_does_not_change_resolution() -> None:
    """cos is even, so the southern hemisphere resolves identically."""
    assert meters_per_source_pixel(-34.0466, ZOOM, SCALE) == pytest.approx(
        meters_per_source_pixel(34.0466, ZOOM, SCALE), rel=1e-12
    )


def test_zoom_increment_halves_resolution() -> None:
    assert meters_per_source_pixel(CASE_LAT, 21, SCALE) == pytest.approx(
        meters_per_source_pixel(CASE_LAT, 20, SCALE) / 2.0, rel=1e-12
    )


@pytest.mark.parametrize(
    ("lat", "zoom", "scale"),
    [
        (CASE_LAT, ZOOM, 0),
        (CASE_LAT, ZOOM, -1),
        (CASE_LAT, -1, 2),
        (CASE_LAT, 99, 2),
        (91.0, 20, 2),
    ],
)
def test_invalid_scale_zoom_or_latitude_rejected(lat: float, zoom: int, scale: int) -> None:
    with pytest.raises(ValueError):
        meters_per_source_pixel(lat, zoom, scale)


# ---------------------------------------------------------------------------
# Pixel <-> metric
# ---------------------------------------------------------------------------


def _to_metric(p: Point2D) -> Point2D:
    return source_pixel_to_metric(
        p,
        source_width_px=SRC_PX,
        source_height_px=SRC_PX,
        ground_m_per_source_px=EXPECTED_M_PER_SRC_PX,
    )


def _to_pixel(p: Point2D) -> Point2D:
    return metric_to_source_pixel(
        p,
        source_width_px=SRC_PX,
        source_height_px=SRC_PX,
        ground_m_per_source_px=EXPECTED_M_PER_SRC_PX,
    )


def test_raster_centre_is_metric_origin() -> None:
    m = _to_metric(P(x=640, y=640))
    assert m.x == pytest.approx(0.0, abs=1e-12)
    assert m.y == pytest.approx(0.0, abs=1e-12)


def test_image_y_is_inverted_so_metric_y_points_north() -> None:
    """Moving DOWN the image must move SOUTH, i.e. metric y decreases."""
    north = _to_metric(P(x=640, y=540))
    south = _to_metric(P(x=640, y=740))
    assert north.y > 0 > south.y
    assert north.y == pytest.approx(-south.y, rel=1e-12)


def test_image_x_increases_east() -> None:
    assert _to_metric(P(x=740, y=640)).x > 0
    assert _to_metric(P(x=540, y=640)).x < 0


@pytest.mark.parametrize("pt", [P(x=0, y=0), P(x=1280, y=1280), P(x=123.5, y=987.25)])
def test_pixel_metric_round_trip(pt: Point2D) -> None:
    back = _to_pixel(_to_metric(pt))
    assert back.x == pytest.approx(pt.x, abs=1e-9)
    assert back.y == pytest.approx(pt.y, abs=1e-9)


def test_metric_to_pixel_rejects_zero_resolution() -> None:
    with pytest.raises(ValueError):
        metric_to_source_pixel(
            P(x=1, y=1),
            source_width_px=SRC_PX,
            source_height_px=SRC_PX,
            ground_m_per_source_px=0.0,
        )


# ---------------------------------------------------------------------------
# Lengths
# ---------------------------------------------------------------------------


def test_pixel_distance_is_euclidean() -> None:
    assert pixel_distance(P(x=0, y=0), P(x=3, y=4)) == pytest.approx(5.0)


def test_projected_length_scales_by_ground_resolution() -> None:
    got = projected_length_m(
        P(x=0, y=0), P(x=100, y=0), ground_m_per_source_px=EXPECTED_M_PER_SRC_PX
    )
    assert got == pytest.approx(100 * EXPECTED_M_PER_SRC_PX)
    assert got == pytest.approx(6.185, abs=1e-3)


def test_true_3d_length_uses_all_three_axes() -> None:
    got = true_3d_length_m(P(x=0, y=0), P(x=3, y=0), height_a_m=0.0, height_b_m=4.0)
    assert got == pytest.approx(5.0)


def test_true_3d_length_equals_projected_when_level() -> None:
    got = true_3d_length_m(P(x=0, y=0), P(x=6, y=8), height_a_m=2.0, height_b_m=2.0)
    assert got == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# Areas
# ---------------------------------------------------------------------------


def test_polygon_area_of_unit_square() -> None:
    sq = [P(x=0, y=0), P(x=2, y=0), P(x=2, y=3), P(x=0, y=3)]
    assert polygon_area_m2(sq) == pytest.approx(6.0)


def test_polygon_area_is_winding_invariant() -> None:
    sq = [P(x=0, y=0), P(x=2, y=0), P(x=2, y=3), P(x=0, y=3)]
    assert polygon_area_m2(sq) == pytest.approx(polygon_area_m2(list(reversed(sq))))


def test_polygon_area_of_triangle() -> None:
    tri = [P(x=0, y=0), P(x=4, y=0), P(x=0, y=3)]
    assert polygon_area_m2(tri) == pytest.approx(6.0)


def test_polygon_area_rejects_degenerate_input() -> None:
    with pytest.raises(ValueError):
        polygon_area_m2([P(x=0, y=0), P(x=1, y=1)])


def test_sloped_area_exceeds_projected_area_at_25_degrees() -> None:
    projected = 23.6
    sloped = sloped_area_m2(projected, 25.0)
    assert sloped == pytest.approx(projected / math.cos(math.radians(25.0)))
    assert sloped > projected
    assert sloped == pytest.approx(26.04, abs=0.01)


def test_sloped_area_of_flat_roof_is_unchanged() -> None:
    assert sloped_area_m2(50.0, 0.0) == pytest.approx(50.0)


def test_sloped_area_rejects_degenerate_pitch() -> None:
    with pytest.raises(ValueError):
        sloped_area_m2(10.0, 90.0)


def test_polygon_centroid_of_square() -> None:
    c = polygon_centroid([P(x=0, y=0), P(x=4, y=0), P(x=4, y=4), P(x=0, y=4)])
    assert (c.x, c.y) == pytest.approx((2.0, 2.0))


def test_polygon_centroid_handles_zero_area() -> None:
    c = polygon_centroid([P(x=0, y=0), P(x=1, y=1), P(x=2, y=2)])
    assert c.x == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Azimuth
# ---------------------------------------------------------------------------


def _square_facet_with_eave_towards(direction: str) -> tuple[Point2D, Point2D, list[Point2D]]:
    """Unit facet centred at the origin whose eave lies on one side."""
    poly = [P(x=-1, y=-1), P(x=1, y=-1), P(x=1, y=1), P(x=-1, y=1)]
    eaves = {
        "north": (P(x=-1, y=1), P(x=1, y=1)),
        "south": (P(x=-1, y=-1), P(x=1, y=-1)),
        "east": (P(x=1, y=-1), P(x=1, y=1)),
        "west": (P(x=-1, y=-1), P(x=-1, y=1)),
    }
    a, b = eaves[direction]
    return a, b, poly


@pytest.mark.parametrize(
    ("direction", "expected"),
    [("north", 0.0), ("east", 90.0), ("south", 180.0), ("west", 270.0)],
)
def test_facet_azimuth_cardinal_directions(direction: str, expected: float) -> None:
    a, b, poly = _square_facet_with_eave_towards(direction)
    got = facet_compass_azimuth(eave_start=a, eave_end=b, facet_polygon=poly)
    assert got % 360.0 == pytest.approx(expected, abs=1e-9)


def test_facet_azimuth_is_independent_of_eave_endpoint_order() -> None:
    a, b, poly = _square_facet_with_eave_towards("north")
    assert facet_compass_azimuth(eave_start=a, eave_end=b, facet_polygon=poly) == pytest.approx(
        facet_compass_azimuth(eave_start=b, eave_end=a, facet_polygon=poly)
    )


def test_facet_azimuth_rejects_centroid_on_eave() -> None:
    poly = [P(x=-1, y=0), P(x=1, y=0), P(x=0, y=0)]
    with pytest.raises(ValueError):
        facet_compass_azimuth(eave_start=P(x=-1, y=0), eave_end=P(x=1, y=0), facet_polygon=poly)


@pytest.mark.parametrize(
    ("compass", "aspect"),
    [(180.0, 0.0), (270.0, 90.0), (90.0, -90.0), (0.0, 180.0), (360.0, 180.0)],
)
def test_compass_to_pvgis_aspect_cardinals(compass: float, aspect: float) -> None:
    assert compass_azimuth_to_pvgis_aspect(compass) == pytest.approx(aspect)


def test_pvgis_aspect_always_within_half_open_180_range() -> None:
    for compass in range(0, 720, 7):
        aspect = compass_azimuth_to_pvgis_aspect(float(compass % 360))
        assert -180.0 < aspect <= 180.0


def test_case_roof_north_facet_maps_to_near_north_aspect() -> None:
    """The reference roof's large facet faces ~8.5 deg (near due north).

    In the southern hemisphere that is the BEST orientation, so this value
    must land near +/-180 in PVGIS terms, not near 0.
    """
    aspect = compass_azimuth_to_pvgis_aspect(8.5)
    assert aspect == pytest.approx(-171.5)
    assert abs(aspect) > 170.0, "north-facing facet must be near +/-180 for PVGIS"


# ---------------------------------------------------------------------------
# A-GEO-1: surface frame
# ---------------------------------------------------------------------------


def _frame(pitch: float = 25.0):
    """Facet with a level eave along +x and its body towards +y."""
    poly = [P(x=0, y=0), P(x=10, y=0), P(x=10, y=4), P(x=0, y=4)]
    return build_surface_frame(
        eave_start=P(x=0, y=0), eave_end=P(x=10, y=0), facet_polygon=poly, pitch_deg=pitch
    )


def test_surface_axes_are_orthonormal() -> None:
    f = _frame()
    dot = f.eave_unit.x * f.inward_unit.x + f.eave_unit.y * f.inward_unit.y
    assert dot == pytest.approx(0.0, abs=1e-12)
    assert math.hypot(f.eave_unit.x, f.eave_unit.y) == pytest.approx(1.0)
    assert math.hypot(f.inward_unit.x, f.inward_unit.y) == pytest.approx(1.0)


def test_inward_normal_points_into_the_facet() -> None:
    f = _frame()
    assert f.inward_unit.y > 0


def test_u_axis_is_not_foreshortened() -> None:
    """Along the eave, surface metres equal projected metres exactly."""
    f = _frame()
    s = f.to_surface(P(x=7.0, y=0.0))
    assert s.x == pytest.approx(7.0)
    assert s.y == pytest.approx(0.0, abs=1e-12)


def test_v_axis_is_foreshortened_by_cos_pitch_only() -> None:
    f = _frame(25.0)
    s = f.to_surface(P(x=0.0, y=2.0))
    assert s.x == pytest.approx(0.0, abs=1e-12)
    assert s.y == pytest.approx(2.0 / math.cos(math.radians(25.0)))
    assert s.y > 2.0


def test_unit_square_on_surface_projects_to_foreshortened_rectangle() -> None:
    """A 1x2 m panel stays 1x2 m on the roof surface; only its plan view shrinks."""
    f = _frame(25.0)
    corners = [P(x=0, y=0), P(x=1, y=0), P(x=1, y=2), P(x=0, y=2)]
    projected = f.polygon_to_projected(corners)

    along_u = math.dist(projected[0].as_tuple(), projected[1].as_tuple())
    along_v = math.dist(projected[1].as_tuple(), projected[2].as_tuple())

    assert along_u == pytest.approx(1.0), "u must be unchanged"
    assert along_v == pytest.approx(2.0 * math.cos(math.radians(25.0)))
    assert polygon_area_m2(projected) == pytest.approx(2.0 * math.cos(math.radians(25.0)))


def test_surface_projected_round_trip() -> None:
    f = _frame(25.0)
    for pt in (P(x=0, y=0), P(x=3.5, y=1.25), P(x=9.9, y=4.4)):
        back = f.to_surface(f.to_projected(pt))
        assert back.x == pytest.approx(pt.x, abs=1e-9)
        assert back.y == pytest.approx(pt.y, abs=1e-9)


def test_flat_facet_surface_equals_projection() -> None:
    f = _frame(0.0)
    s = f.to_surface(P(x=2.0, y=3.0))
    assert (s.x, s.y) == pytest.approx((2.0, 3.0))


def test_build_surface_frame_rejects_zero_length_eave() -> None:
    with pytest.raises(ValueError):
        build_surface_frame(
            eave_start=P(x=1, y=1),
            eave_end=P(x=1, y=1),
            facet_polygon=[P(x=0, y=0), P(x=1, y=0), P(x=0, y=1)],
            pitch_deg=25.0,
        )


def test_surface_area_recovers_sloped_area() -> None:
    """Facet area measured in surface space == projected area / cos(pitch)."""
    poly = [P(x=0, y=0), P(x=10, y=0), P(x=10, y=4), P(x=0, y=4)]
    f = _frame(25.0)
    projected_area = polygon_area_m2(poly)
    surface_area = polygon_area_m2(f.polygon_to_surface(poly))
    assert surface_area == pytest.approx(sloped_area_m2(projected_area, 25.0))


# ---------------------------------------------------------------------------
# A-GEO-1: the hip-edge regression guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("edge_type", [RoofEdgeType.EAVE, RoofEdgeType.HIP, RoofEdgeType.RIDGE])
def test_no_roof_edge_type_permits_blanket_slope_correction(edge_type: RoofEdgeType) -> None:
    assert edge_type_supports_slope_correction(edge_type) is False


def test_hip_length_must_not_be_projected_over_cos_pitch() -> None:
    """Guards the classic overstatement bug.

    A hip on a 25 deg roof with a 10.4 x 6.7 m footprint rises 1.56 m over a
    4.72 m plan run. Its true length is ~4.97 m. Dividing the plan length by
    cos(25 deg) would claim ~5.21 m - a 4.8% overstatement that compounds into
    facet areas and panel counts.
    """
    pitch = 25.0
    half_depth = 6.7 / 2.0
    ridge_height = half_depth * math.tan(math.radians(pitch))

    hip_start = P(x=0.0, y=0.0)
    hip_end = P(x=half_depth, y=half_depth)

    plan_length = math.dist(hip_start.as_tuple(), hip_end.as_tuple())
    correct = true_3d_length_m(hip_start, hip_end, height_a_m=0.0, height_b_m=ridge_height)
    naive = slope_edge_true_length_m(plan_length, pitch)

    assert correct == pytest.approx(4.97, abs=0.02)
    assert naive == pytest.approx(5.21, abs=0.02)
    assert naive > correct
    assert correct != pytest.approx(naive, rel=1e-3)


def test_slope_correction_is_still_correct_for_a_true_maximum_slope_edge() -> None:
    """The correction is not wrong - it is just narrowly scoped."""
    pitch = 25.0
    plan_run = 3.35
    rise = plan_run * math.tan(math.radians(pitch))
    correct = true_3d_length_m(P(x=0, y=0), P(x=plan_run, y=0), height_a_m=0.0, height_b_m=rise)
    assert slope_edge_true_length_m(plan_run, pitch) == pytest.approx(correct)


# ---------------------------------------------------------------------------
# Level-eave precondition
# ---------------------------------------------------------------------------


def test_level_eave_accepted() -> None:
    assert validate_level_eave(height_start_m=3.0, height_end_m=3.02) is True


def test_sloping_eave_rejected() -> None:
    assert validate_level_eave(height_start_m=3.0, height_end_m=3.4) is False


def test_unknown_heights_do_not_fail_the_check() -> None:
    assert validate_level_eave(height_start_m=None, height_end_m=None) is True


# ---------------------------------------------------------------------------
# Responsive display transform
# ---------------------------------------------------------------------------


def test_responsive_scale_halves_at_half_width() -> None:
    d = responsive_scale(P(x=640, y=1280), source_size_px=1280, displayed_size_px=640)
    assert (d.x, d.y) == pytest.approx((320.0, 640.0))


def test_responsive_scale_identity_at_native_size() -> None:
    d = responsive_scale(P(x=17, y=42), source_size_px=1280, displayed_size_px=1280)
    assert (d.x, d.y) == pytest.approx((17.0, 42.0))


def test_responsive_scale_rejects_zero_source() -> None:
    with pytest.raises(ValueError):
        responsive_scale(P(x=1, y=1), source_size_px=0, displayed_size_px=100)
