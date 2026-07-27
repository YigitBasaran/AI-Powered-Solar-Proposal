"""Panel placement tests.

Placement is the part of the pipeline where a wrong answer is most convincing:
an over-packed roof renders beautifully and produces a confident proposal. So
these tests check physical validity (containment, overlap, real panel size) as
hard as they check counts.
"""

from __future__ import annotations

import math

import pytest
from shapely.geometry import Polygon

from app.core.config import get_settings
from app.domain.models import PanelOrientation
from app.services.layout import (
    assert_layout_valid,
    build_facet_candidates,
    generate_layout,
)
from app.services.roof import build_roof_model, facet_surface_polygon
from app.services.yield_ranking import (
    FixtureFacetYieldRankingProvider,
    StaticFacetYieldRankingProvider,
)

PITCH = 25.0
COS_PITCH = math.cos(math.radians(PITCH))


@pytest.fixture(scope="module")
def settings():
    return get_settings()


@pytest.fixture(scope="module")
def roof():
    return build_roof_model()


@pytest.fixture(scope="module")
def provider():
    return FixtureFacetYieldRankingProvider()


# ---------------------------------------------------------------------------
# Requested counts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("size", "expected"), [(3.6, 9), (6.0, 15), (9.6, 24)])
def test_required_panel_count_is_derived_not_hardcoded(settings, size, expected) -> None:
    assert settings.required_panel_count(size) == expected


def test_non_integral_system_size_is_rejected(settings) -> None:
    with pytest.raises(ValueError):
        settings.required_panel_count(5.0)


@pytest.mark.parametrize(("size", "expected"), [(3.6, 9), (6.0, 15), (9.6, 24)])
async def test_each_case_size_places_the_requested_count(
    roof, provider, settings, size, expected
) -> None:
    layout = await generate_layout(roof, size, provider, settings)
    assert layout.requested_panel_count == expected
    assert layout.placed_panel_count == expected
    assert layout.is_fully_satisfied
    assert layout.capacity_warning is None


@pytest.mark.parametrize("size", [3.6, 6.0, 9.6])
async def test_installed_power_equals_count_times_panel_rating(
    roof, provider, settings, size
) -> None:
    layout = await generate_layout(roof, size, provider, settings)
    assert layout.feasible_system_size_kwp == pytest.approx(layout.placed_panel_count * 0.4)
    assert layout.feasible_system_size_kwp == pytest.approx(size)


async def test_unsupported_system_size_is_refused(roof, provider, settings) -> None:
    with pytest.raises(ValueError):
        await generate_layout(roof, 5.0, provider, settings)


# ---------------------------------------------------------------------------
# Physical validity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("size", [3.6, 6.0, 9.6])
async def test_layout_passes_its_own_post_conditions(roof, provider, settings, size) -> None:
    layout = await generate_layout(roof, size, provider, settings)
    assert_layout_valid(roof, layout, settings)


@pytest.mark.parametrize("size", [3.6, 6.0, 9.6])
async def test_every_panel_lies_fully_within_its_facet(roof, provider, settings, size) -> None:
    """Containment of the whole footprint, not just the centre."""
    layout = await generate_layout(roof, size, provider, settings)
    for facet_layout in layout.facet_layouts:
        facet = roof.facet(facet_layout.facet_id)
        usable = Polygon([(p.x, p.y) for p in facet_surface_polygon(roof, facet)])
        for panel in facet_layout.panels:
            assert usable.covers(Polygon([(p.x, p.y) for p in panel.surface_polygon]))


@pytest.mark.parametrize("size", [3.6, 6.0, 9.6])
async def test_no_two_panels_overlap(roof, provider, settings, size) -> None:
    layout = await generate_layout(roof, size, provider, settings)
    for facet_layout in layout.facet_layouts:
        polys = [
            Polygon([(p.x, p.y) for p in panel.surface_polygon]) for panel in facet_layout.panels
        ]
        for i, a in enumerate(polys):
            for b in polys[i + 1 :]:
                assert a.intersection(b).area < 1e-9


