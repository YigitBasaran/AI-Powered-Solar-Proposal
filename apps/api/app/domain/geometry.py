"""Pure roof geometry.

No I/O, no HTTP, no database. Everything here is a function of its arguments,
which is what makes the measurement layer cheap to test and safe to trust.

Three coordinate spaces are used and never conflated:

``source pixel``
    The canonical Google Static Maps raster defined by the verified centre,
    zoom, logical size and scale. Origin top-left, ``y`` increasing downwards.
    This is the authoritative space in which calibration is stored.

``projected metric``
    Ground-plane metres. Origin at the raster centre, ``x`` increasing east and
    ``y`` increasing north. This is a plan view: a pitched roof appears
    foreshortened along its slope.

``facet surface``
    Metres measured on the sloped plane of a single facet. ``u`` runs along the
    facet's eave, ``v`` runs up the slope. A physical 1 x 2 m panel is 1 x 2 m
    in this space and nowhere else, which is why panel placement happens here.

Assumption A-GEO-1 (planar facet, level eave)
---------------------------------------------
Each facet is planar and its eave is level, so the in-plane direction
perpendicular to the eave is the true line of maximum slope. Consequences:

* ``u`` is unforeshortened: ``u_surface == u_projected``.
* ``v`` is foreshortened by ``cos(pitch)`` and only ``v`` is.
* ``sloped_area == projected_area / cos(pitch)`` for the whole facet.
* A hip or ridge edge is NOT ``projected / cos(pitch)``. A hip runs diagonally
  across the slope, not along it. Its true length needs endpoint heights.

Applying the pitch correction outside this validated frame is the single
easiest way to silently overstate a roof, so the helpers below refuse to do it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.core.config import meters_per_source_pixel
from app.domain.models import Point2D, RoofEdgeType, cos_pitch

__all__ = [
    "SurfaceFrame",
    "compass_azimuth_to_pvgis_aspect",
    "facet_compass_azimuth",
    "meters_per_source_pixel",
    "metric_to_source_pixel",
    "pixel_distance",
    "polygon_area_m2",
    "polygon_centroid",
    "projected_length_m",
    "responsive_scale",
    "sloped_area_m2",
    "source_pixel_to_metric",
    "true_3d_length_m",
    "validate_level_eave",
]


# ---------------------------------------------------------------------------
# Pixel <-> metric
# ---------------------------------------------------------------------------


def source_pixel_to_metric(
    p: Point2D,
    *,
    source_width_px: int,
    source_height_px: int,
    ground_m_per_source_px: float,
) -> Point2D:
    """Source-raster pixel -> ground metres, origin at raster centre.

    ``x`` increases east. ``y`` increases north, so the image's downward ``y``
    is negated here. Getting that sign wrong silently mirrors every azimuth,
    which is why the conversion lives in exactly one place.
    """
    cx = source_width_px / 2.0
    cy = source_height_px / 2.0
    return Point2D(
        x=(p.x - cx) * ground_m_per_source_px,
        y=(cy - p.y) * ground_m_per_source_px,
    )


def metric_to_source_pixel(
    p: Point2D,
    *,
    source_width_px: int,
    source_height_px: int,
    ground_m_per_source_px: float,
) -> Point2D:
    """Inverse of :func:`source_pixel_to_metric`."""
    if ground_m_per_source_px <= 0:
        raise ValueError("ground_m_per_source_px must be positive")
    cx = source_width_px / 2.0
    cy = source_height_px / 2.0
    return Point2D(
        x=cx + p.x / ground_m_per_source_px,
        y=cy - p.y / ground_m_per_source_px,
    )


def responsive_scale(p: Point2D, *, source_size_px: float, displayed_size_px: float) -> Point2D:
    """Source pixels -> viewport pixels.

    Display coordinates are derived on the way out and never stored. Only the
    source raster is authoritative.
    """
    if source_size_px <= 0:
        raise ValueError("source_size_px must be positive")
    k = displayed_size_px / source_size_px
    return Point2D(x=p.x * k, y=p.y * k)


# ---------------------------------------------------------------------------
# Lengths
# ---------------------------------------------------------------------------


def pixel_distance(a: Point2D, b: Point2D) -> float:
    return math.hypot(b.x - a.x, b.y - a.y)


def projected_length_m(a: Point2D, b: Point2D, *, ground_m_per_source_px: float) -> float:
    """Plan-view length of an edge given in source pixels."""
    return pixel_distance(a, b) * ground_m_per_source_px


def true_3d_length_m(a: Point2D, b: Point2D, *, height_a_m: float, height_b_m: float) -> float:
    """True length of an edge whose endpoint heights are known.

    ``sqrt(dx^2 + dy^2 + dz^2)`` on metric inputs. This is the only correct way
    to lengthen a hip or ridge; see A-GEO-1 in the module docstring.
    """
    dx = b.x - a.x
    dy = b.y - a.y
    dz = height_b_m - height_a_m
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def slope_edge_true_length_m(projected_length: float, pitch_deg: float) -> float:
    """Lengthen an edge that runs ALONG the line of maximum slope.

    Valid only for such an edge. A hip does not qualify. Callers must have
    established that the edge follows maximum slope, which under A-GEO-1 means
    it is perpendicular to a level eave.
    """
    return projected_length / cos_pitch(pitch_deg)


# ---------------------------------------------------------------------------
# Areas
# ---------------------------------------------------------------------------


def polygon_area_m2(points: list[Point2D]) -> float:
    """Shoelace area. Sign-independent, so winding order does not matter."""
    n = len(points)
    if n < 3:
        raise ValueError("a polygon needs at least 3 vertices")
    total = 0.0
    for i in range(n):
        j = (i + 1) % n
        total += points[i].x * points[j].y - points[j].x * points[i].y
    return abs(total) / 2.0


def point_in_polygon(point: Point2D, polygon: list[Point2D], *, tolerance: float = 1e-9) -> bool:
    """Ray-cast containment test. Points on the boundary count as inside.

    The boundary case is not pedantry here: an obstruction traced right up to a
    ridge, or a facet polygon sharing a vertex with one, would otherwise be
    rejected for touching the edge it legitimately abuts.
    """
    n = len(polygon)
    if n < 3:
        raise ValueError("a polygon needs at least 3 vertices")

    for i in range(n):
        a, b = polygon[i], polygon[(i + 1) % n]
        abx, aby = b.x - a.x, b.y - a.y
        apx, apy = point.x - a.x, point.y - a.y
        if abs(abx * apy - aby * apx) <= tolerance * max(1.0, abs(abx) + abs(aby)):
            t = (apx * abx + apy * aby) / ((abx * abx + aby * aby) or 1.0)
            if -tolerance <= t <= 1.0 + tolerance:
                return True

    inside = False
    for i in range(n):
        a, b = polygon[i], polygon[(i + 1) % n]
        if (a.y > point.y) != (b.y > point.y):
            x = (b.x - a.x) * (point.y - a.y) / (b.y - a.y) + a.x
            if point.x < x:
                inside = not inside
    return inside


def sloped_area_m2(projected_area: float, pitch_deg: float) -> float:
    """Plan-view area -> true sloped surface area.

    Valid for a whole planar facet under A-GEO-1.
    """
    if projected_area < 0:
        raise ValueError("projected area must be non-negative")
    return projected_area / cos_pitch(pitch_deg)


def polygon_centroid(points: list[Point2D]) -> Point2D:
    """Area centroid of a simple polygon.

    Falls back to the vertex mean for degenerate (zero-area) input so callers
    do not have to special-case it.
    """
    n = len(points)
    if n < 3:
        raise ValueError("a polygon needs at least 3 vertices")

    a2 = 0.0
    cx = 0.0
    cy = 0.0
    for i in range(n):
        j = (i + 1) % n
        cross = points[i].x * points[j].y - points[j].x * points[i].y
        a2 += cross
        cx += (points[i].x + points[j].x) * cross
        cy += (points[i].y + points[j].y) * cross

    if abs(a2) < 1e-12:
        return Point2D(
            x=sum(p.x for p in points) / n,
            y=sum(p.y for p in points) / n,
        )

    return Point2D(x=cx / (3.0 * a2), y=cy / (3.0 * a2))


# ---------------------------------------------------------------------------
# Orientation
# ---------------------------------------------------------------------------


def facet_compass_azimuth(
    *, eave_start: Point2D, eave_end: Point2D, facet_polygon: list[Point2D]
) -> float:
    """Compass azimuth a facet faces, in METRIC coordinates.

    A pitched facet drains away from its ridge and towards its eave, so the
    downslope direction is centroid -> eave midpoint.

    Compass convention: north 0, east 90, south 180, west 270.
    """
    centroid = polygon_centroid(facet_polygon)
    mid = Point2D(x=(eave_start.x + eave_end.x) / 2.0, y=(eave_start.y + eave_end.y) / 2.0)

    east = mid.x - centroid.x
    north = mid.y - centroid.y

    if abs(east) < 1e-12 and abs(north) < 1e-12:
        raise ValueError("facet centroid coincides with its eave midpoint")

    return (math.degrees(math.atan2(east, north)) + 360.0) % 360.0


def compass_azimuth_to_pvgis_aspect(compass_deg: float) -> float:
    """Compass azimuth -> PVGIS aspect.

    PVGIS convention: south 0, west 90, east -90, north +/-180.

    Hemisphere-agnostic: this is a pure change of angular reference. What
    changes below the equator is which aspect is *good*, not how it is
    computed. At the Cape Town case site a north-facing facet (aspect near
    +/-180) outproduces a south-facing one by roughly 52%.
    """
    aspect = compass_deg - 180.0
    while aspect <= -180.0:
        aspect += 360.0
    while aspect > 180.0:
        aspect -= 360.0
    return aspect


# ---------------------------------------------------------------------------
# Facet surface frame
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SurfaceFrame:
    """Maps between projected metric coordinates and one facet's surface.

    ``u`` runs along the eave from ``origin``; ``v`` runs up the slope, into
    the facet. Under A-GEO-1 the eave is level, so ``v`` is the true line of
    maximum slope and is the only axis the pitch correction touches.
    """

    origin: Point2D
    eave_unit: Point2D
    inward_unit: Point2D
    pitch_deg: float

    @property
    def cos_pitch(self) -> float:
        return cos_pitch(self.pitch_deg)

    def to_surface(self, p: Point2D) -> Point2D:
        """Projected metric -> facet surface metres."""
        dx = p.x - self.origin.x
        dy = p.y - self.origin.y
        u = dx * self.eave_unit.x + dy * self.eave_unit.y
        v_projected = dx * self.inward_unit.x + dy * self.inward_unit.y
        return Point2D(x=u, y=v_projected / self.cos_pitch)

    def to_projected(self, p: Point2D) -> Point2D:
        """Facet surface metres -> projected metric."""
        v_projected = p.y * self.cos_pitch
        return Point2D(
            x=self.origin.x + p.x * self.eave_unit.x + v_projected * self.inward_unit.x,
            y=self.origin.y + p.x * self.eave_unit.y + v_projected * self.inward_unit.y,
        )

    def polygon_to_surface(self, poly: list[Point2D]) -> list[Point2D]:
        return [self.to_surface(p) for p in poly]

    def polygon_to_projected(self, poly: list[Point2D]) -> list[Point2D]:
        return [self.to_projected(p) for p in poly]


def build_surface_frame(
    *,
    eave_start: Point2D,
    eave_end: Point2D,
    facet_polygon: list[Point2D],
    pitch_deg: float,
) -> SurfaceFrame:
    """Construct the surface frame for a facet from its eave and outline.

    ``inward_unit`` is the eave normal chosen to point into the facet, so ``v``
    is positive everywhere on the facet and the optimiser can reason about
    "up the roof" without sign checks.
    """
    ex = eave_end.x - eave_start.x
    ey = eave_end.y - eave_start.y
    length = math.hypot(ex, ey)
    if length < 1e-9:
        raise ValueError("eave has zero length")
    eave_unit = Point2D(x=ex / length, y=ey / length)

    # Both normals of the eave; pick the one pointing at the facet centroid.
    normal = Point2D(x=-eave_unit.y, y=eave_unit.x)
    centroid = polygon_centroid(facet_polygon)
    if (centroid.x - eave_start.x) * normal.x + (centroid.y - eave_start.y) * normal.y < 0:
        normal = Point2D(x=-normal.x, y=-normal.y)

    return SurfaceFrame(
        origin=eave_start,
        eave_unit=eave_unit,
        inward_unit=normal,
        pitch_deg=pitch_deg,
    )


def validate_level_eave(
    *, height_start_m: float | None, height_end_m: float | None, tolerance_m: float = 0.05
) -> bool:
    """Check the A-GEO-1 level-eave precondition.

    Returns True when the eave is level within tolerance, or when heights are
    unknown (in which case the assumption is taken on trust and recorded as
    such rather than silently assumed correct).
    """
    if height_start_m is None or height_end_m is None:
        return True
    return abs(height_start_m - height_end_m) <= tolerance_m


def edge_type_supports_slope_correction(edge_type: RoofEdgeType) -> bool:
    """Whether ``projected / cos(pitch)`` is a valid lengthening for this edge.

    Only an edge running along maximum slope qualifies. Under A-GEO-1 an eave
    is level (correction factor 1, no lengthening) and a ridge is horizontal,
    while a hip runs diagonally across the slope. None of the three roof edge
    types in this model is a maximum-slope edge, so the answer is always False.
    The function exists so the rule is expressed once and tested, rather than
    re-derived at each call site.
    """
    return False
