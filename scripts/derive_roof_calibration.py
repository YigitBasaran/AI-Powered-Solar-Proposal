"""Derive the fixed roof calibration from the satellite fixture.

A developer tool, not application code. It produces the calibration that a
human can then inspect and nudge in the `/dev/roof-calibration` UI. Committing
the derivation rather than a hand-typed list of pixel coordinates makes the
numbers reproducible and reviewable.

Method
------
1. **Segment the roof.** Threshold a brightness band around a seed, then apply
   a morphological *opening* (erode -> connected component -> dilate). The
   opening matters: neighbouring houses share the same roof material and are
   linked by same-brightness boundary walls, so a plain flood fill bridges
   straight into them and returns a quarter of the street. Eroding first snaps
   those thin bridges; dilating afterwards restores the roof's true extent.

2. **Choose parameters by stability, not by taste.** Sweep the brightness
   tolerance and the erosion depth, and keep the result on the widest plateau
   where the fitted footprint is dimensionally plausible for a dwelling. At the
   case site 16 of 25 parameter combinations agree to within 6%, so the answer
   is a property of the image rather than of the settings.

3. **Fit the footprint.** Convex hull (which bridges over roof vents and hip
   shading) then a minimum-area rectangle by rotating calipers. Suburban hipped
   roofs of this type are rectangular in plan.

4. **Construct the hip topology.** With a uniform pitch on all four faces the
   hip rafters run at 45 degrees in plan, so each ridge end sits half the short
   side in from its short edge, giving ``ridge_length = long - short``. That is
   a geometric consequence of equal pitch, not a guess.

Everything is emitted in SOURCE-MAP pixel coordinates on the 1280x1280
z20/scale2 raster, the only authoritative calibration space. The raster centre
is by construction the resolved case coordinate.

Usage
-----
    python scripts/derive_roof_calibration.py [--write] [--debug-image]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "fixtures" / "maps" / "satellite-fixture.png"
FIXTURE_META = REPO / "fixtures" / "maps" / "satellite-fixture.json"
OUT = REPO / "apps" / "api" / "app" / "data" / "fixed_roof_calibration.json"
DEBUG_OUT = REPO / "docs" / "images" / "roof-calibration-derivation.png"

Pt = tuple[float, float]

# A dwelling of this type. Used only to reject leaked segmentations, never to
# steer the fit towards a preferred answer.
MIN_FOOTPRINT_M2 = 40.0
MAX_FOOTPRINT_M2 = 130.0
MIN_ASPECT = 1.2
MAX_ASPECT = 2.2


# ---------------------------------------------------------------------------
# Segmentation
# ---------------------------------------------------------------------------


def erode(mask: np.ndarray) -> np.ndarray:
    p = np.pad(mask, 1, constant_values=False)
    return p[1:-1, 1:-1] & p[:-2, 1:-1] & p[2:, 1:-1] & p[1:-1, :-2] & p[1:-1, 2:]


def dilate(mask: np.ndarray) -> np.ndarray:
    p = np.pad(mask, 1, constant_values=False)
    return p[1:-1, 1:-1] | p[:-2, 1:-1] | p[2:, 1:-1] | p[1:-1, :-2] | p[1:-1, 2:]


def choose_seed(sub: np.ndarray, *, radius: int = 45) -> tuple[int, int]:
    """Pick a seed representative of the dominant surface near the centre.

    The raster centre is the resolved case coordinate and does land on the
    target roof - but on this property it lands squarely on a roof vent, which
    is 45 grey levels darker than the roof plane. Seeding blindly at the centre
    pixel grows a 400-pixel blob of vent. Taking the median of a disc (which
    the roof dominates by area) and seeding at the pixel nearest that value is
    robust to whatever small feature the coordinate happens to hit.
    """
    h, w = sub.shape
    cy, cx = h // 2, w // 2
    yy, xx = np.ogrid[:h, :w]
    disc = (yy - cy) ** 2 + (xx - cx) ** 2 <= radius**2

    target = float(np.median(sub[disc]))
    deviation = np.where(disc, np.abs(sub - target), np.inf)
    idx = int(np.argmin(deviation))
    return idx // w, idx % w


def connected_component(ok: np.ndarray, seed: tuple[int, int]) -> np.ndarray:
    """4-connected component containing ``seed`` (or the nearest live pixel)."""
    h, w = ok.shape
    sy, sx = seed
    if not ok[sy, sx]:
        ys, xs = np.nonzero(ok)
        if len(ys) == 0:
            return np.zeros_like(ok)
        i = int(np.argmin((ys - sy) ** 2 + (xs - sx) ** 2))
        sy, sx = int(ys[i]), int(xs[i])

    mask = np.zeros_like(ok)
    mask[sy, sx] = True
    queue: deque[tuple[int, int]] = deque([(sy, sx)])
    while queue:
        y, x = queue.popleft()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and not mask[ny, nx] and ok[ny, nx]:
                mask[ny, nx] = True
                queue.append((ny, nx))
    return mask


def segment_roof(
    sub: np.ndarray, seed: tuple[int, int], *, tolerance: float, opening: int
) -> np.ndarray:
    """Brightness band, then a morphological opening anchored at the seed."""
    reference = float(np.median(sub[seed[0] - 3 : seed[0] + 4, seed[1] - 3 : seed[1] + 4]))
    band = np.abs(sub - reference) <= tolerance

    mask = band
    for _ in range(opening):
        mask = erode(mask)
    mask = connected_component(mask, seed)
    if mask.sum() < 200:
        return mask
    for _ in range(opening):
        mask = dilate(mask)
    return mask & band


# ---------------------------------------------------------------------------
# Convex hull + minimum-area rectangle
# ---------------------------------------------------------------------------


def convex_hull(points: list[Pt]) -> list[Pt]:
    """Andrew's monotone chain."""
    pts = sorted(set(points))
    if len(pts) < 3:
        return pts

    def cross(o: Pt, a: Pt, b: Pt) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[Pt] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)

    upper: list[Pt] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)

    return lower[:-1] + upper[:-1]


