# Implementation Status

Live build log and requirement-traceability matrix. Updated at the end of every phase.

**Nothing is marked ✅ until it actually runs and its tests pass.** Legend: ✅ done · 🔨 in progress · ⬜ not started

_Last updated: end of Phase 0._

---

## Phase progress

| Phase | Scope | Status |
|---|---|---|
| 0 | Source audit, coordinate verification, fixture provenance, licensing | ✅ |
| 1 | Foundation — monorepo, Docker Compose, CI, tooling | 🔨 |
| 2 | Deterministic workflow core — state machine, steps, rules parser | ⬜ |
| 3 | Geometry engine + roof calibration tool | ⬜ |
| 4 | Panel placement optimiser | ⬜ |
| 5 | PVGIS + FX integrations | ⬜ |
| 6 | Financial service | ⬜ |
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

## Requirement traceability matrix

| # | Requirement | Backend | Endpoint | Frontend | Tests | Status |
|---|---|---|---|---|---|---|
| 1 | Chat-driven flow | state machine + parser | `POST /projects/{id}/chat` | `components/chat` | — | ⬜ |
| 2 | Local LLM, structured output | `integrations/ollama.py` | via chat | AI status badge | — | ⬜ |
| 3 | Location input step | `services/location.py` | via chat | chat step | — | ⬜ |
| 4 | Fixed property resolution | location resolver | via chat | chat step | — | ⬜ |
| 5 | Coordinate sign verified | `CaseLocationSettings` | — | — | — | ✅ |
| 6 | 1,150 kWh consumption | consumption state | via chat | chat step | — | ⬜ |
| 7 | Exactly three system sizes | whitelist | via chat | size cards | — | ⬜ |
| 8 | Google Static Maps | `integrations/google_maps.py` | `GET /maps/satellite` | Konva bg | — | ⬜ |
| 9 | Fixture mode, no key | fixture loader | `GET /maps/satellite` | fixture badge | — | 🔨 |
| 10 | Four facets | calibration data | `GET /roof/fixed-model` | facet layer | — | ⬜ |
| 11 | All outer eave edges | roof model | `GET /roof/fixed-model` | edge layer | — | ⬜ |
| 12 | Ridge + hip edges | roof model | `GET /roof/fixed-model` | edge layer | — | ⬜ |
| 13 | Metric edge measurements | `domain/geometry.py` | `GET /roof/fixed-model` | measurement layer | — | ⬜ |
| 14 | Pixel-to-metre (Web Mercator) | `domain/geometry.py` | — | — | — | ⬜ |
| 15 | 25° pitch | roof model | — | facet labels | — | ⬜ |
| 16 | Projected + sloped area | `domain/geometry.py` | — | facet table | — | ⬜ |
| 17 | Facet azimuth + PVGIS aspect | `domain/geometry.py` | — | facet table | — | ⬜ |
| 18 | Automatic panel placement | `services/layout.py` | `POST /projects/{id}/layout` | panel layer | — | ⬜ |
| 19 | Physical 1×2 m panel size | surface coordinates | — | panel layer | — | ⬜ |
| 20 | Containment + no overlap | Shapely validation | — | — | — | ⬜ |
| 21 | Higher-yield facet preference | `FacetYieldRankingProvider` | — | — | — | ⬜ |
| 22 | Honest capacity limitation | layout service | `POST /projects/{id}/layout` | capacity warning | — | ⬜ |
| 23 | Facet-level PVGIS | `integrations/pvgis.py` | `POST /projects/{id}/yield` | energy cards | — | ⬜ |
| 24 | Total + monthly production | yield aggregator | `POST /projects/{id}/yield` | monthly chart | — | ⬜ |
| 25 | Electricity price €0.25/kWh | finance settings | — | KPI card | — | ⬜ |
| 26 | Original CAPEX $10,000 USD | domain value | — | KPI card | — | ⬜ |
| 27 | Live FX via Frankfurter/ECB | `integrations/exchange_rates.py` | `POST /projects/{id}/exchange-rate` | FX row | — | ⬜ |
| 28 | CAPEX USD→EUR conversion | FX service | — | KPI card | — | ⬜ |
| 29 | No silent USD/EUR parity | FX service | — | — | — | ⬜ |
| 30 | FX snapshot immutability | proposal snapshot | `POST /projects/{id}/finalize` | — | — | ⬜ |
| 31 | Savings + payback | `services/financial.py` | `POST /projects/{id}/financials` | KPI cards | — | ⬜ |
| 32 | 20-year cash flow | `services/financial.py` | `POST /projects/{id}/financials` | cash-flow chart | — | ⬜ |
| 33 | PDF proposal | `services/pdf.py` | `GET /proposals/{token}/pdf` | download action | — | ⬜ |
| 34 | Shareable web proposal | proposal repo | `GET /proposals/{token}` | `/proposal/[token]` | — | ⬜ |
| 35 | Proposal view tracking (bonus) | view service | `POST /proposals/{token}/view` | — | — | ⬜ |
| 36 | Case questions answered | — | — | — | — | ⬜ |
| 37 | Asset licensing documented | — | — | — | — | ✅ |