async def test_configured_gap_is_respected(roof, provider, settings) -> None:
    """Neighbouring panels in a row must be at least the gap apart."""
    layout = await generate_layout(roof, 9.6, provider, settings)
    gap = settings.panel_gap_m
    for facet_layout in layout.facet_layouts:
        polys = [
            Polygon([(p.x, p.y) for p in panel.surface_polygon]) for panel in facet_layout.panels
        ]
        for i, a in enumerate(polys):
            for b in polys[i + 1 :]:
                assert a.distance(b) >= gap - 1e-6


@pytest.mark.parametrize("size", [3.6, 6.0, 9.6])
async def test_panels_are_physically_one_by_two_metres_on_the_slope(
    roof, provider, settings, size
) -> None:
    """The whole reason placement happens in surface coordinates."""
    layout = await generate_layout(roof, size, provider, settings)
    for panel in layout.panels:
        poly = panel.surface_polygon
        side_a = math.dist(poly[0].as_tuple(), poly[1].as_tuple())
        side_b = math.dist(poly[1].as_tuple(), poly[2].as_tuple())
        assert sorted([side_a, side_b]) == pytest.approx([1.0, 2.0])
        assert Polygon([(p.x, p.y) for p in poly]).area == pytest.approx(2.0)


async def test_projected_panel_is_foreshortened_by_cos_pitch(roof, provider, settings) -> None:
    """In plan view a panel must shrink along the slope - and only along it."""
    layout = await generate_layout(roof, 3.6, provider, settings)
    for panel in layout.panels:
        surface_area = Polygon([(p.x, p.y) for p in panel.surface_polygon]).area
        projected_area = Polygon([(p.x, p.y) for p in panel.projected_metric_polygon]).area
        assert projected_area == pytest.approx(surface_area * COS_PITCH, rel=1e-6)


async def test_panels_have_source_pixel_geometry_for_rendering(roof, provider, settings) -> None:
    layout = await generate_layout(roof, 3.6, provider, settings)
    for panel in layout.panels:
        assert len(panel.source_pixel_polygon) == 4
        for p in panel.source_pixel_polygon:
            assert 0 <= p.x <= roof.source_width_px
            assert 0 <= p.y <= roof.source_height_px


# ---------------------------------------------------------------------------
# Orientation
# ---------------------------------------------------------------------------


def test_both_orientations_are_evaluated_for_every_facet(roof, settings) -> None:
    for facet in roof.facets:
        orientations = {c.orientation for c in build_facet_candidates(roof, facet, settings)}
        assert orientations == {PanelOrientation.PORTRAIT, PanelOrientation.LANDSCAPE}


def test_landscape_outperforms_portrait_on_these_facets(roof, settings) -> None:
    """Shallow facets fit more rows of 2x1 than of 1x2; the search must find that."""
    for facet in roof.facets:
        by_orientation = {
            c.orientation: c.max_count for c in build_facet_candidates(roof, facet, settings)
        }
        assert (
            by_orientation[PanelOrientation.LANDSCAPE] >= by_orientation[PanelOrientation.PORTRAIT]
        )


# ---------------------------------------------------------------------------
# Production-first allocation
# ---------------------------------------------------------------------------


async def test_small_system_goes_entirely_to_the_best_facet(roof, provider, settings) -> None:
    layout = await generate_layout(roof, 3.6, provider, settings)
    assert len(layout.facet_layouts) == 1
    assert layout.facet_layouts[0].facet_id == "facet_n"


async def test_medium_system_prefers_small_high_yield_facets_over_a_large_poor_one(
    roof, provider, settings
) -> None:
    """The southern-hemisphere test that a naive area-greedy fill would fail.

    North and south are the same size (30.1 m2, 9 panels each). A fill that
    ranks by area would put the remaining 6 panels on south. But at this site
    south yields 1120 kWh/kWp while the two small east/west triangles yield
    1367 and 1515, so the correct answer is to use both triangles and leave
    south empty.
    """
    layout = await generate_layout(roof, 6.0, provider, settings)
    used = {fl.facet_id: fl.panel_count for fl in layout.facet_layouts}

    assert used["facet_n"] == 9
    assert "facet_s" not in used, "south is the worst facet and must be skipped"
    assert used["facet_w"] == 3
    assert used["facet_e"] == 3