def min_area_rect(hull: list[Pt]) -> tuple[list[Pt], float, float]:
    """Rotating calipers. Returns (corners_in_order, side_a, side_b)."""
    best: tuple[float, list[Pt], float, float] | None = None
    n = len(hull)

    for i in range(n):
        p, q = hull[i], hull[(i + 1) % n]
        ex, ey = q[0] - p[0], q[1] - p[1]
        norm = math.hypot(ex, ey)
        if norm < 1e-9:
            continue
        ux, uy = ex / norm, ey / norm
        vx, vy = -uy, ux

        us = [pt[0] * ux + pt[1] * uy for pt in hull]
        vs = [pt[0] * vx + pt[1] * vy for pt in hull]
        umin, umax, vmin, vmax = min(us), max(us), min(vs), max(vs)
        area = (umax - umin) * (vmax - vmin)

        if best is None or area < best[0]:
            corners = [
                (umin * ux + vmin * vx, umin * uy + vmin * vy),
                (umax * ux + vmin * vx, umax * uy + vmin * vy),
                (umax * ux + vmax * vx, umax * uy + vmax * vy),
                (umin * ux + vmax * vx, umin * uy + vmax * vy),
            ]
            best = (area, corners, umax - umin, vmax - vmin)

    if best is None:
        raise RuntimeError("could not fit a rectangle to the hull")
    return best[1], best[2], best[3]


# ---------------------------------------------------------------------------
# Hip roof construction
# ---------------------------------------------------------------------------


def build_hip_topology(corners: list[Pt]) -> dict[str, object]:
    """Place the ridge of a uniform-pitch hip roof on a footprint rectangle."""
    e0 = math.dist(corners[0], corners[1])
    e1 = math.dist(corners[1], corners[2])

    if e0 >= e1:
        long_len, short_len = e0, e1
        a, b, c, d = corners[0], corners[1], corners[2], corners[3]
    else:
        long_len, short_len = e1, e0
        a, b, c, d = corners[1], corners[2], corners[3], corners[0]

    ux = (b[0] - a[0]) / long_len
    uy = (b[1] - a[1]) / long_len
    inset = short_len / 2.0

    mid_ad = ((a[0] + d[0]) / 2.0, (a[1] + d[1]) / 2.0)
    mid_bc = ((b[0] + c[0]) / 2.0, (b[1] + c[1]) / 2.0)

    return {
        "corners": [a, b, c, d],
        "ridge": [
            (mid_ad[0] + ux * inset, mid_ad[1] + uy * inset),
            (mid_bc[0] - ux * inset, mid_bc[1] - uy * inset),
        ],
        "long_len_px": long_len,
        "short_len_px": short_len,
    }


# ---------------------------------------------------------------------------
# Emit calibration
# ---------------------------------------------------------------------------


def compass_of(vec: tuple[float, float]) -> float:
    """Image-space vector -> compass degrees (north 0, east 90)."""
    return (math.degrees(math.atan2(vec[0], -vec[1])) + 360.0) % 360.0


def name_for(azimuth: float) -> tuple[str, str]:
    dirs = [
        (0.0, "north", "n"),
        (90.0, "east", "e"),
        (180.0, "south", "s"),
        (270.0, "west", "w"),
    ]
    best = min(dirs, key=lambda d: abs(((azimuth - d[0] + 180.0) % 360.0) - 180.0))
    return best[1], best[2]


