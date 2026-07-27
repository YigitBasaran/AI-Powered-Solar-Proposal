# Implementation Status

Live build log and requirement-traceability matrix. Updated at the end of every phase.

**Nothing is marked ✅ until it actually runs and its tests pass.** Legend: ✅ done · 🔨 in progress · ⬜ not started

_Last updated: all phases complete. 355 API tests + 20 web tests + 10 E2E passing; archive verified from a clean extraction._

---

## Phase progress

| Phase | Scope | Status |
|---|---|---|
| 0 | Source audit, coordinate verification, fixture provenance, licensing | ✅ |
| 1 | Foundation — API, config, DB, tooling (web + Docker outstanding) | ✅ |
| 2 | Deterministic workflow core — state machine, steps, rules parser | ✅ |
| 3 | Geometry engine ✅ + calibration data ✅ + calibration UI ⬜ | ✅ |
| 4 | Panel placement optimiser | ✅ |
| 5 | PVGIS + FX integrations | ✅ |
| 6 | Financial service | ✅ |
| 7 | Product shell and visualisation | ✅ |
| 8 | Proposal, PDF, share route | ✅ |
| 9 | Ollama conversational layer | ✅ |
| 10 | Tracking bonus, hardening, packaging | ✅ |

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

## Phases 2 and 8 — complete backend

**342 tests pass** (2 live-marked deselected), Ruff clean. Integration tests run fully offline against a throwaway database in fixture mode — the exact configuration a reviewer gets from a clean clone with no credentials and no model pulled.

### The case flow, over HTTP, with no credentials

`POST /projects` → `-34.04658, 18.46491` → `1,150 kWh` → `the middle option` → `run-analysis` → `finalize` → share route → PDF. Every step parsed by the deterministic rules parser; `LLM_PROVIDER=rules` is a complete implementation, not a degraded one.

The parser is **step-aware**, which is what makes it safe rather than merely convenient: the bare token `6` is 6 kWp at the system-size step and 6 kWh/month at the consumption step. Two defects it caught in itself: `lat X lon Y` did not match (separator class too narrow) and `-500` lost its sign, parsing as a valid 500 kWh.

Unsupported sizes are **refused, not snapped** — quoting 5 kWp as 6 kWp would misrepresent what the customer asked for. Model-supplied values are re-validated against the same whitelist, so the LLM cannot reach the domain with a value the rules would have rejected.

### Immutability, demonstrated

A test forces a different rate into the FX cache after finalisation and asserts the stored proposal's rate, converted CAPEX and payback are all unchanged. The share page and the PDF read one snapshot and neither recomputes, so they cannot disagree.

### The PDF

Jinja2 → Chromium → A4, page numbers, stable breaks, generated from the real application into `sample-output/example-proposal.pdf`.

Charts are **server-rendered inline SVG**, not a JS charting library: Chromium then has nothing to wait on beyond fonts — no script load, no animation, no race with the print call. The PDF also **draws the roof itself** from stored source-pixel geometry when no Konva export has been uploaded; without that fallback, a proposal generated straight from the API would show no reconstruction at all, which is the first thing a reviewer would hit.

Every non-live data source is surfaced in the assumptions rather than quietly omitted — fixture FX, fixture PVGIS and fixture imagery each add an explicit note.

---

## Phases 7 and 10 — product shell, packaging, verification

**355 API tests · 20 web unit tests · 10 Playwright E2E tests.** Ruff clean, TypeScript strict clean, production build passes, archive verified from a clean extraction.

### Two defects that only a clean extraction could find

Both were invisible in the development tree and would have hit a reviewer on their first command.

1. **`.env.example` did not load.** `SMTP_PORT=` (blank) failed integer validation, so `Settings` raised on construction. Copying that file to `.env` is the first line of the quick start, and `docker compose` reads the same file — so the documented setup path was broken while everything worked locally, because there was no `.env` in the tree at all and defaults applied. Fixed as a class of bug: a blank value now means "not configured" and falls back to the default, while blanks are preserved for genuine string settings where `""` is meaningful.

2. **The fixtures path was inferred from source-file depth.** `parents[4]` has no equivalent when the package sits at `/app/app` in the container. Now an explicit setting rather than a guess about layout.

A third, smaller one: the verifier probed for Python with `command -v python3`, which succeeds against the Windows Store stub that then refuses to run. It now probes by executing each candidate.