async def test_full_system_uses_every_facet(roof, provider, settings) -> None:
    layout = await generate_layout(roof, 9.6, provider, settings)
    assert {fl.facet_id for fl in layout.facet_layouts} == {
        "facet_n",
        "facet_s",
        "facet_e",
        "facet_w",
    }


async def test_allocation_follows_the_supplied_ranking_not_the_geometry(roof, settings) -> None:
    """Invert the yields and the allocation must invert with them."""
    inverted = StaticFacetYieldRankingProvider(
        {"facet_n": 100.0, "facet_s": 2000.0, "facet_e": 100.0, "facet_w": 100.0}
    )
    layout = await generate_layout(roof, 3.6, inverted, settings)
    assert [fl.facet_id for fl in layout.facet_layouts] == ["facet_s"]


async def test_expected_production_is_maximised_for_the_placed_count(
    roof, provider, settings
) -> None:
    """No reshuffle of the same number of panels can beat the chosen one."""
    layout = await generate_layout(roof, 6.0, provider, settings)
    yields = {f.id: provider.lookup(f.pvgis_aspect_deg) for f in roof.facets}
    chosen = sum(fl.panel_count * 0.4 * yields[fl.facet_id] for fl in layout.facet_layouts)

    capacity = {
        f.id: max((c.max_count for c in build_facet_candidates(roof, f, settings)), default=0)
        for f in roof.facets
    }
    # Greedy by yield is optimal for a linear objective, so it is a valid oracle.
    best = 0.0
    remaining = layout.placed_panel_count
    for fid, _ in sorted(yields.items(), key=lambda kv: -kv[1]):
        take = min(remaining, capacity[fid])
        best += take * 0.4 * yields[fid]
        remaining -= take
    assert chosen == pytest.approx(best, rel=1e-9)


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("size", [3.6, 6.0, 9.6])
async def test_repeated_runs_produce_identical_layouts(roof, provider, settings, size) -> None:
    a = await generate_layout(roof, size, provider, settings)
    b = await generate_layout(roof, size, provider, settings)
    assert a.model_dump_json() == b.model_dump_json()


# ---------------------------------------------------------------------------
# Honest capacity limitation
# ---------------------------------------------------------------------------


async def test_large_setback_reduces_capacity_and_raises_a_warning(
    roof, provider, settings
) -> None:
    """The roof fits 24 panels as calibrated, so capacity is forced down here.

    A 1 m safety setback is a realistic requirement and makes the requested
    count physically impossible, which is the behaviour the brief asks to be
    handled honestly rather than papered over.
    """
    tight = settings.model_copy(update={"roof_edge_setback_m": 1.0})
    layout = await generate_layout(roof, 9.6, provider, tight)

    assert layout.requested_panel_count == 24
    assert layout.placed_panel_count < 24
    assert layout.capacity_warning is not None
    assert "not the 24 panels" in layout.capacity_warning
    assert layout.feasible_system_size_kwp == pytest.approx(layout.placed_panel_count * 0.4)
    assert_layout_valid(roof, layout, tight)


async def test_shortfall_still_prefers_the_best_facets(roof, provider, settings) -> None:
    tight = settings.model_copy(update={"roof_edge_setback_m": 1.0})
    layout = await generate_layout(roof, 9.6, provider, tight)
    if layout.facet_layouts:
        best = max(layout.facet_layouts, key=lambda fl: fl.panel_count)
        assert best.facet_id == "facet_n"


async def test_impossible_setback_yields_no_panels_and_a_warning(roof, provider, settings) -> None:
    absurd = settings.model_copy(update={"roof_edge_setback_m": 5.0})
    layout = await generate_layout(roof, 3.6, provider, absurd)
    assert layout.placed_panel_count == 0
    assert layout.feasible_system_size_kwp == 0
    assert layout.capacity_warning is not None


async def test_capacity_warning_never_claims_the_requested_size_was_installed(
    roof, provider, settings
) -> None:
    tight = settings.model_copy(update={"roof_edge_setback_m": 1.0})
    layout = await generate_layout(roof, 9.6, provider, tight)
    assert layout.feasible_system_size_kwp < layout.requested_system_size_kwp
    assert not layout.is_fully_satisfied
