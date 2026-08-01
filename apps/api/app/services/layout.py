"""Automatic panel placement.

Placement happens in each facet's **surface** coordinates - metres measured on
the sloped plane. A physical panel is 1 x 2 m there and nowhere else. Doing
this in the plan view would quietly shrink every panel by cos(pitch) along the
slope and let far too many fit.

The pipeline per facet is:

1. Transform the facet outline into surface coordinates.
2. Shrink it by the configured edge setback (negative buffer).
3. For each orientation, sweep a grid of origin offsets and tile panels,
   keeping only those *fully covered* by the usable polygon. Testing the whole
   panel footprint - not its centre - is what stops panels overhanging a hip.
4. Keep the best layout per orientation as a candidate.

Facets are then combined by an exact dynamic program that maximises expected
annual production, using per-facet specific yields supplied by a
:class:`FacetYieldRankingProvider`. With at most four facets and 24 panels the
DP is tiny, and unlike a greedy fill it stays correct if the objective is ever
made non-linear.

When the requested count cannot fit, the maximum feasible count is placed and a
capacity warning is returned. The feasible capacity - never the requested one -
is what flows on to PVGIS and the financial model.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass

from shapely.affinity import rotate
from shapely.geometry import Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.prepared import prep

from app.core.config import Settings, get_settings
from app.domain.models import (
    FacetLayout,
    PanelLayout,
    PanelOrientation,
    Point2D,
    RoofFacet,
    RoofModel,
    SolarPanel,
)
from app.services.roof import facet_surface_frame, facet_surface_polygon

logger = logging.getLogger("solarvis.layout")

OFFSET_STEP_M = 0.05


@dataclass(frozen=True)
class PanelPlacement:
    """A panel's footprint in surface coordinates."""

    u: float
    v: float
    width_u: float
    height_v: float

    @property
    def corners(self) -> list[Point2D]:
        return [
            Point2D(x=self.u, y=self.v),
            Point2D(x=self.u + self.width_u, y=self.v),
            Point2D(x=self.u + self.width_u, y=self.v + self.height_v),
            Point2D(x=self.u, y=self.v + self.height_v),
        ]


@dataclass
class FacetCandidate:
    """One feasible way to fill a facet.

    `placements` are expressed in the facet's surface frame *rotated by
    `rotation_deg` about `rotation_origin`*, which is what lets the tiler stay a
    simple axis-aligned sweep at every angle. `_to_panel` turns them back.
    """

    facet_id: str
    orientation: PanelOrientation
    placements: list[PanelPlacement]
    rotation_deg: float = 0.0
    rotation_origin: tuple[float, float] = (0.0, 0.0)

    @property
    def max_count(self) -> int:
        return len(self.placements)


def _orientation_dims(orientation: PanelOrientation, settings: Settings) -> tuple[float, float]:
    """(width along u, height along v) in metres, on the roof surface."""
    w, h = settings.panel_width_m, settings.panel_height_m
    return (w, h) if orientation is PanelOrientation.PORTRAIT else (h, w)


def _usable_polygon(roof: RoofModel, facet: RoofFacet, settings: Settings) -> BaseGeometry | None:
    """The part of a facet a panel may stand on, in surface coordinates.

    Returns a Polygon, or a MultiPolygon when an obstruction or a large setback
    splits the facet in two. Both are left intact rather than reduced to the
    largest piece: `_tile` only asks for `.bounds` and a containment test, and
    both work on either, so keeping every part costs nothing and discarding one
    would silently lose roof.

    An obstruction in the middle of a facet becomes an interior ring, and the
    full-footprint containment test in `_tile` is what makes that bite - a
    centre-point test would happily straddle a chimney.
    """
    surface = facet_surface_polygon(roof, facet)
    poly: BaseGeometry = Polygon([(p.x, p.y) for p in surface])
    if not poly.is_valid:
        poly = poly.buffer(0)
    if poly.is_empty or poly.area <= 0:
        return None

    if settings.roof_edge_setback_m > 0:
        poly = poly.buffer(-settings.roof_edge_setback_m, join_style=2)
        if poly.is_empty or poly.area <= 0:
            return None

    obstructions = roof.obstructions_on(facet.id)
    if obstructions:
        frame = facet_surface_frame(roof, facet)
        for obstruction in obstructions:
            hole = Polygon(
                [(p.x, p.y) for p in frame.polygon_to_surface(obstruction.projected_metric_polygon)]
            )
            if not hole.is_valid:
                hole = hole.buffer(0)
            poly = poly.difference(hole)
        if poly.is_empty or poly.area <= 0:
            logger.info("facet %s is fully obstructed", facet.id)
            return None

    return poly


