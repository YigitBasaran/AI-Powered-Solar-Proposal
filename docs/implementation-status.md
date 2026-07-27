# Implementation Status

Live build log and requirement-traceability matrix. Updated at the end of every phase.

**Nothing is marked ✅ until it actually runs and its tests pass.** Legend: ✅ done · 🔨 in progress · ⬜ not started

_Last updated: deterministic engineering core complete and verified end-to-end._

---

## Phase progress

| Phase | Scope | Status |
|---|---|---|
| 0 | Source audit, coordinate verification, fixture provenance, licensing | ✅ |
| 1 | Foundation — API, config, DB, tooling (web + Docker outstanding) | 🔨 |
| 2 | Deterministic workflow core — state machine, steps, rules parser | ⬜ |
| 3 | Geometry engine ✅ + calibration data ✅ + calibration UI ⬜ | 🔨 |
| 4 | Panel placement optimiser | ✅ |
| 5 | PVGIS + FX integrations | ✅ |
| 6 | Financial service | ✅ |
| 7 | Product shell and visualisation | ⬜ |
| 8 | Proposal, PDF, share route | ⬜ |
| 9 | Ollama conversational layer | ⬜ |
| 10 | Tracking bonus, hardening, packaging | ⬜ |

---

## Phase 0 — completed

**Delivered**

- Repository initialised; directory skeleton created.
- Case coordinate verified and resolved. The brief's latitude is missing a minus sign; `+34.0466` is open sea. Resolved to `−34.04658242871865, 18.46491476666948` (Cape Town, ZA) with three independent lines of evidence. Full write-up in [`location-verification.md`](location-verification.md).
- Authoritative map configuration locked: zoom 20, size 640×640, scale 2 → **1280 × 1280** source raster at **0.0618500 ground m/source-px**, spanning 79.168 m.
- Satellite fixture rendered on the **exact** Google z20/scale2 Web Mercator bbox, making `FixtureImageTransform` the identity with `verified = true`.
- Case reference images retained separately as topology references, explicitly barred from use as a scale source.
- Third-party asset provenance, attribution and usage scope documented in [`../LICENSE-NOTICE.md`](../LICENSE-NOTICE.md).

**Findings that shape later phases**

1. **Southern hemisphere inverts facet ranking.** Measured live: north-facing 10,122 kWh/yr vs south-facing 6,646 kWh/yr for 6 kWp. North wins by ≈52 %. No optimal aspect is hardcoded; ranking comes from per-facet 1 kWp PVGIS probes. Tests must assert southern-hemisphere expectations.
2. **The brief's reference images are ≈3.4× magnified** relative to the source-map grid. Using their pixels as source-map pixels would inflate lengths ≈3.4× and areas ≈11.6× while looking plausible. Scale comes only from the verified Web Mercator configuration.
3. **9.6 kWp is expected to be infeasible.** 24 panels need 48 m² against ≈76.8 m² of sloped roof (63 % utilisation) split across two trapezoids and two triangles. The honest capacity-warning path is expected to trigger and must drive PVGIS and financials from *feasible* capacity.
4. **Roof as measured:** ≈10.4 m × 6.7 m footprint, ≈9.7° rotation, ridge ≈3.7 m, ≈76.8 m² sloped at 25°.

**Verification performed**

| Check | Result |
|---|---|
| Nominatim reverse geocode, `+34.0466` | `Unable to geocode` — open sea |
| Nominatim reverse geocode, `−34.0466` | Galway Road, Cape Town, Western Cape 7945, ZA |
| PVGIS 5.3 `PVcalc` at resolved coord | HTTP 200, `PVGIS-SARAH3`, elevation 17.0 m |
| Frankfurter v2 ECB endpoint | HTTP 200, `{"date":"2026-07-24","base":"USD","quote":"EUR","rate":0.87897}` |
| Ollama library `qwen3.5` tags | `0.8b` and `2b` both exist |
| Imagery vs brief reference photos | Same estate, same house type; rotation 9.7° vs 9.8° |
| Fixture raster dimensions | 1280 × 1280 asserted |

**Open items carried forward**

- Precise roof vertex calibration is Phase 3 (values above are as-measured approximations, not committed calibration).
- CI workflow will be committed but cannot execute — no git remote exists. To be stated plainly in the README rather than implied green.

---

## Phase 1 — backend foundation (web app and Docker still outstanding)

**Delivered**

- `apps/api` package with Python 3.12 venv; FastAPI 0.140, Pydantic 2.13, SQLAlchemy 2.0.51, Shapely 2.1.2, httpx, Alembic, aiosqlite.
- Typed settings (`app/core/config.py`). The raw and resolved case coordinates are separate fields, and there is deliberately **no** USD/EUR rate setting, so a parity assumption cannot be configured into existence.
- `SatelliteImageConfig` derives ground resolution from Web Mercator and the verified zoom/scale alone — **0.0618500 m per source pixel**, never from image dimensions.
- Domain models for the whole pipeline (roof, panels, yield, FX, finance, proposal, chat intents).
- **Pure geometry engine** (`app/domain/geometry.py`) with three explicitly separated coordinate spaces and assumption **A-GEO-1** enforced by tests.
- SQLAlchemy tables, async session management, structured error envelope, health endpoints reporting every operating mode explicitly.

