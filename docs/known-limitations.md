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
| **Ollama extraction quality** | The model *is* verified reachable: `qwen3.5:2b` was pulled on 2026-07-28 and the `@live` tier passes against it. What is uneven is its usefulness. Measured on 2026-07-29, ten of eleven probe messages — including every regression case the conversational redesign was written for — are settled deterministically in single-digit milliseconds; the model is consulted once, for the one phrasing no rule covers, and takes 2–7 s. It classifies *kind* reliably. It is less reliable about *values*: asked about *"about the same as we used last winter"* it returned a `provide_value` carrying an invented consumption figure, despite the prompt forbidding exactly that. The figure can only ever be a plausible consumption — never a computed one — and it is echoed straight back for correction, but it is a real weakness and is recorded rather than patched over. All of this is a property of a 2.3 B model, not of the integration, and it is why the workflow is built to run correctly on `LLM_PROVIDER=rules`. A larger model would need re-measuring, not re-coding. |
| **SMTP notifications** | `EMAIL_MODE=console` is exercised; the SMTP branch is configuration-dependent and untested. |
| **Full WCAG 2.1 AA compliance** | `@axe-core/playwright` runs clean over three screens, and the suite additionally proves keyboard-only completion, heading order and text-not-colour provenance. Automated tooling catches roughly a third of WCAG failures; no screen-reader, zoom or forced-colours testing has been done. **No compliance claim is made.** |

## Not attempted

| Item | Reason |
|---|---|
| **3D rendering (Babylon/Three)** | The brief prioritises the 2D flow and warns against sacrificing it for 3D. The chosen bonus is proposal-view tracking. |
| **Geocoding** | Explicitly out of scope — and, since there is no geocoder, an address *cannot* be checked against the calibrated property. A location away from the case coordinate is therefore refused, with the case property offered instead, and nothing is stored. Accepting it and analysing Cape Town's roof underneath — which an earlier build did — labelled every figure with a property it had never seen. See [`conversation.md`](conversation.md#the-property-is-fixed-and-said-so). |
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

### A dependency outage still makes the customer wait
With PVGIS and the FX provider both unreachable, an analysis takes about **25 seconds** before the labelled fallbacks are used — measured on the E2E degraded stack. Per-facet PVGIS requests are issued concurrently and each call has a bounded timeout and retry budget, but the budgets for the two services are spent in series and nothing is returned early. A production system would want a circuit breaker so that the *second* customer during an outage does not pay the same cost as the first.

### Revisions chain; they do not branch
Editing a project whose proposal has been finalised forks a **revision** — a new editable project with the change applied, only the dependent sections recomputed, and no proposal of its own. The issued proposal and its share link are immutable and permanent; finalising the revision mints a new link. `projects.revision_of_project_id` is UNIQUE, so a parent may have at most one direct child and a retried or concurrent change cannot fork two drafts.

The limitation is the shape: revisions form a **chain**, not a tree. There is no way to hold two alternative drafts of the same finalised proposal side by side, and no UI for browsing the chain — a customer sees only the current end of it. Both are deliberate for one calibrated property and one salesperson; a multi-quote workflow would want branching, and that would need the unique constraint replaced with a different idempotency key.

### Share links do not expire
Tokens carry 192 bits of entropy and are unguessable, but there is no expiry, no revocation and no rate limiting on the public route. A leaked link is permanent.

### SQLite
One file, one writer. The engine runs in WAL mode with a 5-second `busy_timeout`, so concurrent writers queue instead of failing instantly — verified by the E2E concurrency specs — but the write path is still serialised. Correct for this workload; a real deployment would want Postgres. The SQLAlchemy layer is portable and Alembic migrations use batch mode, so the move is mechanical.

The serialised write path also makes *how long a transaction is held* load-bearing. Both slow paths — the first analysis and a selective recompute — commit their in-progress status marker **before** the network work rather than flushing it, so the write lock is not held across three PVGIS calls and an FX lookup. Holding it there queued every other writer behind the analysis and, past `busy_timeout`, produced "database is locked" as a 500 on an unrelated request. Found on 2026-07-29 by two E2E files running concurrently against the degraded stack; a second replica would surface the same class of problem elsewhere.

---

## Data and privacy

- Proposals contain a home address, consumption and financial position. Access control is the unguessable token and nothing else.
- View tracking hashes IPs and never stores them raw, but there is no retention policy and no disclosure to the viewer.
- Bundled imagery is licensed for **evaluation of this submission only** — see [`LICENSE-NOTICE.md`](../LICENSE-NOTICE.md). Anyone reusing this repository must supply their own imagery or run in live mode with their own key.

---

## Platform

- `make` is not available on Windows by default. The Makefile ships for parity; `scripts/setup.ps1` and the npm scripts are the real entry points.
- `python3` resolves to a non-functional Microsoft Store stub on many Windows machines. Both `verify-submission.sh` and `verify-submission.ps1` probe interpreters **by executing them** rather than by name for this reason.
- Packaging and verification ship in **both** shells (`scripts/*.sh` and `scripts/*.ps1`), and both were executed on this machine. The `.ps1` scripts are real PowerShell, not wrappers that shell out to Bash, because a Windows machine without Git Bash cannot run the `.sh` versions at all.
- Connecting to an unbound local port on Windows **times out** rather than being refused — the SYN is dropped. Anything that simulates an unreachable service should use an unresolvable host (`*.invalid`) instead, which the E2E degraded stack does.
- The API image carries Chromium for PDF rendering, so it is much larger than a plain API image would be: **2.44 GB**, measured on 2026-07-28 (the web image is 1.74 GB). An earlier revision built on the Playwright base image; installing from pyproject onto  removed a layer of duplication but not Chromium itself, which is what makes in-container PDF rendering work. Splitting rendering into its own service would shrink the API image and is the obvious next step.

---

## Conversation

### The knowledge registry is curated, not retrieved
About thirty typed entries cover the workflow, the assumptions and the scope. There is no vector store and no retrieval: the question space is small and known, a lookup table over it is faster and auditable, and — unlike a nearest-neighbour search — it can say *"there is no entry for that"*. The limitation is the obvious one: a question outside those entries gets an honest "I don't have an answer for that one" plus a list of what it can explain, rather than an attempt.

### It is a workflow assistant, not a solar consultant
It answers questions about this workflow, this property and the assumptions behind these figures. It does not provide electrical design, structural certification, permitting advice or a binding quotation, and it does not reconstruct arbitrary roofs. Asked for any of those it says so.

### An answer's provenance is recorded but not shown
Every reply records its answer state, its source and the help entry it used in `ChatMessage.payload_json`. None of that is surfaced in the UI beyond the fallback chip and its expandable detail. A reviewer can read it from the API or the database; a customer cannot see it in the transcript.

---

## What a real deployment would need first

1. Shading and obstruction analysis — the largest missing physical effect
2. Hourly simulation and self-consumption — the largest missing financial effect
3. Jurisdiction-specific setbacks and structural limits
4. A worker queue for PDF and simulation
5. Share-link expiry, revocation and rate limiting
6. Postgres, and a second replica with a shared cache