def _tile(
    usable: BaseGeometry,
    width_u: float,
    height_v: float,
    gap: float,
    step: float = OFFSET_STEP_M,
) -> list[PanelPlacement]:
    """Best tiling of one orientation, over a bounded sweep of grid offsets.

    Sweeping offsets matters: anchoring at the polygon's bounding-box corner
    frequently wastes a whole row against a sloping hip. The sweep is bounded
    by one grid pitch, because offsets beyond that repeat.

    `step` coarsens that sweep. A coarse sweep undercounts - it can miss the
    offset that fits one more row - so it is only ever used to *rank* rotation
    angles cheaply, never to produce a layout. Every angle that survives the
    ranking is re-tiled at full resolution.
    """
    min_u, min_v, max_u, max_v = usable.bounds
    pitch_u = width_u + gap
    pitch_v = height_v + gap
    if max_u - min_u < width_u or max_v - min_v < height_v:
        return []

    contains = prep(usable)
    best: list[PanelPlacement] = []

    n_off_u = max(1, round(pitch_u / step))
    n_off_v = max(1, round(pitch_v / step))

    for i in range(n_off_u):
        offset_u = i * step
        for j in range(n_off_v):
            offset_v = j * step

            placements: list[PanelPlacement] = []
            v = min_v + offset_v
            while v + height_v <= max_v + 1e-9:
                u = min_u + offset_u
                while u + width_u <= max_u + 1e-9:
                    footprint = box(u, v, u + width_u, v + height_v)
                    if contains.covers(footprint):
                        placements.append(PanelPlacement(u, v, width_u, height_v))
                    u += pitch_u
                v += pitch_v

            if len(placements) > len(best):
                best = placements

    # Fill from the eave upward, then along the eave, so that taking a prefix
    # yields a compact, bottom-anchored block of continuous rows.
    best.sort(key=lambda p: (round(p.v, 6), round(p.u, 6)))
    return best


# The offset sweep is the expensive part of placement, and the roof is static
# for the case property. Cache on everything the tiling actually depends on, so
# a settings change still invalidates it.
_CANDIDATE_CACHE: dict[tuple[object, ...], list[FacetCandidate]] = {}


def _candidate_cache_key(
    roof: RoofModel, facet: RoofFacet, settings: Settings
) -> tuple[object, ...]:
    return (
        roof.id,
        facet.id,
        round(roof.ground_m_per_source_px, 9),
        round(facet.pitch_deg, 6),
        # The facet's own outline. `roof.id` is a profile name, not a content
        # hash, so without this an edited calibration would be served a layout
        # tiled against the previous geometry.
        tuple((round(p.x, 6), round(p.y, 6)) for p in facet.projected_metric_polygon),
        tuple(
            (o.id, tuple((round(p.x, 6), round(p.y, 6)) for p in o.projected_metric_polygon))
            for o in roof.obstructions_on(facet.id)
        ),
        settings.panel_width_m,
        settings.panel_height_m,
        settings.panel_gap_m,
        settings.roof_edge_setback_m,
        settings.panel_rotation_step_deg,
        settings.panel_rotation_max_deg,
    )


#: Angles kept from the coarse ranking pass and re-tiled at full resolution.
#: Measured on the case roof: 2, 3 and 5 all reach the same 22 panels, at 3.8,
#: 5.4 and 8.5 seconds of cold start. Three keeps a margin for roofs where the
#: coarse pass ranks less cleanly, without paying for the tail.
_ROTATION_SHORTLIST = 3

