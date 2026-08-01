"""Server-rendered SVG charts for the PDF.

Rendered as inline SVG rather than by a JavaScript charting library, because
the PDF pipeline then has nothing to wait for: no script to load, no canvas to
settle, no race between Chromium's print call and a chart's animation. The
markup that leaves this module is the markup that prints.

Both charts are single-series, so neither carries a legend - the title names
the series. Marks are thin with rounded data-ends, gridlines and axes are
recessive, and all text wears ink tokens rather than the series colour.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from html import escape
from typing import Any

# Light-surface tokens. The PDF always prints on white, so only one mode is
# needed here; the web proposal themes separately.
SURFACE = "#fcfcfb"
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
BASELINE = "#c3c2b7"
SERIES = "#2a78d6"  # categorical slot 1 / sequential hue
SERIES_SOFT = "#cde2fb"
NEGATIVE = "#d03b3b"  # diverging warm pole
NEGATIVE_SOFT = "#f7d5d5"

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'


def _nice_ceiling(value: float) -> float:
    """Round an axis maximum up to a readable step."""
    if value <= 0:
        return 1.0
    magnitude: float = float(10 ** (len(str(int(value))) - 1))
    for factor in (1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 7.5, 10.0):
        candidate = float(magnitude * factor)
        if candidate >= value:
            return candidate
    return float(magnitude * 10)


def monthly_production_chart(
    monthly_kwh: list[float], *, width: int = 720, height: int = 260
) -> str:
    """Twelve monthly bars. Magnitude over time, one series."""
    if len(monthly_kwh) != 12:
        raise ValueError("monthly production needs 12 values")

    # `pad_top` leaves room for the value printed above each bar. Without it
    # the tallest month's label is clipped by the viewBox.
    pad_left, pad_right, pad_top, pad_bottom = 54, 12, 30, 34
    plot_w = width - pad_left - pad_right
    plot_h = height - pad_top - pad_bottom

    top = _nice_ceiling(max(monthly_kwh) * 1.08)
    gap = 2.0  # surface gap between adjacent bars
    slot = plot_w / 12
    bar_w = slot - gap

    parts: list[str] = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" '
        f'xmlns="http://www.w3.org/2000/svg" role="img" '
        f'aria-label="Monthly production in kilowatt hours">',
        f'<rect width="{width}" height="{height}" fill="{SURFACE}"/>',
    ]

    # Recessive gridlines, labelled on the left.
    for step in range(5):
        value = top * step / 4
        y = pad_top + plot_h - (plot_h * step / 4)
        parts.append(
            f'<line x1="{pad_left}" y1="{y:.1f}" x2="{width - pad_right}" '
            f'y2="{y:.1f}" stroke="{GRIDLINE}" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{pad_left - 8}" y="{y + 4:.1f}" text-anchor="end" '
            f'font-family="{FONT}" font-size="10" fill="{INK_MUTED}" '
            f'style="font-variant-numeric:tabular-nums">{value:,.0f}</text>'
        )

    for index, value in enumerate(monthly_kwh):
        bar_h = max(0.0, (value / top) * plot_h) if top else 0.0
        x = pad_left + index * slot + gap / 2
        y = pad_top + plot_h - bar_h
        radius = min(4.0, bar_w / 2, max(bar_h, 0.1))
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" '
            f'height="{bar_h:.1f}" rx="{radius:.1f}" fill="{SERIES}"/>'
        )
        # The figure, directly above its own bar.
        #
        # A bar chart shows *shape* well and *value* badly: reading July off
        # the gridlines gets you "about 1,100", and the one question a customer
        # actually asks of this chart is what a given month produces. The label
        # is small and recessive so the shape still leads, and tabular numerals
        # keep the twelve of them optically aligned.
        parts.append(
            f'<text x="{x + bar_w / 2:.1f}" y="{y - 5:.1f}" text-anchor="middle" '
            f'font-family="{FONT}" font-size="9" fill="{INK_MUTED}" '
            f'style="font-variant-numeric:tabular-nums">{value:,.0f}</text>'
        )
        parts.append(
            f'<text x="{x + bar_w / 2:.1f}" y="{height - 12}" text-anchor="middle" '
            f'font-family="{FONT}" font-size="10" fill="{INK_MUTED}">'
            f"{MONTHS[index]}</text>"
        )

    # Baseline sits above the month labels, anchoring the bars.
    parts.append(
        f'<line x1="{pad_left}" y1="{pad_top + plot_h}" x2="{width - pad_right}" '
        f'y2="{pad_top + plot_h}" stroke="{BASELINE}" stroke-width="1"/>'
    )
    parts.append("</svg>")
    return "".join(parts)


def cash_flow_chart(
    cash_flow: Sequence[Mapping[str, Any]],
    *,
    payback_years: float | None,
    width: int = 720,
    height: int = 300,
) -> str:
    """Cumulative cash flow over 20 years.

    Polarity is the point here - the year the project stops being a cost and
    starts being a return - so the area is split at zero using the diverging
    pair, and the crossing is direct-labelled. The line itself stays one colour.
    """
    values = [float(Decimal(str(row["cumulativeCashFlowEur"]))) for row in cash_flow]
    years = [int(row["year"]) for row in cash_flow]
    if not values:
        raise ValueError("cash flow is empty")

    pad_left, pad_right, pad_top, pad_bottom = 62, 16, 20, 34
    plot_w = width - pad_left - pad_right
    plot_h = height - pad_top - pad_bottom

    lo = min(min(values), 0.0)
    hi = max(max(values), 0.0)
    span = (hi - lo) or 1.0
    lo -= span * 0.08
    hi += span * 0.08
    span = hi - lo

    def x_of(year: int) -> float:
        return pad_left + (year / max(years)) * plot_w

    def y_of(value: float) -> float:
        return pad_top + plot_h - ((value - lo) / span) * plot_h

    zero_y = y_of(0.0)

    parts: list[str] = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" '
        f'xmlns="http://www.w3.org/2000/svg" role="img" '
        f'aria-label="Cumulative cash flow over twenty years">',
        f'<rect width="{width}" height="{height}" fill="{SURFACE}"/>',
    ]

    for step in range(5):
        value = lo + span * step / 4
        y = y_of(value)
        parts.append(
            f'<line x1="{pad_left}" y1="{y:.1f}" x2="{width - pad_right}" '
            f'y2="{y:.1f}" stroke="{GRIDLINE}" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{pad_left - 8}" y="{y + 4:.1f}" text-anchor="end" '
            f'font-family="{FONT}" font-size="10" fill="{INK_MUTED}" '
            f'style="font-variant-numeric:tabular-nums">'
            f"{value / 1000:,.0f}k</text>"
        )

    points = [(x_of(y), y_of(v)) for y, v in zip(years, values, strict=True)]

    # Split the fill at zero: below is a cost, above is a return.
    below = (
        f"M {points[0][0]:.1f} {zero_y:.1f} "
        + " ".join(f"L {x:.1f} {min(y, zero_y):.1f}" for x, y in points)
        + f" L {points[-1][0]:.1f} {zero_y:.1f} Z"
    )
    above = (
        f"M {points[0][0]:.1f} {zero_y:.1f} "
        + " ".join(f"L {x:.1f} {max(y, zero_y):.1f}" for x, y in points)
        + f" L {points[-1][0]:.1f} {zero_y:.1f} Z"
    )
    parts.append(f'<path d="{below}" fill="{NEGATIVE_SOFT}"/>')
    parts.append(f'<path d="{above}" fill="{SERIES_SOFT}"/>')

    line = " ".join(
        ("M" if i == 0 else "L") + f" {x:.1f} {y:.1f}" for i, (x, y) in enumerate(points)
    )
    parts.append(
        f'<path d="{line}" fill="none" stroke="{SERIES}" stroke-width="2" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
    )

    # The zero line carries the meaning, so it is the one emphatic rule.
    parts.append(
        f'<line x1="{pad_left}" y1="{zero_y:.1f}" x2="{width - pad_right}" '
        f'y2="{zero_y:.1f}" stroke="{INK_SECONDARY}" stroke-width="1.5"/>'
    )

    if payback_years is not None and 0 < payback_years <= max(years):
        px = pad_left + (payback_years / max(years)) * plot_w
        parts.append(
            f'<line x1="{px:.1f}" y1="{pad_top}" x2="{px:.1f}" '
            f'y2="{pad_top + plot_h}" stroke="{NEGATIVE}" stroke-width="1" '
            f'stroke-dasharray="4 3"/>'
        )
        parts.append(
            f'<circle cx="{px:.1f}" cy="{zero_y:.1f}" r="4" fill="{NEGATIVE}" '
            f'stroke="{SURFACE}" stroke-width="2"/>'
        )
        anchor = "start" if payback_years < max(years) * 0.75 else "end"
        offset = 8 if anchor == "start" else -8
        parts.append(
            f'<text x="{px + offset:.1f}" y="{pad_top + 14:.1f}" '
            f'text-anchor="{anchor}" font-family="{FONT}" font-size="11" '
            f'font-weight="600" fill="{INK_PRIMARY}">'
            f"Payback {payback_years:.1f} yr</text>"
        )

    for year in years:
        if year % 5 == 0:
            parts.append(
                f'<text x="{x_of(year):.1f}" y="{height - 12}" text-anchor="middle" '
                f'font-family="{FONT}" font-size="10" fill="{INK_MUTED}">'
                f"Year {year}</text>"
            )

    parts.append("</svg>")
    return "".join(parts)


def facet_orientation_diagram(facets: Sequence[Mapping[str, Any]], *, size: int = 190) -> str:
    """A compass rose showing where each facet points and how much it yields.

    Included because "north facet, aspect -169 degrees" is not something a
    customer can picture, and at this site the north-facing orientation is the
    whole reason the layout looks the way it does.
    """
    import math

    cx = cy = size / 2
    radius = size / 2 - 34
    yields = [float(f.get("specificYieldKwhPerKwp") or 0) for f in facets]
    best = max(yields) if yields else 1.0

    parts = [
        f'<svg viewBox="0 0 {size} {size}" width="{size}" height="{size}" '
        f'xmlns="http://www.w3.org/2000/svg" role="img" '
        f'aria-label="Facet orientations and specific yields">',
        f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="none" '
        f'stroke="{GRIDLINE}" stroke-width="1"/>',
    ]
    for label, angle in (("N", 0), ("E", 90), ("S", 180), ("W", 270)):
        rad = math.radians(angle)
        lx = cx + (radius + 14) * math.sin(rad)
        ly = cy - (radius + 14) * math.cos(rad) + 4
        parts.append(
            f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="middle" '
            f'font-family="{FONT}" font-size="10" fill="{INK_MUTED}">{label}</text>'
        )

    for facet in facets:
        azimuth = float(facet.get("compassAzimuthDeg") or 0)
        specific = float(facet.get("specificYieldKwhPerKwp") or 0)
        rad = math.radians(azimuth)
        length = radius * (0.45 + 0.55 * (specific / best if best else 0))
        ex = cx + length * math.sin(rad)
        ey = cy - length * math.cos(rad)
        # Opacity encodes relative yield; the best facet reads strongest.
        opacity = 0.35 + 0.65 * (specific / best if best else 0)
        parts.append(
            f'<line x1="{cx}" y1="{cy}" x2="{ex:.1f}" y2="{ey:.1f}" '
            f'stroke="{SERIES}" stroke-width="3" stroke-linecap="round" '
            f'opacity="{opacity:.2f}"/>'
        )
        parts.append(
            f'<circle cx="{ex:.1f}" cy="{ey:.1f}" r="4" fill="{SERIES}" opacity="{opacity:.2f}"/>'
        )

    parts.append(f'<circle cx="{cx}" cy="{cy}" r="3" fill="{INK_SECONDARY}"/>')
    parts.append("</svg>")
    return "".join(parts)


def roof_plan_svg(
    roof: Mapping[str, Any],
    layout: Mapping[str, Any],
    *,
    width: int = 420,
) -> str:
    """Plan view of the reconstruction, drawn from stored source-pixel geometry.

    A fallback for when no exported Konva snapshot is attached. Without it the
    PDF would depend on the frontend having uploaded an image, and a proposal
    generated straight from the API would show no roof at all.
    """
    facets: Sequence[Mapping[str, Any]] = roof.get("facetGeometry") or roof.get("facets") or []
    polygons = [f.get("sourcePixelPolygon") for f in facets]
    layout_facets: Sequence[Mapping[str, Any]] = layout.get("facets", [])
    panels = [p["sourcePixelPolygon"] for facet in layout_facets for p in facet.get("panels", [])]

    points = [pt for poly in polygons if poly for pt in poly]
    if not points:
        return ""

    min_x = min(p["x"] for p in points)
    max_x = max(p["x"] for p in points)
    min_y = min(p["y"] for p in points)
    max_y = max(p["y"] for p in points)
    pad = 18.0
    span_x = (max_x - min_x) + pad * 2
    span_y = (max_y - min_y) + pad * 2
    height = int(width * span_y / span_x)

    def path_of(poly: list[dict[str, float]]) -> str:
        return (
            " ".join(
                ("M" if i == 0 else "L") + f" {p['x'] - min_x + pad:.1f} {p['y'] - min_y + pad:.1f}"
                for i, p in enumerate(poly)
            )
            + " Z"
        )

    parts = [
        f'<svg viewBox="0 0 {span_x:.0f} {span_y:.0f}" width="100%" height="{height}" '
        f'xmlns="http://www.w3.org/2000/svg" role="img" '
        f'aria-label="Plan view of the roof reconstruction and panel layout">',
        f'<rect width="{span_x:.0f}" height="{span_y:.0f}" fill="{SURFACE}"/>',
    ]
    for poly in polygons:
        if poly:
            parts.append(
                f'<path d="{path_of(poly)}" fill="#eef2f6" stroke="{INK_SECONDARY}" '
                f'stroke-width="1.2" stroke-linejoin="round"/>'
            )
    for poly in panels:
        parts.append(
            f'<path d="{path_of(poly)}" fill="{SERIES}" fill-opacity="0.85" '
            f'stroke="{SURFACE}" stroke-width="0.6"/>'
        )
    parts.append("</svg>")
    return "".join(parts)


def escape_text(value: object) -> str:
    return escape(str(value), quote=True)
