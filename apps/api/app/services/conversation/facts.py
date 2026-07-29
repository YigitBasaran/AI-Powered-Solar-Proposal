"""The closed set of facts an answer may be built from.

Two rules make this safe, and both are structural rather than advisory.

**Analysis is a source only while it is provably fresh.** A snapshot carries
the inputs it describes, so it can be compared against the project's current
inputs. Where they disagree, the affected sections are withheld - not softened,
withheld - and the sections a change cannot reach stay available. Answering
"what is my payback?" from figures computed for a consumption the customer has
since corrected would be worse than saying nothing.

**A finalised proposal outranks the live analysis.** Once a document has been
issued, that is the authoritative account of what the customer was told.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.config import Settings, get_settings
from app.services.conversation.actions import AnswerSource, AnswerState, Topic
from app.services.conversation.invalidation import (
    DEPENDS_ON_CONSUMPTION,
    DEPENDS_ON_SYSTEM_SIZE,
    detect_staleness,
    sections_of,
)

#: Which snapshot section answers a given topic. Topics absent from this map
#: are answered from configuration or curated help, never from the analysis.
SECTION_FOR_TOPIC: dict[Topic, str] = {
    Topic.ROOF: "roof",
    Topic.LAYOUT: "layout",
    Topic.YIELD: "energy",
    Topic.FX: "exchangeRate",
    Topic.FINANCE: "financial",
    Topic.SYSTEM_SIZE: "layout",
    Topic.CONSUMPTION: "financial",
}


#: Every section any project input can rewrite.
COMPUTED_SECTIONS: frozenset[str] = sections_of(DEPENDS_ON_CONSUMPTION) | sections_of(
    DEPENDS_ON_SYSTEM_SIZE
)


def _blast_radius(project: Any) -> frozenset[str]:
    """Sections a recomputation in flight might currently be rewriting.

    When the project records *which* inputs are being recomputed, only the
    sections those inputs can reach are withheld - so the roof and the rate
    stay answerable throughout. When it does not, every computed section is
    treated as suspect, because serving a number that may be moving is the
    failure this exists to prevent.
    """
    pending: frozenset[str] = getattr(project, "recalculating_inputs", frozenset()) or frozenset()
    if not pending:
        return COMPUTED_SECTIONS

    sections: set[str] = set()
    if "monthly_consumption_kwh" in pending:
        sections |= sections_of(DEPENDS_ON_CONSUMPTION)
    if "selected_system_size_kwp" in pending:
        sections |= sections_of(DEPENDS_ON_SYSTEM_SIZE)
    return frozenset(sections)


@dataclass(frozen=True)
class FactBundle:
    """What may be said about one topic, right now, and on whose authority."""

    topic: Topic
    state: AnswerState
    source: AnswerSource
    values: dict[str, Any] = field(default_factory=dict)
    #: Named so a reply can be specific: "the consumption you just changed".
    stale_inputs: frozenset[str] = frozenset()

    @property
    def has_values(self) -> bool:
        return bool(self.values)


def _roof_values(settings: Settings) -> dict[str, Any]:
    """The roof is fixed and derivable with no analysis at all.

    Worth exploiting: it means "how big is the roof?" has a real answer at the
    very first step, which is the clearest demonstration that this is a
    conversation rather than a wizard with a chat skin.
    """
    from app.services.roof import build_roof_model, roof_summary

    summary = roof_summary(build_roof_model(settings))
    return {
        "pitchDeg": summary["pitchDeg"],
        "facetCount": len(summary["facets"]),
        "totalProjectedAreaM2": summary["totalProjectedAreaM2"],
        "totalSlopedAreaM2": summary["totalSlopedAreaM2"],
        "groundMetresPerSourcePixel": summary["groundMetresPerSourcePixel"],
        "facets": [
            {
                "label": f["label"],
                "compassAzimuthDeg": f["compassAzimuthDeg"],
                "slopedAreaM2": f["slopedAreaM2"],
            }
            for f in summary["facets"]
        ],
        "edgeCounts": {
            kind: sum(1 for e in summary["edges"] if e["type"] == kind)
            for kind in ("eave", "hip", "ridge")
        },
    }


def _case_values(settings: Settings) -> dict[str, Any]:
    """Fixed case configuration - true before anything has been calculated."""
    location = settings.case_location
    return {
        "resolvedLatitude": round(location.resolved_latitude, 6),
        "resolvedLongitude": round(location.resolved_longitude, 6),
        "rawLatitude": location.raw_case_latitude,
        "pitchDeg": settings.roof_pitch_deg,
        "panelWidthM": settings.panel_width_m,
        "panelHeightM": settings.panel_height_m,
        "panelPowerWp": settings.panel_power_wp,
        "panelGapM": settings.panel_gap_m,
        "roofEdgeSetbackM": settings.roof_edge_setback_m,
        "systemSizesKwp": list(settings.allowed_system_sizes_kwp),
        "panelCounts": {
            f"{size:g}": settings.required_panel_count(size)
            for size in settings.allowed_system_sizes_kwp
        },
        "electricityPriceEurPerKwh": settings.case_electricity_price,
        "capexAmount": settings.case_capex_amount,
        "capexCurrency": settings.case_capex_currency,
        "pvgisSystemLossPercent": settings.pvgis_system_loss_percent,
        "pvgisTechnology": settings.pvgis_technology,
        "pvgisMountingPlace": settings.pvgis_mounting_place,
        "fxProvider": settings.fx_data_provider,
        "fxSourceApi": settings.fx_provider,
        "mapZoom": settings.google_maps_zoom,
        "mapScale": settings.google_maps_scale,
        "analysisYears": 20,
    }


def build_facts(
    *,
    project: Any,
    settings: Settings | None = None,
    topic: Topic,
) -> FactBundle:
    """Everything that may be cited about `topic`, and nothing that may not.

    `project` is a `workflow.ProjectState`; it is typed loosely here only to
    keep the import one-directional.
    """
    settings = settings or get_settings()
    section = SECTION_FOR_TOPIC.get(topic)

    # 1. A finalised proposal is the authoritative account.
    snapshot = getattr(project, "proposal_snapshot", None)
    source = AnswerSource.PROPOSAL_SNAPSHOT

    if snapshot is None:
        snapshot = getattr(project, "analysis", None)
        source = AnswerSource.ANALYSIS
        status = getattr(project, "analysis_status", "pending")

        if section is not None and snapshot:
            staleness = detect_staleness(
                snapshot=snapshot,
                monthly_consumption_kwh=getattr(project, "monthly_consumption_kwh", None),
                selected_system_size_kwp=getattr(project, "selected_system_size_kwp", None),
            )

            # Both statuses are scoped to the sections the change could reach.
            # Withholding the roof because a *financial* recomputation failed
            # would be theatre: no project input can move a roof measurement,
            # and the test suite asserts that field by field.
            at_risk = section in _blast_radius(project)

            if status == "recalculating" and at_risk:
                return FactBundle(
                    topic=topic,
                    state=AnswerState.RECALCULATING,
                    source=AnswerSource.NONE,
                    stale_inputs=staleness.stale_inputs,
                )
            if (status == "stale" and at_risk) or staleness.affects(section):
                return FactBundle(
                    topic=topic,
                    state=AnswerState.NOT_CALCULATED_YET,
                    source=AnswerSource.NONE,
                    stale_inputs=staleness.stale_inputs,
                )

        if section is not None and status not in {"complete", "recalculating", "stale"}:
            snapshot = None

    if section is not None and snapshot:
        return FactBundle(
            topic=topic,
            state=AnswerState.ANSWERABLE_NOW,
            source=source,
            values=dict(snapshot.get(section) or {}),
        )

    # 2. Topics the analysis never covers, and the roof - which is fixed.
    if topic is Topic.ROOF:
        return FactBundle(
            topic=topic,
            state=AnswerState.ANSWERABLE_NOW,
            source=AnswerSource.CASE_CONFIG,
            values=_roof_values(settings),
        )
    if topic in {Topic.LOCATION, Topic.PRIVACY, Topic.GENERAL, Topic.PROPOSAL}:
        return FactBundle(
            topic=topic,
            state=AnswerState.ANSWERABLE_NOW,
            source=AnswerSource.CASE_CONFIG,
            values=_case_values(settings),
        )

    # 3. A section that exists but has not been computed yet.
    return FactBundle(
        topic=topic,
        state=AnswerState.NOT_CALCULATED_YET,
        source=AnswerSource.CASE_CONFIG,
        values=_case_values(settings),
    )


def case_values(settings: Settings | None = None) -> dict[str, Any]:
    return _case_values(settings or get_settings())


__all__ = ["COMPUTED_SECTIONS", "SECTION_FOR_TOPIC", "FactBundle", "build_facts", "case_values"]