#: Offset step used while ranking angles. Coarse on purpose - see `_tile`.
_ROTATION_SCAN_STEP_M = 0.25


def _rotation_angles(settings: Settings) -> list[float]:
    """Angles to try, always including 0 so the search cannot regress."""
    step = settings.panel_rotation_step_deg
    if step <= 0:
        return [0.0]
    limit = settings.panel_rotation_max_deg
    count = int(limit / step)
    return sorted({0.0, *(round(i * step, 6) for i in range(count) if i * step < limit)})


def _turn(placement: PanelPlacement, degrees: float, origin: tuple[float, float]) -> list[Point2D]:
    """A placement's corners, rotated back into the facet's own surface frame."""
    if degrees == 0.0:
        return [Point2D(x=p.x, y=p.y) for p in placement.corners]
    radians = math.radians(degrees)
    cos, sin = math.cos(radians), math.sin(radians)
    ox, oy = origin
    out = []
    for corner in placement.corners:
        dx, dy = corner.x - ox, corner.y - oy
        out.append(Point2D(x=ox + dx * cos - dy * sin, y=oy + dx * sin + dy * cos))
    return out


def build_facet_candidates(
    roof: RoofModel, facet: RoofFacet, settings: Settings | None = None
) -> list[FacetCandidate]:
    """Every way this facet could be filled, one per (orientation, angle) kept.

    Rotating the array is searched in two passes because a full offset sweep at
    every angle is roughly twenty times the cost of the whole placement stage.
    Pass one ranks angles with a coarse offset sweep; pass two re-tiles the best
    few properly. Angle 0 is always in both, so the result can never be worse
    than the un-rotated layout that a coarse pass might have mis-ranked.
    """
    settings = settings or get_settings()

    key = _candidate_cache_key(roof, facet, settings)
    cached = _CANDIDATE_CACHE.get(key)
    if cached is not None:
        return cached

    usable = _usable_polygon(roof, facet, settings)
    if usable is None:
        logger.info("facet %s has no usable area after setback", facet.id)
        _CANDIDATE_CACHE[key] = []
        return []

    centroid = usable.centroid
    origin = (centroid.x, centroid.y)
    angles = _rotation_angles(settings)

    turned: dict[float, BaseGeometry] = {
        angle: usable if angle == 0.0 else rotate(usable, -angle, origin=centroid)
        for angle in angles
    }

    candidates: list[FacetCandidate] = []
    for orientation in (PanelOrientation.LANDSCAPE, PanelOrientation.PORTRAIT):
        width_u, height_v = _orientation_dims(orientation, settings)

        if len(angles) > 1:
            ranked = sorted(
                angles,
                key=lambda a: (
                    -len(
                        _tile(
                            turned[a],
                            width_u,
                            height_v,
                            settings.panel_gap_m,
                            step=_ROTATION_SCAN_STEP_M,
                        )
                    ),
                    a,
                ),
            )
            shortlist = sorted({0.0, *ranked[:_ROTATION_SHORTLIST]})
        else:
            shortlist = angles

        best: FacetCandidate | None = None
        for angle in shortlist:
            placements = _tile(turned[angle], width_u, height_v, settings.panel_gap_m)
            if not placements:
                continue
            # Ties go to the smaller angle, so a rotation is only ever chosen
            # when it is strictly better than lying parallel to the eave.
            if best is None or len(placements) > best.max_count:
                best = FacetCandidate(facet.id, orientation, placements, angle, origin)

        if best is not None:
            candidates.append(best)

    if any(c.rotation_deg for c in candidates):
        logger.info(
            "facet %s: array rotation chosen %s",
            facet.id,
            {c.orientation.value: (c.rotation_deg, c.max_count) for c in candidates},
        )

    _CANDIDATE_CACHE[key] = candidates
    return candidates


# ---------------------------------------------------------------------------
# Combination search
# ---------------------------------------------------------------------------


