"""Turning a facet's array to fit more panels.

The search is a two-pass heuristic - a coarse offset sweep ranks the angles, and
only a shortlist is tiled properly - so the two properties worth pinning are
that it *never regresses* and that what it produces is still physically a panel.

The second is easy to lose. Rotation happens in the facet's surface frame, where
a panel is a true 2 x 1 m rectangle. The projection into plan view shortens the
up-slope axis by cos(pitch), so a turned panel is a parallelogram there. If the
rotation were ever applied on the wrong side of that projection the panel would
still *look* plausible and would no longer be 2 x 1 m on the roof.
"""

from __future__ import annotations

import math

import pytest

from app.core.config import get_settings
from app.domain.models import PanelOrientation
from app.services.layout import (
    _CANDIDATE_CACHE,
    assert_layout_valid,
    build_facet_candidates,
    generate_layout,
)
from app.services.roof import build_roof_model

YIELDS = {"facet_n": 1678.77, "facet_w": 1503.89, "facet_e": 1367.24, "facet_s": 1114.85}


@pytest.fixture(autouse=True)
def _clear_cache():
    """The candidate cache is keyed on the rotation settings, but these tests
    flip them repeatedly and a stale entry would make a failure look like a
    pass."""
    _CANDIDATE_CACHE.clear()
    yield
    _CANDIDATE_CACHE.clear()


@pytest.fixture
def straight():
    return get_settings().model_copy(update={"panel_rotation_step_deg": 0.0})


@pytest.fixture
def turning():
    return get_settings()


def _capacity(settings) -> dict[str, int]:
    roof = build_roof_model(settings)
    return {
        f.id: max((c.max_count for c in build_facet_candidates(roof, f, settings)), default=0)
        for f in roof.facets
    }


# ---------------------------------------------------------------------------
# The search
# ---------------------------------------------------------------------------


def test_the_setting_is_on_by_default_and_zero_disables_it(straight, turning) -> None:
    assert turning.panel_rotation_step_deg > 0
    roof = build_roof_model(straight)
    for facet in roof.facets:
        for candidate in build_facet_candidates(roof, facet, straight):
            assert candidate.rotation_deg == 0.0


def test_rotation_never_places_fewer_panels_than_lying_parallel(straight, turning) -> None:
    """The guarantee that makes the heuristic safe to enable.

    The coarse ranking pass undercounts, so a good angle can be ranked badly.
    Angle 0 is therefore always tiled at full resolution as well, and ties go to
    the smaller angle - so the result is a floor, not a gamble.
    """
    flat, turned = _capacity(straight), _capacity(turning)
    for facet_id, count in flat.items():
        assert turned[facet_id] >= count, f"rotation lost panels on {facet_id}"
    assert sum(turned.values()) >= sum(flat.values())


def test_on_the_case_roof_only_the_east_triangle_gains(straight, turning) -> None:
    """Measured, and worth stating: three of four facets are better off flat.

    The east triangle finds a fourth bay at 45 deg. The south trapezoid does
    *not* gain, even though a stronger tiler would find a tenth panel there at
    46 deg - the shipped tiler lays a rigid lattice, and a rigid lattice at 45
    deg fits 8 where flat fits 9. Rotation is not a substitute for a better
    tiler.
    """
    assert _capacity(straight) == {"facet_n": 6, "facet_s": 9, "facet_w": 3, "facet_e": 3}
    assert _capacity(turning) == {"facet_n": 6, "facet_s": 9, "facet_w": 3, "facet_e": 4}


def test_the_chosen_angle_is_recorded_on_the_layout_and_on_every_panel(turning) -> None:
    roof = build_roof_model(turning)
    layout = generate_layout(roof, 9.6, YIELDS, turning)

    east = next(fl for fl in layout.facet_layouts if fl.facet_id == "facet_e")
    assert east.rotation_deg == pytest.approx(45.0)
    assert all(p.rotation_deg == east.rotation_deg for p in east.panels)

    north = next(fl for fl in layout.facet_layouts if fl.facet_id == "facet_n")
    assert north.rotation_deg == 0.0
    assert all(p.rotation_deg == 0.0 for p in north.panels)


# ---------------------------------------------------------------------------
# The panels it produces
# ---------------------------------------------------------------------------


