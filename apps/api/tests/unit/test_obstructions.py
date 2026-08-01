"""Roof obstructions: loaded, validated, and actually excluded from placement.

An obstruction that is published but not subtracted would be the worst of both
worlds - a red outline in the proposal and a panel drawn straight across it - so
these tests care much more about the layout than about the parsing.

The load-bearing one is `test_removing_the_obstruction_restores_the_lost_bays`:
it rebuilds the roof from a calibration with the obstruction deleted and shows
capacity going back up. Without that, "the chimney costs three panels" is an
assertion about a number nobody derived independently.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.core.errors import RoofCalibrationMismatchError
from app.services.layout import assert_layout_valid, build_facet_candidates, generate_layout
from app.services.roof import CALIBRATION_PATH, build_roof_model

YIELDS = {"facet_n": 1678.77, "facet_w": 1503.89, "facet_e": 1367.24, "facet_s": 1114.85}


@pytest.fixture
def settings():
    """Array rotation off, so these tests measure the chimney and nothing else.

    With rotation enabled the east facet finds a fourth bay at 45 deg, which
    would move every count here for a reason that has nothing to do with an
    obstruction. Rotation has its own tests.
    """
    return get_settings().model_copy(update={"panel_rotation_step_deg": 0.0})


@pytest.fixture
def roof(settings):
    return build_roof_model(settings)


@pytest.fixture
def calibration() -> dict:
    return json.loads(CALIBRATION_PATH.read_text(encoding="utf-8"))


def _model_from(data: dict, tmp_path: Path, settings):
    path = tmp_path / "calibration.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return build_roof_model(settings, calibration_path=path)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def test_the_chimney_is_loaded_onto_the_north_facet(roof) -> None:
    assert len(roof.obstructions) == 1
    chimney = roof.obstructions[0]
    assert chimney.facet_id == "facet_n"
    assert chimney.kind == "chimney"
    assert len(chimney.source_pixel_polygon) == 4
    assert chimney.projected_area_m2 == pytest.approx(2.99, abs=0.01)


def test_sloped_area_is_the_plan_area_over_cos_pitch(roof) -> None:
    """Exact for an obstruction, unlike for a hip (A-GEO-1).

    A hip runs diagonally across the slope, so the pitch factor overstates it.
    An obstruction's footprint is a patch of the facet plane, so every part of
    it scales by the same cos(pitch) and the shortcut is the right answer.
    """
    chimney = roof.obstructions[0]
    assert chimney.sloped_area_m2 == pytest.approx(
        chimney.projected_area_m2 / math.cos(math.radians(roof.pitch_deg)), rel=1e-12
    )


def test_the_host_facet_reports_the_obstruction_and_its_usable_area(roof) -> None:
    north = roof.facet("facet_n")
    assert north.obstructed_area_m2 == pytest.approx(roof.obstructions[0].projected_area_m2)
    assert north.usable_projected_area_m2 == pytest.approx(
        north.projected_area_m2 - north.obstructed_area_m2
    )
    # The roof's own size is unchanged: a chimney stands ON the roof.
    assert north.projected_area_m2 > north.usable_projected_area_m2

    for other in roof.facets:
        if other.id != "facet_n":
            assert other.obstructed_area_m2 == 0.0


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_an_obstruction_declared_on_the_wrong_facet_is_refused(
    calibration, tmp_path, settings
) -> None:
    """The failure this guard exists for is silent, not loud.

    A wrong `facet_id` subtracts the polygon from a facet it does not overlap,
    so the real facet keeps its full area, a panel gets placed across the
    chimney, and everything downstream still renders plausibly.
    """
    calibration["obstructions"][0]["facet_id"] = "facet_s"

    with pytest.raises(RoofCalibrationMismatchError) as caught:
        _model_from(calibration, tmp_path, settings)

    assert "facet_s" in str(caught.value)
    assert caught.value.details["obstructionId"] == "obst_chimney_0"


def test_a_calibration_without_obstructions_still_loads(calibration, tmp_path, settings) -> None:
    """Backward compatibility: the key is optional."""
    del calibration["obstructions"]
    model = _model_from(calibration, tmp_path, settings)

    assert model.obstructions == []
    assert model.total_obstructed_area_m2 == 0.0
    assert all(f.obstructed_area_m2 == 0.0 for f in model.facets)


# ---------------------------------------------------------------------------
# Placement
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("size", [3.6, 6.0, 9.6])
def test_no_placed_panel_stands_on_the_chimney(roof, settings, size) -> None:
    layout = generate_layout(roof, size, YIELDS, settings)
    # The production post-condition names the obstruction explicitly.
    assert_layout_valid(roof, layout, settings)
    assert layout.placed_panel_count > 0


def test_removing_the_obstruction_restores_the_lost_bays(
    roof, calibration, tmp_path, settings
) -> None:
    """The revert proof: the three missing panels are the chimney's doing.

    2.99 m2 is a panel and a half by area, but it lands inside a row and breaks
    the tiling either side of itself, so it costs three. That is a claim worth
    demonstrating rather than asserting.
    """

    def capacity(model) -> dict[str, int]:
        return {
            f.id: max((c.max_count for c in build_facet_candidates(model, f, settings)), default=0)
            for f in model.facets
        }

    del calibration["obstructions"]
    without = _model_from(calibration, tmp_path, settings)

    with_chimney = capacity(roof)
    without_chimney = capacity(without)

    assert with_chimney["facet_n"] == 6
    assert without_chimney["facet_n"] == 9
    assert sum(with_chimney.values()) == 21
    assert sum(without_chimney.values()) == 24

    # ...and only the host facet is affected.
    for facet_id in ("facet_s", "facet_e", "facet_w"):
        assert with_chimney[facet_id] == without_chimney[facet_id]


def test_the_largest_system_becomes_a_shortfall_and_says_so(roof, settings) -> None:
    layout = generate_layout(roof, 9.6, YIELDS, settings)

    assert layout.requested_panel_count == 24
    assert layout.placed_panel_count == 21
    assert layout.feasible_system_size_kwp == pytest.approx(8.4)
    assert layout.capacity_warning is not None
    assert "9.6 kWp" in layout.capacity_warning
    assert "8.4" in layout.capacity_warning


def test_a_panel_over_the_chimney_is_rejected_by_the_post_condition(roof, settings) -> None:
    """Proves the guard fires, rather than trusting that it would.

    A panel is moved onto the chimney by hand and `assert_layout_valid` must
    refuse the layout, naming the obstruction rather than reporting a vague
    out-of-facet error.
    """
    layout = generate_layout(roof, 3.6, YIELDS, settings)
    north = next(fl for fl in layout.facet_layouts if fl.facet_id == "facet_n")

    frame_polygon = roof.obstructions[0].projected_metric_polygon
    from app.services.roof import facet_surface_frame

    surface = facet_surface_frame(roof, roof.facet("facet_n")).polygon_to_surface(frame_polygon)
    centre_u = sum(p.x for p in surface) / len(surface)
    centre_v = sum(p.y for p in surface) / len(surface)

    panel = north.panels[0]
    moved = panel.model_copy(
        update={
            "surface_polygon": [
                p.model_copy(update={"x": centre_u - 0.1, "y": centre_v - 0.1})
                if i == 0
                else p.model_copy(update={"x": centre_u + 0.1, "y": centre_v - 0.1})
                if i == 1
                else p.model_copy(update={"x": centre_u + 0.1, "y": centre_v + 0.1})
                if i == 2
                else p.model_copy(update={"x": centre_u - 0.1, "y": centre_v + 0.1})
                for i, p in enumerate(panel.surface_polygon)
            ]
        }
    )
    north.panels[0] = moved

    with pytest.raises(AssertionError, match="obst_chimney_0"):
        assert_layout_valid(roof, layout, settings)
