"""Facet yield ranking.

The panel-placement optimiser needs a specific yield per facet in order to
decide which roof faces are worth filling first. That is genuinely PVGIS data,
but the optimiser must not depend on the live PVGIS client: it would make the
optimiser untestable without a network, and it would force the integration
layer to exist before the algorithm that consumes it.

So ranking is expressed as a port. The fixture implementation reads captured
PVGIS responses and is what the optimiser's tests bind, permanently - which is
what keeps optimiser behaviour deterministic. The live implementation lands in
the PVGIS phase and satisfies the same protocol.
"""

from __future__ import annotations

import json
from itertools import pairwise
from pathlib import Path
from typing import Protocol, runtime_checkable

from app.core.config import get_settings
from app.core.errors import PvgisUnavailableError
from app.domain.models import RoofFacet


@runtime_checkable
class FacetYieldRankingProvider(Protocol):
    """Supplies 1 kWp specific yield for a facet, used only for ranking."""

    async def specific_yield_kwh_per_kwp(self, facet: RoofFacet) -> float: ...


def _wrap_aspect(aspect: float) -> float:
    while aspect <= -180.0:
        aspect += 360.0
    while aspect > 180.0:
        aspect -= 360.0
    return aspect


class FixtureFacetYieldRankingProvider:
    """Ranking from committed PVGIS captures.

    Exact aspects present in the table are returned verbatim; anything else is
    linearly interpolated between the two nearest samples, wrapping across the
    +/-180 seam so a north-facing facet never falls off the end of the table.
    """

    def __init__(self, fixture_path: Path | None = None) -> None:
        self._path = fixture_path or (
            get_settings().fixtures_dir / "pvgis" / "specific-yield-by-aspect.json"
        )
        self._table: dict[float, float] | None = None

    def _load(self) -> dict[float, float]:
        if self._table is None:
            if not self._path.is_file():
                raise PvgisUnavailableError(
                    f"PVGIS yield fixture not found at {self._path}. "
                    "Run scripts/fetch_pvgis_fixtures.py to capture it."
                )
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            self._table = {
                float(k): float(v["specific_yield_kwh_per_kwp"])
                for k, v in raw["by_aspect"].items()
            }
            if not self._table:
                raise PvgisUnavailableError("PVGIS yield fixture contains no samples")
        return self._table

    def lookup(self, aspect_deg: float) -> float:
        table = self._load()
        aspect = _wrap_aspect(aspect_deg)

        if aspect in table:
            return table[aspect]

        points = sorted(table.items())
        # Wrap the seam so interpolation is continuous through +/-180.
        extended = [
            (points[-1][0] - 360.0, points[-1][1]),
            *points,
            (points[0][0] + 360.0, points[0][1]),
        ]
        for (a0, y0), (a1, y1) in pairwise(extended):
            if a0 <= aspect <= a1:
                if a1 == a0:
                    return y0
                t = (aspect - a0) / (a1 - a0)
                return y0 + t * (y1 - y0)
        return points[0][1]

    async def specific_yield_kwh_per_kwp(self, facet: RoofFacet) -> float:
        return self.lookup(facet.pvgis_aspect_deg)


class StaticFacetYieldRankingProvider:
    """Fixed per-facet yields. Used by tests that need a specific ordering."""

    def __init__(self, yields: dict[str, float]) -> None:
        self._yields = yields

    async def specific_yield_kwh_per_kwp(self, facet: RoofFacet) -> float:
        if facet.id not in self._yields:
            raise PvgisUnavailableError(f"no static yield configured for {facet.id!r}")
        return self._yields[facet.id]


async def rank_facets(
    facets: list[RoofFacet], provider: FacetYieldRankingProvider
) -> dict[str, float]:
    """Specific yield per facet id."""
    return {f.id: await provider.specific_yield_kwh_per_kwp(f) for f in facets}
