# Known Limitations

What this system does not do, what has not been verified, and what would break first in production.

A limitation stated is a decision a reviewer can weigh. A limitation discovered by a customer is a defect.

---

## Not verified

These are honestly unproven. Nothing elsewhere in the repository claims otherwise.

| Item | Why |
|---|---|
| **Live Google Static Maps** | No API key was available. The code path is unit-tested with a mocked transport — it has never received a real Google response. Status, content type and payload size are validated, but the raster itself has not been seen. |
| **GitHub Actions CI** | No git remote exists, so `.github/workflows/ci.yml` has never executed. Its commands are verified locally. There is no green badge and none is implied. |
| **Live Ollama with a pulled model** | The adapter is fully tested against a mocked transport, including schema validation, timeout, invalid JSON and fallback. An attempt to pull `qwen3.5:2b` (2.7 GB) exhausted the disk and was abandoned, so no real model has ever answered. Whether it produces *good* extractions is unmeasured — only that whatever it returns is validated before use. |
| **SMTP notifications** | `EMAIL_MODE=console` is exercised; the SMTP branch is configuration-dependent and untested. |

## Not attempted

| Item | Reason |
|---|---|
| **3D rendering (Babylon/Three)** | The brief prioritises the 2D flow and warns against sacrificing it for 3D. The chosen bonus is proposal-view tracking. |
| **Geocoding** | Explicitly out of scope. Any entered location resolves to the fixed case property, and the UI says so. |
| **Multi-property support** | One calibrated roof, as the brief specifies. |

---

## Physical modelling

Ordered by how much each would move the number.

### No shading analysis
No near-object shading (trees, neighbouring buildings, the roof's own dormers) and no horizon shading. On a real site this is frequently the **largest single error** in the production estimate. PVGIS accepts a horizon profile, so the integration point exists.

### No obstruction detection
Chimneys, vents, skylights and HVAC units are not detected and not excluded. The optimiser already works against a Shapely polygon, so obstructions would be holes in it — the placement code needs no change, only the input.

Concretely on this property: the raster centre lands on a **roof vent**, which the calibration script detects as a segmentation problem but the model does not treat as an exclusion zone. A panel could be placed over it.

### Zero roof-edge setback
`ROOF_EDGE_SETBACK_M=0.0` is the brief's figure, not a safe default. Most jurisdictions require fire/access setbacks at ridge and eaves, which materially reduce capacity. The value is configurable and a 1 m setback is what the capacity-warning tests use.

### Planar facets with level eaves (A-GEO-1)
Documented and tested, but it is still an assumption. Dormers, valleys, multi-pitch and curved roofs break it. `validate_level_eave()` returns `True` when vertex heights are unknown — the assumption is then **recorded as taken on trust**, not proven.

### Imagery age and geometry
The imagery date is unknown and may predate changes to the property. Near-nadir capture is assumed; off-nadir parallax displaces a roof relative to its footprint and is not corrected.

### Calibration method
The footprint comes from a minimum-area rectangle fit, which suits this rectangular hip roof and would not suit an irregular one. `/dev/roof-calibration` exists so a human can correct it, and a human **should** confirm it against current imagery before any commercial use.

---

## Financial modelling

### Savings are capped at consumption
This follows the brief, and it is the **single largest source of optimism** in the savings figure. It implicitly assumes generation and consumption occur at the same moment. They do not — real self-consumption without a battery is typically 30–50 %.

An hourly (8,760-step) simulation is the first item in [`case-questions.md`](case-questions.md) for exactly this reason. Adding it would likely make the headline saving **worse**, which is correct.

### Deliberately excluded
Flat electricity price for 20 years · no module degradation (~0.5 %/yr in reality) · no inflation · no O&M · no financing cost · no tax effect · no export compensation.

Each is defensible for a feasibility estimate and each moves the answer.

### Rounding
Money is canonicalised to cents once, so the cash-flow table reconciles exactly. Energy is deliberately **not** — a 0.01 kWh residue on ~9,500 kWh sits far below PVGIS's own model uncertainty, and forcing agreement there would manufacture precision that does not exist. The two are treated differently on purpose.

---

## Engineering

### Packing does not scale
The exhaustive offset sweep plus exact DP is right for four facets and ≤24 panels. It is not right for a commercial roof with dozens of facets and hundreds of panels — rectangle packing into non-convex polygons is NP-hard. A time-boxed heuristic above a size threshold would be needed, and should report which solver ran.

### PDF rendering is in-request
Chromium is launched inside the request that asks for the PDF. Fine at this scale; under real load it needs a worker queue. Memory-hungry.

### Caches are process-local
The PVGIS cache lives in process memory and is lost on restart. The FX cache is database-backed and survives. Fine for one instance; a second replica would duplicate PVGIS calls.

### Share links do not expire
Tokens carry 192 bits of entropy and are unguessable, but there is no expiry, no revocation and no rate limiting on the public route. A leaked link is permanent.

### SQLite
One file, one writer. Correct for this workload; a real deployment would want Postgres. The SQLAlchemy layer is portable and Alembic migrations use batch mode, so the move is mechanical.

---

## Data and privacy

- Proposals contain a home address, consumption and financial position. Access control is the unguessable token and nothing else.
- View tracking hashes IPs and never stores them raw, but there is no retention policy and no disclosure to the viewer.
- Bundled imagery is licensed for **evaluation of this submission only** — see [`LICENSE-NOTICE.md`](../LICENSE-NOTICE.md). Anyone reusing this repository must supply their own imagery or run in live mode with their own key.

---

## Platform

- `make` is not available on Windows by default. The Makefile ships for parity; `scripts/setup.ps1` and the npm scripts are the real entry points.
- `python3` resolves to a non-functional Microsoft Store stub on many Windows machines. `verify-submission.sh` probes interpreters **by executing them** rather than by name for this reason.
- The API image carries Chromium for PDF rendering, so it is larger than a plain API image would be (662 MB). An earlier revision built on the Playwright base image and reached 3.75 GB; installing from pyproject onto python:3.12-slim removed most of that.

---

## What a real deployment would need first

1. Shading and obstruction analysis — the largest missing physical effect
2. Hourly simulation and self-consumption — the largest missing financial effect
3. Jurisdiction-specific setbacks and structural limits
4. A worker queue for PDF and simulation
5. Share-link expiry, revocation and rate limiting
6. Postgres, and a second replica with a shared cache
