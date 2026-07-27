# Case Questions

---

## Question 1 — Three features to add next

Chosen because each removes a specific reason this tool would currently lose a deal, in the order the losses actually happen: the design is wrong, then the savings number is wrong, then the deal stalls after the proposal is sent.

### 1. Roof, obstacle and shading intelligence

**Why first.** Today the roof is one calibrated property with four clean facets, no chimneys and no shade. That is exactly the case a real survey overturns. A layout that silently places a panel over a vent, or on a face shaded until 11 a.m., produces a confident number that the site visit then contradicts — which is worse than no number.

- **Automatic facet detection** from satellite and aerial imagery, replacing the fixed calibration: outline, ridge/hip/valley classification, pitch from stereo or LiDAR where available. Return a **per-facet confidence**, and route anything low-confidence to a human rather than quietly guessing.
- **Obstruction detection** — chimneys, vents, skylights, HVAC units, satellite dishes — subtracted from the usable polygon before placement. The optimiser already works against a Shapely polygon, so obstructions are holes in that polygon; the placement code needs no change.
- **Safety and fire setbacks** as a per-jurisdiction policy layer rather than the single global `ROOF_EDGE_SETBACK_M` today. Ridge access paths and eave clearances differ by market and are usually non-negotiable.
- **Shading**: near-object (trees, neighbouring buildings, the roof's own dormers) via a height model, plus **horizon shading** from terrain. PVGIS already accepts a horizon profile, so this feeds the existing call. Report production loss per facet, not just a global de-rate — a facet that loses 40 % of its morning is a different decision from one that loses 5 % evenly.
- **A correction UI** where a surveyor can drag a vertex, add an obstruction or override a pitch, with every edit versioned and attributed. The `/dev/roof-calibration` tool is the seed of this.

**Hard part:** confidence calibration. A model that is wrong 5 % of the time but *knows which 5 %* is far more useful than one that is wrong 2 % of the time and always certain.

### 2. Hourly simulation, self-consumption and batteries

**Why second.** The current model caps savings at annual consumption and assumes no export compensation. That is a reasonable simplification, and it is also the single biggest source of error in the savings figure — because it implicitly assumes generation and consumption happen at the same moment, which they do not.

- **8,760-hour simulation** of generation against a load profile, so self-consumption is *computed* rather than assumed. Real self-consumption without a battery is typically 30–50 %, not the 100 % the annual cap implies.
- **Load profiles** from a smart-meter upload where available, or a synthesised profile from household type and appliance mix, with the source clearly labelled — the same discipline already applied to FX and PVGIS.
- **Time-of-use tariffs**, export tariffs and standing charges. In many markets the tariff structure moves the payback more than the panel count does.
- **Battery sizing and dispatch**: simulate charge/discharge against the tariff, size by marginal return per kWh rather than by rule of thumb, and include round-trip efficiency and degradation. Show the customer the battery's *own* payback separately, because it is usually worse than the panels' and they deserve to see that.
- **Scenario comparison** — panels only, panels + battery, larger array with export — side by side.

**Hard part:** the honest answer often gets worse. Self-consumption modelling typically *reduces* the headline saving. That is correct, and the product has to be built to survive telling the truth.

### 3. The commercial proposal workflow

**Why third.** The proposal is currently a terminal state: a link and a PDF. Everything that determines whether it becomes a sale happens after that, and is invisible.

- **Multiple priced scenarios** in one proposal, with a real bill of materials — panels, inverter, mounting, labour — rather than a single fixed CAPEX.
- **Financing**: cash, loan, lease and PPA, with monthly-cost framing. Most residential customers decide on monthly cost, not on payback years.
- **E-signature and deposit**, so acceptance is a step in the flow rather than an email thread.
- **CRM integration and proposal analytics.** View tracking (already implemented as the bonus) is the first inch of this: which sections were read, how long, whether it was forwarded. A proposal opened four times and never signed is a specific, actionable signal.
- **Versioning with immutability.** The snapshot design already guarantees a sent proposal never changes. Extend it to a version chain, so "the price went up" is always attributable to a specific revision.
- **Customer-side interaction** — questions against a section, a callback request, a comparison view.

**Hard part:** keeping immutability while allowing revision. The temptation is to edit in place; the discipline is to supersede.

---

## Question 2 — Technical bottlenecks

Grouped by where confidence actually breaks down. The first group is the one that matters most, because it fails silently.

### Geometry and measurement — plausible wrong answers

The defining risk of this product: **a wrong measurement still renders as a beautiful roof.** Nothing looks broken.

- **Scale provenance.** Metres-per-pixel must derive from a verified projection configuration, never from a rendered image's dimensions. This build has a concrete example: the brief's reference images sit at ~3.4× the source-map grid; using their pixels would have inflated lengths 3.4× and areas 11.6× while looking entirely normal.
- **Coordinate-space leakage.** During this build a segmentation result in window coordinates was passed through as source-map pixels, offsetting the calibration by 410 px. The *dimensions, aspect ratio and facet azimuths were all correct* — only rendering the overlay on the imagery exposed it. Unit tests would not have caught it; a visual check did.
- **Misapplied trigonometry.** `projected / cos(pitch)` is correct only along the line of maximum slope. Applying it to hips overstates them by ~4.8 % on this roof, and that error compounds into areas and panel counts.
- **Imagery age, resolution and off-nadir angle.** A roof measured from a 3-year-old, slightly oblique image is not the roof that exists. Parallax on a tall building shifts the roof relative to its footprint.
- **Complex roofs.** Dormers, valleys, multi-pitch and curved surfaces break the planar-facet assumption this build documents explicitly (A-GEO-1). The assumption must be *checked* per facet, not assumed globally.

**Mitigation:** one authoritative coordinate space; scale from configuration only; assumptions named, documented and unit-tested; and a visual regression check on the overlay, because the failure mode is visual.

### Panel packing

- Rectangle packing into non-convex polygons with holes is NP-hard. The current bounded offset sweep plus exact DP works at four facets and ≤24 panels; it does not scale to commercial roofs with dozens of facets and hundreds of panels.
- Real constraints multiply fast: string lengths, inverter MPPT grouping, roof structural capacity, walkways, rail runs.
- Determinism matters commercially — the same input must produce the same layout, or two salespeople quote differently on the same house.

**Mitigation:** keep the exact solver for small roofs, add a time-boxed heuristic above a size threshold, and always report which was used.

### External API dependency

- PVGIS and the FX provider are third-party services with rate limits and downtime, and PVGIS **revises its radiation datasets** — so the same request can return a different number months later.
- Fallback data is the real hazard, not outage. A cached or fixture value that renders identically to a live one will be trusted identically.

**Mitigation:** live → cache → labelled fixture, never a silent default; the retrieval source travels with every value and is surfaced in UI, snapshot and PDF; exact assertions live in fixture tests while live tests assert only invariants and ranges.

### Reproducibility and money

- A proposal must reproduce the numbers it was sent with, permanently. Any live lookup at render time breaks this.
- **Rounded series do not sum to rounded totals.** This build hit it: carrying full precision through a cash flow and rounding each year for display made consecutive rows differ by €2,530.57 and €2,530.58 — every figure individually correct, the printed table not reconcilable. Money is now canonicalised to cents once. Energy is deliberately *not*, because a 0.01 kWh residue is far below PVGIS's own uncertainty and forcing agreement would manufacture precision.
- Currency direction errors are easy to make and expensive: mixing USD CAPEX with EUR savings misstates payback by ~12 % at current rates.

**Mitigation:** immutable snapshots; Decimal end to end; parity unreachable by construction rather than by convention.

### Scale and performance

- 8,760-hour simulation across scenarios and batteries is orders of magnitude heavier than the current annual model.
- PDF rendering via a headless browser is memory-hungry and does not belong in a request thread at volume.
- Cache invalidation across imagery, calibration, production and pricing is genuinely hard: a re-derived roof must invalidate everything downstream of it.

**Mitigation:** move heavy simulation and PDF generation to a worker; key caches on every input that changes the answer (as the PVGIS cache already does).

### Security and privacy

- Public share links are unauthenticated by design. They need real entropy (192 bits here), no enumeration, and eventually expiry and revocation.
- Proposals contain a home address, consumption and financial position. View tracking is helpful commercially and is personal data: hash IPs, retain briefly, disclose.
- LLM output is untrusted input. It is schema-constrained, validated and re-checked against the domain whitelist here; prompt injection must never be able to move the workflow or alter a number.

### Observability

- The hardest production question is "why did this proposal say that?" — six months later, when imagery, calibration and rates have all moved.
- **Mitigation:** structured logs at every boundary, the data source recorded on every derived value, and immutable snapshots so any historical proposal can be reconstructed exactly.
