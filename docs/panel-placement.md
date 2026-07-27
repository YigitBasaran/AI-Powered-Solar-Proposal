# Panel Placement

How panels are laid out, why it happens in surface coordinates, and how the allocator decides which roof faces are worth filling.

Implementation: [`apps/api/app/services/layout.py`](../apps/api/app/services/layout.py)

---

## 1. Placement happens on the roof, not on the map

A panel is **1 m × 2 m on the sloped plane**. In the plan view it appears foreshortened by `cos(pitch)` along the slope — at 25°, about 91 % of its true extent.

Placing in the plan view would treat each panel as 1 × 1.81 m, so roughly **11 % more panels would appear to fit along the slope than physically can**. Nothing would look wrong; the roof would simply be over-packed, and the production and payback figures built on it would be too good.

So every facet is transformed into its own surface frame first (`u` along the eave, `v` up the slope — see [`geometry.md`](geometry.md)), panels are placed there, and the result is transformed back for rendering.

Two tests pin this: a placed panel measures exactly 1 × 2 m in surface space, and its projected footprint has area exactly `2 × cos(25°)` m².

---

## 2. Per-facet pipeline

1. **Transform** the facet outline into surface coordinates.
2. **Shrink** by the configured edge setback (negative buffer). A setback that consumes the facet yields no panels rather than an error.
3. **Tile** for each orientation, sweeping grid offsets.
4. **Keep** the best tiling per orientation as a candidate.

### Why the offsets are swept

Anchoring the grid at the polygon's bounding-box corner frequently wastes an entire row against a sloping hip. The search sweeps origin offsets in **0.05 m** steps across one grid pitch in each axis — offsets beyond one pitch simply repeat.

### Containment is tested on the whole footprint

```python
if contains.covers(box(u, v, u + width, v + height)):
```

Not the centre. A centre-only test lets a panel hang over a hip while its midpoint is comfortably inside — the classic way an over-packed roof passes validation.

Shapely's `prepared` geometry makes the repeated containment tests cheap enough to sweep exhaustively.

### Both orientations, always

| Orientation | Along `u` | Along `v` |
|---|---|---|
| Portrait | 1 m | 2 m |
| Landscape | 2 m | 1 m |

On this roof landscape wins everywhere — the facets are shallow, so more 1 m-tall rows fit up the slope than 2 m-tall ones. That is a *result*, not an assumption; a test asserts landscape ≥ portrait per facet rather than hardcoding the choice.

### Fill order

Candidates are sorted by `(v, u)` — from the eave upward, then along the eave. Taking the first *n* therefore yields a compact, bottom-anchored block of continuous rows rather than a scattering.

---

## 3. Allocation across facets

Facets are combined by an **exact dynamic program** maximising expected annual production:

```
dp[k] = best (production, allocation) using exactly k panels so far
```

For each facet, each candidate orientation, and each count `j`:

```
production += j × 0.4 kWp × specific_yield(facet)
```

Ties break on fewer facets used (a more compact install), then deterministically, so the same input always produces the same layout. Two salespeople quoting the same house must not get different answers.

With four facets and at most 24 panels the DP is trivial. Greedy-by-yield would be optimal for this linear objective, and is used as the test oracle — but the DP stays correct if the objective ever becomes non-linear (per-string losses, inverter grouping), which greedy would not.

### The yield ranking port

The optimiser needs a specific yield per facet, which is genuinely PVGIS data. It does **not** depend on the PVGIS client:

```python
class FacetYieldRankingProvider(Protocol):
    async def specific_yield_kwh_per_kwp(self, facet: RoofFacet) -> float: ...
```

- `FixtureFacetYieldRankingProvider` — captured PVGIS probes. The optimiser's tests bind this permanently, which is what keeps their behaviour deterministic.
- `PvgisFacetYieldRankingProvider` — live 1 kWp probes, cached.

The optimiser was built and tested before the live client existed. Adding it changed no optimiser code and broke no optimiser test.

---

## 4. What the allocator actually decides here

Capacity and yield on the case roof:

| Facet | Sloped area | Capacity | Specific yield |
|---|---|---|---|
| North trapezoid | 30.12 m² | 9 | **1,678.7 kWh/kWp** |
| West triangle | 14.08 m² | 3 | 1,515.3 |
| East triangle | 14.07 m² | 3 | 1,367.2 |
| South trapezoid | 30.12 m² | 9 | 1,119.8 |
| **Total** | **88.40 m²** | **24** | |

| System | Panels | Allocation |
|---|---|---|
| 3.6 kWp | 9 | North only |
| **6.0 kWp** | **15** | **North 9 + West 3 + East 3** |
| 9.6 kWp | 24 | All four facets |

### The 6 kWp case is the proof

North and south are the **same size** and hold **9 panels each**. An allocator ranking facets by area — the obvious implementation — would put the remaining 6 panels on south.

The correct answer fills both **small triangles** and leaves the large south trapezoid **empty**, because at −34° latitude west (1,515) and east (1,367) out-produce south (1,120).

A test inverts the yield table and asserts the allocation inverts with it, proving the decision follows the data and not the geometry.

---

## 5. Honest capacity limits

If the requested count cannot fit:

- place the maximum feasible count,
- return a warning naming both figures,
- and drive **PVGIS and the financial model from the feasible capacity**.

Nothing downstream ever sees the requested figure as though it were installed.

The case roof happens to fit all three sizes exactly, so the warning path is exercised by applying a realistic 1 m safety setback, which makes 24 panels impossible. Tests assert the shortfall, the warning text, and that the best facet is still preferred when short.

> An earlier estimate in this build predicted 9.6 kWp would be infeasible. It was wrong — the roof holds exactly 24. The forecast was replaced with the measurement rather than left in the docs.

---

## 6. Post-conditions, checked in production

`assert_layout_valid()` runs on every request, not only in tests:

- every panel fully inside its facet,
- no two panels overlapping,
- installed power exactly `count × 400 Wp`,
- a short layout always carries a warning.

An over-packed roof renders beautifully and produces a confident, wrong proposal. That failure mode is worth paying a few milliseconds to prevent.

---

## 7. Performance

The offset sweep is the expensive part, and the roof is static, so candidates are cached on everything the tiling depends on: roof id, facet id, ground resolution, pitch, panel dimensions, gap and setback. A settings change invalidates the cache.

This took the layout test suite from 39 s to 1.6 s and removes ~1.1 s from every analysis request.

---

## Related

- [`docs/geometry.md`](geometry.md) — the surface frame
- [`docs/assumptions.md`](assumptions.md) — panel and setback assumptions