def _sides(polygon) -> list[float]:
    return [
        math.hypot(
            polygon[(i + 1) % len(polygon)].x - polygon[i].x,
            polygon[(i + 1) % len(polygon)].y - polygon[i].y,
        )
        for i in range(len(polygon))
    ]


def _angles(polygon) -> list[float]:
    out = []
    for i in range(len(polygon)):
        a, b, c = polygon[i - 1], polygon[i], polygon[(i + 1) % len(polygon)]
        v1 = (a.x - b.x, a.y - b.y)
        v2 = (c.x - b.x, c.y - b.y)
        cos = (v1[0] * v2[0] + v1[1] * v2[1]) / (math.hypot(*v1) * math.hypot(*v2))
        out.append(math.degrees(math.acos(max(-1.0, min(1.0, cos)))))
    return out


def test_a_turned_panel_is_still_exactly_two_metres_by_one_on_the_roof(turning) -> None:
    """Rotation is a rigid motion of the surface frame, so nothing may resize."""
    roof = build_roof_model(turning)
    layout = generate_layout(roof, 9.6, YIELDS, turning)

    for panel in layout.panels:
        sides = sorted(_sides(panel.surface_polygon))
        assert sides[0] == pytest.approx(turning.panel_width_m, abs=1e-9)
        assert sides[-1] == pytest.approx(turning.panel_height_m, abs=1e-9)
        for angle in _angles(panel.surface_polygon):
            assert angle == pytest.approx(90.0, abs=1e-9)


def test_a_turned_panel_is_a_parallelogram_in_plan_view(turning) -> None:
    """The visible consequence, asserted rather than left as a surprise.

    A panel lying parallel to the eave stays rectangular in plan, just
    foreshortened to 2 x 0.906 m. Turn it and the corners open to roughly 95 and
    85 degrees. Anything that measures or hit-tests a panel has to do it on the
    surface, not here.
    """
    roof = build_roof_model(turning)
    layout = generate_layout(roof, 9.6, YIELDS, turning)
    cos_pitch = math.cos(math.radians(roof.pitch_deg))

    flat = next(p for p in layout.panels if p.rotation_deg == 0.0)
    assert sorted(_sides(flat.projected_metric_polygon))[0] == pytest.approx(
        turning.panel_width_m * cos_pitch, rel=1e-9
    )
    for angle in _angles(flat.projected_metric_polygon):
        assert angle == pytest.approx(90.0, abs=1e-9)

    turned = next(p for p in layout.panels if p.rotation_deg != 0.0)
    corners = _angles(turned.projected_metric_polygon)
    assert max(corners) > 93.0
    assert min(corners) < 87.0
    # Opposite corners still match: an affine map sends a rectangle to a
    # parallelogram, never to a general quadrilateral.
    assert corners[0] == pytest.approx(corners[2], abs=1e-9)
    assert corners[1] == pytest.approx(corners[3], abs=1e-9)


def test_turned_panels_still_satisfy_every_placement_post_condition(turning) -> None:
    """Containment, no overlap, and nothing standing on the chimney."""
    roof = build_roof_model(turning)
    for size in (3.6, 6.0, 9.6):
        layout = generate_layout(roof, size, YIELDS, turning)
        assert_layout_valid(roof, layout, turning)


def test_the_extra_east_panel_reaches_production_and_the_shortfall(turning, straight) -> None:
    """The point of the whole change: one more panel, and the warning follows."""
    roof_flat = build_roof_model(straight)
    flat = generate_layout(roof_flat, 9.6, YIELDS, straight)

    _CANDIDATE_CACHE.clear()
    roof_turned = build_roof_model(turning)
    turned = generate_layout(roof_turned, 9.6, YIELDS, turning)

    assert flat.placed_panel_count == 21
    assert turned.placed_panel_count == 22
    assert turned.feasible_system_size_kwp == pytest.approx(8.8)
    # Still short of 24, so the warning must survive the improvement.
    assert turned.capacity_warning is not None
    assert "8.8" in turned.capacity_warning


def test_landscape_and_portrait_are_still_both_searched(turning) -> None:
    roof = build_roof_model(turning)
    orientations = {
        c.orientation for f in roof.facets for c in build_facet_candidates(roof, f, turning)
    }
    assert orientations <= {PanelOrientation.LANDSCAPE, PanelOrientation.PORTRAIT}
    assert PanelOrientation.LANDSCAPE in orientations