**Verified:** 63/63 geometry tests pass; Ruff clean; API boots, initialises the database and serves `/health/live`, `/health/ready`, `/health/case-location`.

**Outstanding in this phase:** Next.js app, Docker Compose (with the optional Ollama profile), Makefile/PowerShell scripts, GitHub Actions workflow.

---

## Phase 3 — geometry engine and calibration data complete

The geometry engine and the committed calibration are done; the `/dev/roof-calibration` UI is not yet built.

**Calibration result** — `apps/api/app/data/fixed_roof_calibration.json`, in source-map pixels:

| Quantity | Value |
|---|---|
| Footprint | 11.216 m × 7.143 m = **80.11 m²** |
| Ridge | 4.073 m |
| Vertices / edges / facets | 6 / 9 (4 eave, 4 hip, 1 ridge) / 4 |
| Facet azimuths | N 10.6° · E 100.6° · S 190.6° · W 280.6° |

Topology matches the brief's reference overlay exactly: four outer eaves, one central ridge, four hips, two trapezoids and two triangles. Visual confirmation: [`images/roof-calibration-derivation.png`](images/roof-calibration-derivation.png).

**Two segmentation problems worth recording**

1. The raster centre *is* the resolved case coordinate and *does* land on the target roof — but squarely on a roof vent 45 grey levels darker than the roof plane. Seeding there grew a 400-pixel blob of vent. The seed is now the pixel nearest the median of a surrounding disc.
2. Neighbouring houses share roof material and are linked by same-brightness boundary walls, so a plain flood fill bridged into them and returned a quarter of the street. A morphological opening snaps those bridges before the component is taken.

Parameters are chosen by **stability, not taste**: sweeping tolerance and erosion depth, 16 of 25 combinations agree to within 6%, so the fit is a property of the image rather than of the settings.

**A coordinate-space bug the debug overlay caught.** Segmentation points are window-relative and were being passed through as source-map pixels, offsetting the entire calibration by the 410 px window origin. The numbers looked entirely plausible — correct dimensions, correct aspect ratio, correct facet azimuths — and only rendering the overlay on the imagery exposed it. This is precisely the failure mode the three-coordinate-space discipline exists to prevent.

**Capacity, measured.** The earlier forecast that 9.6 kWp would be infeasible was **wrong**. The calibrated roof holds exactly 24 panels — north 9, south 9, west 3, east 3 — so all three case sizes are satisfiable. See [`location-verification.md`](location-verification.md) §6.

---

## Phases 4–6 — deterministic engineering core complete

**211 tests pass** (2 live-marked tests deselected by default), Ruff clean.

### Panel placement

Placement runs in facet **surface** coordinates, where a panel really is 1 × 2 m. Both orientations are swept over a bounded grid of origin offsets, and a panel survives only if its **whole footprint** is covered by the usable polygon, so nothing overhangs a hip. Facets are combined by an exact DP over expected production.

| Facet | Sloped area | Capacity | Specific yield |
|---|---|---|---|
| North trapezoid | 30.12 m² | 9 | 1,678.7 kWh/kWp |
| West triangle | 14.08 m² | 3 | 1,515.3 |
| East triangle | 14.07 m² | 3 | 1,367.2 |
| South trapezoid | 30.12 m² | 9 | 1,119.8 |

The **6 kWp case is the one that proves the allocator is production-driven**: north and south are the same size and hold 9 panels each, so ranking by area would put the remaining 6 on south. The correct answer fills both small triangles and leaves south empty.

### Verified end-to-end (live PVGIS + live ECB rate)

`1 USD = 0.87897 EUR`, dated 2026-07-24, ECB via Frankfurter, retrieved live. CAPEX $10,000 → **€8,789.70**.

| System | Panels | Allocation | Production | Coverage | Savings | Payback | 20-yr net |
|---|---|---|---|---|---|---|---|
| 3.6 kWp | 9 | N 9 | 6,043 kWh | 43.8 % | €1,510.79 | 5.82 yr | €21,426.10 |
| 6.0 kWp | 15 | N 9 · W 3 · E 3 | 9,502 kWh | 68.9 % | €2,375.55 | 3.70 yr | €38,721.30 |
| 9.6 kWp | 24 | all four facets | 13,534 kWh | 98.1 % | €3,383.40 | 2.60 yr | €58,878.30 |

### A defect a test caught that review would not have

Carrying full precision through the cash flow and rounding each year for display made consecutive rows differ by €2,530.57 in some years and €2,530.58 in others. Every individual figure was correctly rounded, yet a customer adding up the printed table could not reconcile it against the printed annual saving. Money is now canonicalised to cents once and the series derived from that — a 5-cent change to the 20-year figure that makes the table internally exact.