def _allocate(
    candidates_by_facet: list[list[FacetCandidate]],
    yields: Mapping[str, float],
    target: int,
    panel_kwp: float,
) -> dict[str, tuple[FacetCandidate, int]]:
    """Exact DP maximising expected annual production for a total panel count.

    ``dp[k]`` is the best (production, allocation) achievable with exactly ``k``
    panels across the facets considered so far. Ties break on fewer facets used
    (a more compact install) and then on facet id, so the result is stable.
    """
    NEG = float("-inf")
    dp: list[tuple[float, int, dict[str, tuple[FacetCandidate, int]]]] = [
        (NEG, 0, {}) for _ in range(target + 1)
    ]
    dp[0] = (0.0, 0, {})

    for candidates in candidates_by_facet:
        if not candidates:
            continue
        facet_id = candidates[0].facet_id
        yield_ = yields.get(facet_id, 0.0)
        nxt = list(dp)

        for k in range(target + 1):
            if dp[k][0] == NEG:
                continue
            base_production, base_facets, base_alloc = dp[k]
            for candidate in candidates:
                for j in range(1, min(candidate.max_count, target - k) + 1):
                    production = base_production + j * panel_kwp * yield_
                    facets_used = base_facets + 1
                    slot = k + j
                    current = nxt[slot]
                    better = production > current[0] + 1e-9 or (
                        abs(production - current[0]) <= 1e-9 and facets_used < current[1]
                    )
                    if better:
                        alloc = dict(base_alloc)
                        alloc[facet_id] = (candidate, j)
                        nxt[slot] = (production, facets_used, alloc)
        dp = nxt

    for k in range(target, -1, -1):
        if dp[k][0] > NEG:
            return dp[k][2]
    return {}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def _to_panel(
    roof: RoofModel,
    facet: RoofFacet,
    placement: PanelPlacement,
    orientation: PanelOrientation,
    index: int,
    settings: Settings,
    rotation_deg: float = 0.0,
    rotation_origin: tuple[float, float] = (0.0, 0.0),
) -> SolarPanel:
    frame = facet_surface_frame(roof, facet)
    # Placements are tiled in the rotated frame; the panel is described in the
    # facet's own frame, so the rotation is undone exactly here and nowhere else.
    surface = _turn(placement, rotation_deg, rotation_origin)
    projected = frame.polygon_to_projected(surface)

    cx = roof.source_width_px / 2.0
    cy = roof.source_height_px / 2.0
    scale = roof.ground_m_per_source_px
    pixels = [Point2D(x=cx + p.x / scale, y=cy - p.y / scale) for p in projected]

    return SolarPanel(
        id=f"{facet.id}_panel_{index:02d}",
        facet_id=facet.id,
        orientation=orientation,
        rotation_deg=rotation_deg,
        power_wp=settings.panel_power_wp,
        surface_width_m=placement.width_u,
        surface_height_m=placement.height_v,
        surface_polygon=surface,
        projected_metric_polygon=projected,
        source_pixel_polygon=pixels,
    )