### Honest record

The commit `674b333` was made with one test failing and its message claimed 354 passing when the real result was 353 passed / 1 failed. Corrected in the following commit. The test was wrong, not the code — it grepped the whole of `.env.example` for `CASE_USD_EUR`, which appears there in a comment explaining why no such setting exists.

### Packaging

`git archive` builds the submission, so `.gitignore` is the single definition of what ships and there is no second exclude list to drift. Pre-flight refuses a tracked `.env`, greps for key patterns, and fails if the sample PDF is missing. `verify-submission.sh` extracts to a throwaway directory and follows the README from scratch.

---

## Requirement traceability matrix

**Rebuilt manually, row by row, on 2026-07-27.**

A previous revision of this file was bulk-edited by a regex that flipped every
remaining marker to ✅ — including rows that had never been verified, and
corrupting several cells into nonsense. That was wrong and is the reason this
table now carries an explicit *Evidence* column: a status is only as good as
the command that backs it.

**Status is only ✅ when the implementation exists, a named verification has
actually been run, and it passed.** 🔨 means implemented but not yet verified
by a test. ⬜ means not implemented.

| # | Requirement | Implementation | Evidence | Status |
|---|---|---|---|---|
| 1 | Chat-driven flow | `services/workflow.py`, `api/v1/projects.py` | `test_workflow_api.py` · E2E `welcomes the user…` | ✅ |
| 2 | Local LLM, structured output | `integrations/ollama.py` | `test_ollama.py` (17) + `test_chat.py` (22) | ✅ |
| 3 | Location input step | `services/workflow.py` | `test_workflow_api.py::test_location_resolves…` | ✅ |
| 4 | Fixed property resolution | location resolver in workflow | `test_any_location_still_resolves_to_the_case_property` | ✅ |
| 5 | Coordinate sign verified | `CaseLocationSettings` | `test_config.py::test_case_location_keeps_both_coordinates`; `docs/location-verification.md` | ✅ |
| 6 | 1,150 kWh consumption | consumption step | `test_consumption_is_multiplied_out_deterministically` | ✅ |
| 7 | Exactly three system sizes | whitelist in settings + workflow | `test_exactly_three_system_sizes_are_offered` | ✅ |
| 8 | Google Static Maps (live) | `api/v1/maps.py` | `test_maps.py` (14): request matches the documented contract, key never reaches the client, bad responses rejected. **Never exercised against Google itself** — no API key. See *Not claimed*. | 🔨 |
| 9 | Fixture mode, no key | `api/v1/maps.py` | `test_satellite_image_is_served_same_origin_and_labelled` | ✅ |
| 10 | Four facets | `data/fixed_roof_calibration.json` | `test_roof_service.py::test_roof_has_four_facets` | ✅ |
| 11 | All outer eave edges | calibration + `services/roof.py` | `test_roof_has_every_required_edge` (4 eaves asserted) | ✅ |
| 12 | Ridge + hip edges | calibration + `services/roof.py` | `test_roof_has_every_required_edge` (4 hips, 1 ridge) | ✅ |
| 13 | Metric edge measurements | `domain/geometry.py` | `test_summary_reports_measurements_for_every_edge` | ✅ |
| 14 | Pixel-to-metre (Web Mercator) | `domain/geometry.py` | `test_geometry.py` — 7 scale tests | ✅ |
| 15 | 25° pitch drives geometry | roof model | `test_each_facet_sloped_area_uses_the_pitch` | ✅ |
| 16 | Projected + sloped area | `domain/geometry.py` | `test_sloped_area_is_projected_over_cos_pitch` | ✅ |
| 17 | Facet azimuth + PVGIS aspect | `domain/geometry.py` | `test_geometry.py` cardinal tests + `test_roof_service.py` | ✅ |
| 18 | Automatic panel placement | `services/layout.py` | `test_layout.py` — 40 tests | ✅ |
| 19 | Physical 1×2 m panel size | surface coordinates | `test_panels_are_physically_one_by_two_metres_on_the_slope` | ✅ |
| 20 | Containment + no overlap | Shapely + `assert_layout_valid` | `test_every_panel_lies_fully_within_its_facet`, `test_no_two_panels_overlap` | ✅ |
| 21 | Higher-yield facet preference | `FacetYieldRankingProvider` + DP | `test_medium_system_prefers_small_high_yield_facets_over_a_large_poor_one` | ✅ |
| 22 | Honest capacity limitation | layout service | `test_large_setback_reduces_capacity_and_raises_a_warning` | ✅ |
| 23 | Facet-level PVGIS | `integrations/pvgis.py` | `test_pvgis.py` (26) + live-marked ordering test | ✅ |
| 24 | Total + monthly production | `services/analysis.py` | `test_energy_is_calculated_per_occupied_facet` | ✅ |
| 25 | Electricity price €0.25/kWh | settings | `test_financial.py::test_case_annual_savings` | ✅ |
| 26 | Original CAPEX $10,000 USD | settings + domain | `test_capex_converts_by_multiplying_by_the_rate` | ✅ |
| 27 | Live FX via Frankfurter/ECB | `integrations/exchange_rates.py` | `test_live_rate_calls_the_right_endpoint_with_ecb_provider` | ✅ |
| 28 | CAPEX USD→EUR conversion | `services/financial.py` | `test_capex_conversion_direction_is_not_inverted` | ✅ |
| 29 | No silent USD/EUR parity | FX service (no such setting exists) | `test_no_path_ever_returns_parity`, `test_source_repository_contains_no_parity_literal` | ✅ |
| 30 | FX snapshot immutability | `services/proposal.py` | `test_a_moved_market_rate_does_not_change_a_finalised_proposal` | ✅ |
| 31 | Savings + payback | `services/financial.py` | `test_financial.py` — 25 tests | ✅ |
| 32 | 20-year cash flow | `services/financial.py` | `test_cash_flow_spans_year_zero_to_twenty` | ✅ |
| 33 | PDF proposal | `services/pdf.py` | `test_pdf_is_a_real_pdf`; `sample-output/example-proposal.pdf` | ✅ |
| 34 | Shareable web proposal | `/proposal/[token]` | `test_proposal_api.py` + E2E happy path | ✅ |
| 35 | Proposal view tracking (bonus) | `services/proposal.py` | `test_view_is_recorded_and_counted` | ✅ |
| 36 | Case questions answered | `docs/case-questions.md` | Document exists and is complete | ✅ |
| 37 | Asset licensing documented | `LICENSE-NOTICE.md` | Document exists and is complete | ✅ |
| 38 | Roof calibration tool (§26) | `/dev/roof-calibration` | `calibration.test.ts` (18); route returns 200 in the container | ✅ |
| 39 | Alembic migrations (§22) | `migrations/`, `alembic.ini` | `test_schema_parity.py` (16); `alembic current` → `1c779d205bda (head)` in the container | ✅ |
| 40 | AI executive summary (§24) | `services/summary.py` | `test_summary.py` (21); prose with an invented number is discarded | ✅ |
| 41 | Docker Compose, no credentials | `docker-compose.yml` | `docker compose up --build` → healthy; full proposal + 104 KB PDF in-container; 10/10 E2E against it | ✅ |
| 42 | Required documentation set (§18) | `docs/` | All 12 present | ✅ |

### Verified operating modes

Every one of these completes a full proposal and serves a working share page.
Each fallback is **labelled**, never silently substituted.

| Mode | PVGIS source | FX source |
|---|---|---|
| All fixtures, rules only | `fixture` | `fixture` |
| `LLM_PROVIDER=disabled` | `fixture` | `fixture` |
| Ollama configured but unreachable | `fixture` | `fixture` (parser falls back to rules) |
| PVGIS live unreachable | `live_fallback_fixture` | `fixture` |
| FX live unreachable | `fixture` | `live_fallback_fixture` |
| Both live unreachable | `live_fallback_fixture` | `live_fallback_fixture` |

**PDF ↔ share page:** annual savings, both CAPEX figures, the 20-year net, the
FX rate and its date all appear identically in both, and the payback value is
object-identical. One snapshot, no recomputation.

### Not claimed

| Item | Status |
|---|---|
| Live Google Static Maps against the real API | **Unverified** — no API key available. The code path is unit-tested with a mocked transport; it has never received a real Google response. |
| GitHub Actions CI | **Unexecuted** — no git remote exists. Commands verified locally only. |
| 3D rendering bonus | **Not attempted** — the brief prioritises 2D, and the chosen bonus is view tracking. |
