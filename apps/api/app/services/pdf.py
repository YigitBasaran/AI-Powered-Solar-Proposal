"""PDF rendering: stored snapshot -> Jinja2 -> Chromium -> A4.

The renderer reads the proposal snapshot and nothing else. It performs no
calculation, so a figure in the PDF cannot disagree with the same figure on the
share page: both read the same stored values.

Charts are inline SVG produced server-side, so there is nothing for Chromium to
wait on beyond fonts and the layout image - no chart library, no animation, no
race with the print call.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.core.config import Settings, get_settings
from app.services.charts import (
    cash_flow_chart,
    facet_orientation_diagram,
    monthly_production_chart,
    roof_plan_svg,
)

logger = logging.getLogger("solarvis.pdf")

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates"

FX_SOURCE_LABELS = {
    "live": "live ECB reference rate",
    "cache": "cached ECB reference rate",
    "live_fallback_cache": "cached ECB reference rate (live retrieval unavailable)",
    "fixture": "development fixture rate — not live market data",
    "live_fallback_fixture": "development fixture rate — not live market data",
}


def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _data_mode_notes(snapshot: dict[str, Any], settings: Settings) -> list[str]:
    """Surface any non-live data source in the assumptions, never silently."""
    notes: list[str] = []

    fx_source = snapshot.get("exchangeRate", {}).get("retrievalSource", "")
    if fx_source in ("fixture", "live_fallback_fixture"):
        notes.append(
            "The USD/EUR rate in this proposal is development fixture data, not a live market rate."
        )
    elif fx_source in ("cache", "live_fallback_cache"):
        notes.append("The USD/EUR rate was taken from cache rather than retrieved live.")

    energy_source = snapshot.get("energy", {}).get("dataSource", "")
    if energy_source == "replay":
        notes.append(
            "Production figures come from a replayed PVGIS capture, not a live retrieval. "
            "This document is not proposal-grade."
        )
    elif energy_source in ("fixture", "live_fallback_fixture"):
        # Legacy snapshots only. Nothing produces these values any more; a
        # proposal issued before PVGIS became mandatory still has to render,
        # and still has to say what it was built on.
        notes.append("Production figures come from captured PVGIS fixtures, not a live call.")

    if settings.maps_mode.value == "fixture":
        notes.append(
            "Satellite imagery is a development fixture rendered on the verified "
            "map grid, not a live Google Static Maps request."
        )
    return notes


def build_context(
    snapshot: dict[str, Any],
    *,
    share_token: str,
    created_at: str,
    settings: Settings | None = None,
    layout_image_bytes: bytes | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()

    roof = snapshot["roof"]
    layout = snapshot["layout"]
    energy = snapshot["energy"]
    financial = snapshot["financial"]
    fx = snapshot["exchangeRate"]
    meta = snapshot.get("meta", {})

    panels_by_facet = {f["facetId"]: f["panelCount"] for f in layout.get("facets", [])}
    facet_labels = {f["id"]: f["label"] for f in roof["facets"]}

    cash_flow = financial["cashFlow"]
    half = (len(cash_flow) + 1) // 2
    pairs = [
        (cash_flow[i], cash_flow[i + half] if i + half < len(cash_flow) else None)
        for i in range(half)
    ]

    layout_image = None
    if layout_image_bytes:
        encoded = base64.b64encode(layout_image_bytes).decode("ascii")
        layout_image = f"data:image/png;base64,{encoded}"

    map_attribution = "Imagery (c) Esri, Maxar, Earthstar Geographics"
    if settings.maps_mode.value == "live":
        map_attribution = "Map data (c) Google"

    return {
        "roof": roof,
        "layout": layout,
        "energy": energy,
        "financial": financial,
        "exchangeRate": fx,
        "meta": meta,
        "share_token": share_token,
        "created_at": created_at,
        "capacity_warning": snapshot.get("capacityWarning") or layout.get("capacityWarning"),
        "ai_summary": snapshot.get("aiSummary"),
        "panels_by_facet": panels_by_facet,
        "facet_labels": facet_labels,
        "cash_flow_pairs": pairs,
        "monthly_svg": monthly_production_chart(energy["totalMonthlyProductionKwh"]),
        "cashflow_svg": cash_flow_chart(
            cash_flow, payback_years=financial.get("simplePaybackYears")
        ),
        "compass_svg": facet_orientation_diagram(energy["facets"]),
        "layout_image": layout_image,
        # Drawn from stored geometry when the frontend has not uploaded an
        # exported canvas, so the PDF always shows the reconstruction.
        "roof_plan_svg": None if layout_image else roof_plan_svg(roof, layout),
        "map_attribution": map_attribution,
        "fx_source_label": FX_SOURCE_LABELS.get(
            fx.get("retrievalSource", ""), fx.get("retrievalSource", "unknown")
        ),
        "panel_spec": (
            f"{settings.panel_width_m:g} m x {settings.panel_height_m:g} m, "
            f"{settings.panel_power_wp} Wp"
        ),
        "pvgis_loss": f"{settings.pvgis_system_loss_percent:g}",
        "pvgis_technology": settings.pvgis_technology,
        "pvgis_mounting": settings.pvgis_mounting_place,
        "setback": f"{settings.roof_edge_setback_m:g}",
        "gap": f"{settings.panel_gap_m:g}",
        "data_mode_notes": _data_mode_notes(snapshot, settings),
    }


def render_html(context: dict[str, Any]) -> str:
    return _environment().get_template("proposal.html.j2").render(**context)


async def render_pdf(html: str) -> bytes:
    """HTML -> A4 PDF via Chromium.

    Waits for fonts explicitly. Everything else in the document is inline (SVG
    charts, a data-URI image), so there are no network resources to settle.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise RuntimeError(
            "Playwright is not installed. Run: pip install -e '.[pdf]' && "
            "playwright install chromium"
        ) from exc

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(args=["--no-sandbox"])
        try:
            page = await browser.new_page()
            await page.set_content(html, wait_until="load")
            await page.evaluate("() => document.fonts.ready")
            pdf_bytes = await page.pdf(
                format="A4",
                print_background=True,
                margin={"top": "16mm", "bottom": "18mm", "left": "14mm", "right": "14mm"},
                display_header_footer=True,
                header_template="<div></div>",
                footer_template=(
                    '<div style="width:100%;font-size:8px;color:#898781;'
                    "font-family:system-ui,sans-serif;padding:0 14mm;"
                    'display:flex;justify-content:space-between">'
                    "<span>solarVis AI &mdash; solar feasibility proposal</span>"
                    '<span>Page <span class="pageNumber"></span> of '
                    '<span class="totalPages"></span></span></div>'
                ),
            )
        finally:
            await browser.close()

    if len(pdf_bytes) < 5_000:
        raise RuntimeError("Generated PDF is suspiciously small")

    logger.info("rendered proposal PDF (%d bytes)", len(pdf_bytes))
    return pdf_bytes
