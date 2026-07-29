"""Facet yields for tests, without going near PVGIS.

These two providers used to live in `app/services/yield_ranking.py`. The
fixture one interpolated specific yield between captured aspects for *any*
aspect at all - a synthetic estimator sitting in the runtime, selected whenever
PVGIS was not reachable. It is exactly the kind of thing "no synthetic
fallback for production estimates" is about, and it was easy to miss because it
was not in `pvgis.py`.

So it moved here. The optimiser now takes a plain mapping of facet id to
specific yield, which is all it ever needed; these helpers build one.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from itertools import pairwise
from pathlib import Path

from app.core.config import get_settings
from app.core.errors import PvgisUnavailableError
from app.domain.models import RoofFacet


def _wrap_aspect(aspect: float) -> float:
    """Into `(-180, 180]`, the same interval the geometry converter produces."""
    while aspect <= -180.0:
        aspect += 360.0
    while aspect > 180.0:
        aspect -= 360.0
    return aspect


class AspectYieldTable:
    """The captured 1 kWp sweep, interpolated by aspect.

    Test-only. The values come from `fixtures/pvgis/specific-yield-by-aspect.json`,
    captured at the case coordinate, 25 degrees, 14 % loss.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (
            get_settings().fixtures_dir / "pvgis" / "specific-yield-by-aspect.json"
        )
        self._table: dict[float, float] | None = None

    def _load(self) -> dict[float, float]:
        if self._table is not None:
            return self._table
        if not self._path.is_file():
            raise PvgisUnavailableError(
                f"PVGIS yield fixture not found at {self._path}. "
                f"Run scripts/fetch_pvgis_fixtures.py to capture it."
            )
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        self._table = {
            float(key): float(value["specific_yield_kwh_per_kwp"])
            for key, value in raw["by_aspect"].items()
        }
        if not self._table:
            raise PvgisUnavailableError(f"PVGIS yield fixture at {self._path} is empty")
        return self._table

    def lookup(self, aspect_deg: float) -> float:
        table = self._load()
        aspect = _wrap_aspect(aspect_deg)
        if aspect in table:
            return table[aspect]

        points = sorted(table.items())
        # Extend across the +/-180 seam so interpolation is continuous there.
        extended = [
            (points[-1][0] - 360.0, points[-1][1]),
            *points,
            (points[0][0] + 360.0, points[0][1]),
        ]
        for (a0, y0), (a1, y1) in pairwise(extended):
            if a0 <= aspect <= a1:
                t = (aspect - a0) / (a1 - a0)
                return y0 + t * (y1 - y0)
        raise PvgisUnavailableError(f"no yield sample near aspect {aspect_deg}")


def fixture_yields(facets: Sequence[RoofFacet], path: Path | None = None) -> dict[str, float]:
    """`{facet_id: specific yield}` from the captured sweep."""
    table = AspectYieldTable(path)
    return {facet.id: table.lookup(facet.pvgis_aspect_deg) for facet in facets}


__all__ = ["AspectYieldTable", "fixture_yields"]