### Structural guarantees, not conventions

- **Parity is unreachable.** No setting exposes a fixed USD/EUR rate, no code path defaults to 1.0, and a test greps the source for the usual offenders. Fallback is live → cache → labelled fixture, with stale cached rates rejected.
- **Fixture never masquerades as live.** One parser serves both paths; the retrieval source travels with every value.
- **The optimiser never depended on the live PVGIS client.** It was built and tested against the `FacetYieldRankingProvider` port; the live implementation was added afterwards and changed no optimiser code or test.

---

## Requirement traceability matrix

| # | Requirement | Backend | Endpoint | Frontend | Tests | Status |
|---|---|---|---|---|---|---|
| 1 | Chat-driven flow | state machine + parser | `POST /projects/{id}/chat` | `components/chat` | — | ⬜ |
| 2 | Local LLM, structured output | `integrations/ollama.py` | via chat | AI status badge | — | ⬜ |
| 3 | Location input step | `services/location.py` | via chat | chat step | — | ⬜ |
| 4 | Fixed property resolution | location resolver | via chat | chat step | — | ⬜ |
| 5 | Coordinate sign verified | `CaseLocationSettings` | `GET /health/case-location` | — | ✅ | ✅ |
| 6 | 1,150 kWh consumption | consumption state | via chat | chat step | — | ⬜ |
| 7 | Exactly three system sizes | whitelist | via chat | size cards | — | ⬜ |
| 8 | Google Static Maps | `integrations/google_maps.py` | `GET /maps/satellite` | Konva bg | — | ⬜ |
| 9 | Fixture mode, no key | fixture loader | `GET /maps/satellite` | fixture badge | — | 🔨 |
| 10 | Four facets | calibration data ✅ | `GET /roof/fixed-model` | facet layer | — | 🔨 |
| 11 | All outer eave edges | calibration data ✅ | `GET /roof/fixed-model` | edge layer | — | 🔨 |
| 12 | Ridge + hip edges | calibration data ✅ | `GET /roof/fixed-model` | edge layer | — | 🔨 |
| 13 | Metric edge measurements | `domain/geometry.py` | `GET /roof/fixed-model` | measurement layer | — |✅ |
| 14 | Pixel-to-metre (Web Mercator) | `domain/geometry.py` | — | — | ✅ 12 tests | ✅ |
| 15 | 25° pitch | roof model | — | facet labels | — |✅ |
| 16 | Projected + sloped area | `domain/geometry.py` | — | facet table | ✅ 9 tests | 🔨 |
| 17 | Facet azimuth + PVGIS aspect | `domain/geometry.py` | — | facet table | ✅ 9 tests | 🔨 |
| 18 | Automatic panel placement | `services/layout.py` | `POST /projects/{id}/layout` | panel layer | — |✅ |
| 19 | Physical 1×2 m panel size | surface coordinates | — | panel layer | — |✅ |
| 20 | Containment + no overlap | Shapely validation | — | — | — |✅ |
| 21 | Higher-yield facet preference | `FacetYieldRankingProvider` | — | — | — |✅ |
| 22 | Honest capacity limitation | layout service | `POST /projects/{id}/layout` | capacity warning | — |✅ |
| 23 | Facet-level PVGIS | `integrations/pvgis.py` | `POST /projects/{id}/yield` | energy cards | — |✅ |
| 24 | Total + monthly production | yield aggregator | `POST /projects/{id}/yield` | monthly chart | — |✅ |
| 25 | Electricity price €0.25/kWh | finance settings | — | KPI card | — |✅ |
| 26 | Original CAPEX $10,000 USD | domain value | — | KPI card | — |✅ |
| 27 | Live FX via Frankfurter/ECB | `integrations/exchange_rates.py` | `POST /projects/{id}/exchange-rate` | FX row | — |✅ |
| 28 | CAPEX USD→EUR conversion | FX service | — | KPI card | — |✅ |
| 29 | No silent USD/EUR parity | FX service | — | — | — |✅ |
| 30 | FX snapshot immutability | proposal snapshot | `POST /projects/{id}/finalize` | — | — | ⬜ |
| 31 | Savings + payback | `services/financial.py` | `POST /projects/{id}/financials` | KPI cards | — |✅ |
| 32 | 20-year cash flow | `services/financial.py` | `POST /projects/{id}/financials` | cash-flow chart | — |✅ |
| 33 | PDF proposal | `services/pdf.py` | `GET /proposals/{token}/pdf` | download action | — | ⬜ |
| 34 | Shareable web proposal | proposal repo | `GET /proposals/{token}` | `/proposal/[token]` | — | ⬜ |
| 35 | Proposal view tracking (bonus) | view service | `POST /proposals/{token}/view` | — | — | ⬜ |
| 36 | Case questions answered | — | — | — | — | ⬜ |
| 37 | Asset licensing documented | — | — | — | — | ✅ |
