"""What a changed input invalidates, and how to tell a stale snapshot.

The analysis snapshot is a deterministic function of exactly two project
inputs - monthly consumption and system size - plus fixed settings and
fixtures. So the interesting question is not *whether* a change invalidates
something; it is **which fields**, and that is answered by experiment rather
than by reading the code. `tests/unit/test_corrections.py` runs the analysis
twice with one input varied and asserts the set of fields that actually moved
equals the set declared here.

That matters because "only the section called `financial` depends on
consumption" is plausible, is currently true, and is not something to take on
trust. If a future field elsewhere starts depending on consumption, the
differential test fails and this map has to be updated - which is the point.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: Wall-clock metadata that legitimately differs between two runs of the same
#: analysis.
#:
#: This list is the easiest place in the codebase to hide a real difference, so
#: it is closed, it is short, and it is tested from both directions: one test
#: asserts it contains nothing from a domain or provenance path, another
#: asserts two identical runs differ *only* within it.
#:
#: FX provenance - the rate, its date, the retrieval source, the provider - is
#: **data**, not metadata. Only the moment of retrieval is volatile.
VOLATILE_SNAPSHOT_PATHS: frozenset[str] = frozenset({"exchangeRate.retrievedAt"})

#: Sections that no project input can affect.
NEVER_AFFECTED: frozenset[str] = frozenset({"roof"})

_INDEX = re.compile(r"\[\d+\]")


def _normalise_path(path: str) -> str:
    """`financial.cashFlow[3].year` -> `financial.cashFlow[].year`.

    List position is not a field. Collapsing the index keeps the comparison
    stable when a change alters how many facets or panels there are.
    """
    return _INDEX.sub("[]", path)


def flatten_snapshot(value: Any, prefix: str = "") -> dict[str, list[Any]]:
    """Every leaf of a snapshot, keyed by its index-normalised path.

    Values collect into a list, so two snapshots with different facet counts
    still compare on the same key rather than looking like disjoint documents.
    """
    flat: dict[str, list[Any]] = {}

    def _walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                _walk(child, f"{path}.{key}" if path else str(key))
        elif isinstance(node, list):
            for index, child in enumerate(node):
                _walk(child, f"{path}[{index}]")
        else:
            flat.setdefault(_normalise_path(path), []).append(node)

    _walk(value, prefix)
    return flat


def differing_paths(
    a: dict[str, Any],
    b: dict[str, Any],
    *,
    ignore: frozenset[str] = VOLATILE_SNAPSHOT_PATHS,
) -> set[str]:
    """Index-normalised leaf paths whose values differ between two snapshots."""
    flat_a = flatten_snapshot(a)
    flat_b = flatten_snapshot(b)
    return {
        path
        for path in flat_a.keys() | flat_b.keys()
        if path not in ignore and flat_a.get(path) != flat_b.get(path)
    }


def sections_of(paths: set[str] | frozenset[str]) -> frozenset[str]:
    """The top-level snapshot sections a set of paths touches."""
    return frozenset(_normalise_path(path).split(".", 1)[0].split("[", 1)[0] for path in paths)


# ---------------------------------------------------------------------------
# The declared map, verified by tests/unit/test_corrections.py
# ---------------------------------------------------------------------------

#: Fields that can move when monthly consumption changes and nothing else does.
#:
#: "Can", not "does". A single pair under-approximates: at 6 kWp the system
#: produces 9,502 kWh a year, so 1,150 and 900 kWh a month are *both* above it
#: and savings - capped at consumption - are identical for the two. Comparing
#: only those would have declared savings independent of consumption, which is
#: false the moment the household uses less than the roof makes. The map is the
#: union over pairs that straddle that cap, and the test asserts both that no
#: field outside it ever moves and that every field in it moves for some pair.
DEPENDS_ON_CONSUMPTION: frozenset[str] = frozenset(
    {
        "financial.annualConsumptionKwh",
        "financial.coveredEnergyKwh",
        "financial.coveragePercent",
        "financial.annualSavingsEur",
        "financial.simplePaybackYears",
        "financial.twentyYearNetBenefitEur",
        "financial.cashFlow[].annualSavingsEur",
        "financial.cashFlow[].cumulativeCashFlowEur",
    }
)

#: Fields that can move when the system size changes and nothing else does.
#:
#: Note what is *absent*: every `roof.*` path, and every `exchangeRate.*` path.
#: The roof is fixed and the rate is an observation already made.
DEPENDS_ON_SYSTEM_SIZE: frozenset[str] = frozenset(
    {
        "layout.requestedSystemSizeKwp",
        "layout.requestedPanelCount",
        "layout.placedPanelCount",
        "layout.feasibleSystemSizeKwp",
        "layout.facets[].facetId",
        "layout.facets[].orientation",
        "layout.facets[].panelCount",
        "layout.facets[].panels[].id",
        "layout.facets[].panels[].sourcePixelPolygon[].x",
        "layout.facets[].panels[].sourcePixelPolygon[].y",
        "energy.installedPowerKwp",
        "energy.totalAnnualProductionKwh",
        "energy.totalMonthlyProductionKwh[]",
        "energy.facets[].facetId",
        "energy.facets[].panelCount",
        "energy.facets[].installedPowerKwp",
        "energy.facets[].pitchDeg",
        "energy.facets[].compassAzimuthDeg",
        "energy.facets[].pvgisAspectDeg",
        "energy.facets[].annualProductionKwh",
        "energy.facets[].specificYieldKwhPerKwp",
        "energy.facets[].monthlyProductionKwh[]",
        "energy.facets[].dataSource",
        "financial.annualProductionKwh",
        "financial.coveredEnergyKwh",
        "financial.coveragePercent",
        "financial.annualSavingsEur",
        "financial.simplePaybackYears",
        "financial.twentyYearNetBenefitEur",
        "financial.cashFlow[].annualSavingsEur",
        "financial.cashFlow[].cumulativeCashFlowEur",
    }
)


@dataclass(frozen=True)
class Staleness:
    """Whether a snapshot still describes the project it belongs to."""

    stale_inputs: frozenset[str]
    stale_sections: frozenset[str]

    @property
    def is_stale(self) -> bool:
        return bool(self.stale_inputs)

    def affects(self, section: str) -> bool:
        return section in self.stale_sections


FRESH = Staleness(frozenset(), frozenset())

_TOLERANCE = 1e-6


def snapshot_signature(snapshot: dict[str, Any]) -> tuple[float | None, float | None]:
    """The inputs a snapshot claims to describe: (monthly consumption, size).

    Both are already echoed inside the snapshot, so staleness needs no extra
    column and works on rows written before any of this existed.
    """
    financial = snapshot.get("financial") or {}
    layout = snapshot.get("layout") or {}
    annual = financial.get("annualConsumptionKwh")
    monthly = None if annual is None else float(annual) / 12.0
    size = layout.get("requestedSystemSizeKwp")
    return (monthly, None if size is None else float(size))


def detect_staleness(
    *,
    snapshot: dict[str, Any] | None,
    monthly_consumption_kwh: float | None,
    selected_system_size_kwp: float | None,
) -> Staleness:
    """Compare a snapshot against the inputs it is supposed to describe."""
    if not snapshot:
        return FRESH

    snapshot_monthly, snapshot_size = snapshot_signature(snapshot)
    stale: set[str] = set()

    def _moved(recorded: float | None, current: float | None) -> bool:
        """Only a value present on *both* sides can be shown to have moved."""
        return recorded is not None and current is not None and abs(recorded - current) > _TOLERANCE

    if _moved(snapshot_monthly, monthly_consumption_kwh):
        stale.add("monthly_consumption_kwh")
    if _moved(snapshot_size, selected_system_size_kwp):
        stale.add("selected_system_size_kwp")

    sections: set[str] = set()
    if "monthly_consumption_kwh" in stale:
        sections |= sections_of(DEPENDS_ON_CONSUMPTION)
    if "selected_system_size_kwp" in stale:
        sections |= sections_of(DEPENDS_ON_SYSTEM_SIZE)

    return Staleness(frozenset(stale), frozenset(sections))


__all__ = [
    "DEPENDS_ON_CONSUMPTION",
    "DEPENDS_ON_SYSTEM_SIZE",
    "FRESH",
    "NEVER_AFFECTED",
    "VOLATILE_SNAPSHOT_PATHS",
    "Staleness",
    "detect_staleness",
    "differing_paths",
    "flatten_snapshot",
    "sections_of",
    "snapshot_signature",
]