def generate_layout(
    roof: RoofModel,
    requested_system_size_kwp: float,
    yields: Mapping[str, float],
    settings: Settings | None = None,
) -> PanelLayout:
    """Place panels to maximise production for the requested count.

    Takes the facet yields as data rather than a provider to await. They are
    necessarily retrieved before layout can begin - the optimiser ranks the
    facets against each other, so it needs all of them - which makes the port
    that used to live here a decorated way of building a dict the caller
    already had. Passing the mapping makes this a pure function, and lets the
    coverage check below be an error rather than a silent zero.
    """
    settings = settings or get_settings()

    if requested_system_size_kwp not in settings.allowed_system_sizes_kwp:
        raise ValueError(
            f"{requested_system_size_kwp} kWp is not one of {settings.allowed_system_sizes_kwp}"
        )

    missing = [f.id for f in roof.facets if f.id not in yields]
    if missing:
        # `_allocate` reads `yields.get(facet_id, 0.0)`, so a missing facet used
        # to score zero and simply never receive panels - a silently different
        # layout rather than a failure.
        raise ValueError(f"no yield supplied for {missing}; every facet must be ranked")

    requested_count = settings.required_panel_count(requested_system_size_kwp)

    candidates_by_facet = [build_facet_candidates(roof, f, settings) for f in roof.facets]
    capacity = sum(max((c.max_count for c in cs), default=0) for cs in candidates_by_facet)

    logger.info(
        "layout | requested=%d panels, facet capacity=%s, total=%d",
        requested_count,
        {
            roof.facets[i].id: max((c.max_count for c in cs), default=0)
            for i, cs in enumerate(candidates_by_facet)
        },
        capacity,
    )

    target = min(requested_count, capacity)
    allocation = _allocate(candidates_by_facet, yields, target, settings.panel_power_kwp)

    facet_layouts: list[FacetLayout] = []
    placed = 0
    for facet in roof.facets:
        if facet.id not in allocation:
            continue
        candidate, count = allocation[facet.id]
        chosen = candidate.placements[:count]
        panels = [
            _to_panel(
                roof,
                facet,
                p,
                candidate.orientation,
                i,
                settings,
                candidate.rotation_deg,
                candidate.rotation_origin,
            )
            for i, p in enumerate(chosen)
        ]
        placed += len(panels)
        facet_layouts.append(
            FacetLayout(
                facet_id=facet.id,
                orientation=candidate.orientation,
                rotation_deg=candidate.rotation_deg,
                panels=panels,
            )
        )

    feasible_kwp = round(placed * settings.panel_power_kwp, 6)

    warning: str | None = None
    if placed < requested_count:
        warning = (
            f"The roof physically accommodates {placed} panels "
            f"({feasible_kwp:.1f} kWp), not the {requested_count} panels "
            f"({requested_system_size_kwp} kWp) requested. Production and "
            f"financials below are based on the {feasible_kwp:.1f} kWp that fits."
        )
        logger.warning("capacity shortfall: %d of %d panels placed", placed, requested_count)

    return PanelLayout(
        requested_system_size_kwp=requested_system_size_kwp,
        requested_panel_count=requested_count,
        placed_panel_count=placed,
        feasible_system_size_kwp=feasible_kwp,
        facet_layouts=facet_layouts,
        capacity_warning=warning,
    )


def assert_layout_valid(roof: RoofModel, layout: PanelLayout, settings: Settings) -> None:
    """Post-conditions checked in production, not only in tests.

    A layout that silently overhangs a hip or double-books a square metre would
    still render plausibly and still produce a confident-looking proposal, so
    these are worth paying for on every request.
    """
    for facet_layout in layout.facet_layouts:
        facet = roof.facet(facet_layout.facet_id)
        usable = _usable_polygon(roof, facet, settings)
        if usable is None:
            raise AssertionError(f"facet {facet.id} has panels but no usable area")

        obstructions = roof.obstructions_on(facet.id)
        holes = []
        if obstructions:
            frame = facet_surface_frame(roof, facet)
            holes = [
                (
                    o.id,
                    Polygon(
                        [(p.x, p.y) for p in frame.polygon_to_surface(o.projected_metric_polygon)]
                    ),
                )
                for o in obstructions
            ]

        boxes = []
        for panel in facet_layout.panels:
            poly = Polygon([(p.x, p.y) for p in panel.surface_polygon])
            # Checked before the containment test purely so the message names the
            # cause. `usable` already excludes the obstructions, so this cannot
            # pass while that one fails - it just says "chimney" instead of
            # "somewhere outside the facet".
            for obstruction_id, hole in holes:
                if poly.intersection(hole).area > 1e-9:
                    raise AssertionError(f"panel {panel.id} stands on obstruction {obstruction_id}")
            if not usable.covers(poly):
                raise AssertionError(f"panel {panel.id} is not fully inside {facet.id}")
            boxes.append(poly)

        for i, a in enumerate(boxes):
            for b in boxes[i + 1 :]:
                if a.intersection(b).area > 1e-9:
                    raise AssertionError(f"panels overlap on {facet.id}")

    expected_kwp = round(layout.placed_panel_count * settings.panel_power_kwp, 6)
    if abs(layout.feasible_system_size_kwp - expected_kwp) > 1e-6:
        raise AssertionError("installed power does not equal count x panel rating")

    if layout.placed_panel_count < layout.requested_panel_count and not layout.capacity_warning:
        raise AssertionError("short layout must carry a capacity warning")