def build_calibration(topo: dict[str, object], m_per_px: float) -> dict[str, object]:
    a, b, c, d = topo["corners"]  # type: ignore[misc]
    r0, r1 = topo["ridge"]  # type: ignore[misc]

    verts: dict[str, Pt] = {
        "v_corner_a": a,
        "v_corner_b": b,
        "v_corner_c": c,
        "v_corner_d": d,
        "v_ridge_0": r0,
        "v_ridge_1": r1,
    }

    facet_defs = [
        (
            "v_corner_a",
            "v_corner_b",
            ["v_corner_a", "v_corner_b", "v_ridge_1", "v_ridge_0"],
        ),
        (
            "v_corner_c",
            "v_corner_d",
            ["v_corner_c", "v_corner_d", "v_ridge_0", "v_ridge_1"],
        ),
        ("v_corner_d", "v_corner_a", ["v_corner_d", "v_corner_a", "v_ridge_0"]),
        ("v_corner_b", "v_corner_c", ["v_corner_b", "v_corner_c", "v_ridge_1"]),
    ]

    edges: list[dict[str, str]] = []
    for i, (s, e) in enumerate(
        [
            ("v_corner_a", "v_corner_b"),
            ("v_corner_b", "v_corner_c"),
            ("v_corner_c", "v_corner_d"),
            ("v_corner_d", "v_corner_a"),
        ]
    ):
        edges.append(
            {
                "id": f"eave_{i}",
                "start_vertex_id": s,
                "end_vertex_id": e,
                "edge_type": "eave",
            }
        )
    for i, (s, e) in enumerate(
        [
            ("v_corner_a", "v_ridge_0"),
            ("v_corner_d", "v_ridge_0"),
            ("v_corner_b", "v_ridge_1"),
            ("v_corner_c", "v_ridge_1"),
        ]
    ):
        edges.append(
            {
                "id": f"hip_{i}",
                "start_vertex_id": s,
                "end_vertex_id": e,
                "edge_type": "hip",
            }
        )
    edges.append(
        {
            "id": "ridge_0",
            "start_vertex_id": "v_ridge_0",
            "end_vertex_id": "v_ridge_1",
            "edge_type": "ridge",
        }
    )

    facets = []
    for e0, e1, vids in facet_defs:
        poly = [verts[v] for v in vids]
        cx = sum(p[0] for p in poly) / len(poly)
        cy = sum(p[1] for p in poly) / len(poly)
        mid = ((verts[e0][0] + verts[e1][0]) / 2.0, (verts[e0][1] + verts[e1][1]) / 2.0)
        az = compass_of((mid[0] - cx, mid[1] - cy))
        name, short = name_for(az)

        eave_edge_id = next(
            edge["id"]
            for edge in edges
            if edge["edge_type"] == "eave"
            and {edge["start_vertex_id"], edge["end_vertex_id"]} == {e0, e1}
        )
        facets.append(
            {
                "id": f"facet_{short}",
                "label": f"{name.capitalize()} facet",
                "vertex_ids": vids,
                "eave_edge_id": eave_edge_id,
                "eave_vertex_ids": [e0, e1],
                "approx_compass_azimuth_deg": round(az, 2),
                "shape": "trapezoid" if len(vids) == 4 else "triangle",
            }
        )

    long_m = topo["long_len_px"] * m_per_px  # type: ignore[operator]
    short_m = topo["short_len_px"] * m_per_px  # type: ignore[operator]

    return {
        "id": "case_fixed_roof",
        "description": (
            "Hipped roof of the fixed case property. Coordinates are SOURCE-MAP "
            "pixels on the 1280x1280 z20/scale2 raster - never viewport or "
            "fixture-crop pixels. The raster centre is the resolved case coordinate."
        ),
        "derivation": (
            "Brightness band + morphological opening anchored at a median-selected "
            "seed, convex hull, minimum-area rectangle by rotating calipers, then "
            "uniform-pitch hip construction (ridge = long - short, hips at 45 deg "
            "in plan). Reproduce with scripts/derive_roof_calibration.py."
        ),
        "source_raster": {
            "width_px": 1280,
            "height_px": 1280,
            "ground_m_per_source_px": m_per_px,
        },
        "pitch_deg": 25.0,
        "vertices": [
            {"id": vid, "source_pixel": {"x": round(p[0], 2), "y": round(p[1], 2)}}
            for vid, p in verts.items()
        ],
        "edges": edges,
        "facets": facets,
        "measured": {
            "footprint_long_m": round(long_m, 3),
            "footprint_short_m": round(short_m, 3),
            "footprint_area_m2": round(long_m * short_m, 2),
            "ridge_m": round(math.dist(r0, r1) * m_per_px, 3),
        },
    }


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--debug-image", action="store_true")
    ap.add_argument("--window", type=int, default=460)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    meta = json.loads(FIXTURE_META.read_text(encoding="utf-8"))
    m_per_px = float(meta["ground_m_per_source_px"])

    img = Image.open(FIXTURE).convert("RGB")
    gray = np.asarray(img.convert("L"), dtype=np.float32)

    cx, cy = img.width // 2, img.height // 2
    half = args.window // 2
    x0, y0 = cx - half, cy - half
    sub = gray[y0 : cy + half, x0 : cx + half]

    seed = choose_seed(sub)
    print(f"seed (window px)   : {seed[1]},{seed[0]}  value={sub[seed]:.0f}")

    candidates = []
    for tol in (14, 17, 20, 23, 26):
        for opening in (2, 3, 4, 5, 6):
            mask = segment_roof(sub, seed, tolerance=float(tol), opening=opening)
            if mask.sum() < 500:
                continue
            ys, xs = np.nonzero(mask)
            # Lift out of window coordinates into SOURCE-MAP pixels immediately.
            # Everything downstream - calibration, geometry, panel layout - is
            # defined in source pixels, so the window origin must not leak past
            # this line.
            pts: list[Pt] = [(float(x + x0), float(y + y0)) for x, y in zip(xs, ys, strict=True)]
            try:
                corners, sa, sb = min_area_rect(convex_hull(pts))
            except RuntimeError:
                continue
            long_px, short_px = max(sa, sb), min(sa, sb)
            area = long_px * short_px * m_per_px * m_per_px
            aspect = long_px / short_px if short_px else 0.0
            plausible = (
                MIN_FOOTPRINT_M2 <= area <= MAX_FOOTPRINT_M2 and MIN_ASPECT <= aspect <= MAX_ASPECT
            )
            if args.verbose:
                print(
                    f"   tol={tol:2d} open={opening} {long_px * m_per_px:5.2f}x"
                    f"{short_px * m_per_px:5.2f}m {area:6.1f}m2 "
                    f"{'ok' if plausible else 'rejected'}"
                )
            if plausible:
                candidates.append((area, corners, tol, opening))

    if not candidates:
        print("ERROR: no plausible footprint found", file=sys.stderr)
        return 1

    areas = [c[0] for c in candidates]
    median_area = float(np.median(areas))
    area, corners, tol, opening = min(candidates, key=lambda c: abs(c[0] - median_area))
    spread = (max(areas) - min(areas)) / median_area

    print(f"plateau            : {len(candidates)}/25 combos plausible, spread {spread * 100:.1f}%")
    print(f"chosen             : tolerance={tol} opening={opening} (median of plateau)")

    topo = build_hip_topology(corners)
    calib = build_calibration(topo, m_per_px)
    meas = calib["measured"]  # type: ignore[index]

    print(
        f"footprint          : {meas['footprint_long_m']} x {meas['footprint_short_m']} m "  # type: ignore[index]
        f"= {meas['footprint_area_m2']} m2"
    )  # type: ignore[index]
    print(f"ridge              : {meas['ridge_m']} m")  # type: ignore[index]
    print("facets             :")
    for f in calib["facets"]:  # type: ignore[union-attr]
        print(f"   {f['id']:<9} az={f['approx_compass_azimuth_deg']:>6.1f}  {f['shape']}")

    if args.debug_image:
        DEBUG_OUT.parent.mkdir(parents=True, exist_ok=True)
        prev = img.copy()
        d = ImageDraw.Draw(prev)
        vp = {v["id"]: (v["source_pixel"]["x"], v["source_pixel"]["y"]) for v in calib["vertices"]}  # type: ignore[union-attr,index]
        colours = {
            "eave": (255, 255, 255),
            "hip": (110, 215, 255),
            "ridge": (255, 195, 55),
        }
        for e in calib["edges"]:  # type: ignore[union-attr]
            d.line(
                [vp[e["start_vertex_id"]], vp[e["end_vertex_id"]]],
                fill=colours[e["edge_type"]],
                width=3,
            )
        for p in vp.values():
            d.ellipse([p[0] - 5, p[1] - 5, p[0] + 5, p[1] + 5], fill=(255, 60, 60))
        # Frame on the roof's own bounding box so nothing is clipped.
        margin = 45
        bx0 = int(min(p[0] for p in vp.values())) - margin
        by0 = int(min(p[1] for p in vp.values())) - margin
        bx1 = int(max(p[0] for p in vp.values())) + margin
        by1 = int(max(p[1] for p in vp.values())) + margin
        prev.crop((bx0, by0, bx1, by1)).resize(
            ((bx1 - bx0) * 3, (by1 - by0) * 3), Image.LANCZOS
        ).save(DEBUG_OUT)
        print(f"debug image        : {DEBUG_OUT}")

    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(calib, indent=2) + "\n", encoding="utf-8")
        print(f"written            : {OUT}")
    else:
        print("\n(dry run - pass --write to save)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
