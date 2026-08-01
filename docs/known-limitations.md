# Known Limitations

What this system does not do, what has not been verified, and what would break first in production.

A limitation stated is a decision a reviewer can weigh. A limitation discovered by a customer is a defect.

---

## Not verified

These are honestly unproven. Nothing elsewhere in the repository claims otherwise.

| Item | Why |
|---|---|
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

### Obstructions are modelled but not detected
One obstruction is now excluded: the **chimney on the north facet**, 2.99 m² in plan. It is a hole in the Shapely polygon the optimiser already worked against, so no placement code changed — only the input. `assert_layout_valid` names the obstruction if a panel is ever found standing on one, and `tests/unit/test_obstructions.py` proves the exclusion by rebuilding the roof without it and watching capacity go back from 21 to 24.

**It was outlined by hand, not detected.** Automatic detection was attempted and abandoned: brightness segmentation finds the roof's own rendered eave lines and the neighbouring wall before it finds a chimney, and a chimney reads as dark (shaded face plus cast shadow) about as often as bright. So every other obstruction on this roof — vents, skylights, HVAC — remains unmodelled, and a panel could still be placed over one. The raster centre in particular lands on a **roof vent** that is detected as a segmentation problem but is not an exclusion zone.

Anything added here must also be traced by hand, and a mis-declared `facet_id` is refused at load rather than silently subtracting nothing.

### Rotated arrays are not buildable as drawn
`PANEL_ROTATION_STEP_DEG=5` lets the optimiser turn each facet's array to fit more panels. On this roof it finds one extra bay on the east triangle at **45°**, taking capacity from 21 to 22. The figure is real; the installation is not.

On a pitched roof the panel is coplanar with the roof, so turning it changes **nothing** about tilt, azimuth or yield — it only changes packing. Against that single extra panel:

- **Mounting.** Hooks are fixed to the rafters and rails run parallel to the eave. A landscape or portrait panel presents a frame edge parallel to the rails for the clamp to grip. At 45° no edge is parallel to anything, so the array needs a bespoke sub-frame above the rails.
- **Certification.** Module makers specify where on the frame a clamp may sit, and the mechanical load rating depends on it. A diagonal mount supports the panel outside those zones — the rating and the mechanical warranty no longer apply.
- **Wind.** Racking edge-zone load tables assume rows parallel to the roof edges. A diagonal array is outside them.
- **Soiling.** The frame stands ~35–40 mm proud. An edge parallel to the fall line sheds water and dust; a diagonal edge dams them. At a dusty site this is a recurring yield loss that eats the gain.

**Set `PANEL_ROTATION_STEP_DEG=0` for anything a customer will act on.** It is on by default because it was asked for, not because it is recommended. And it does not solve the shortfall it was reached for: 22 panels is still short of the 24 the largest system needs.

The honest lever for that shortfall is module power, not packing geometry: 21 panels at 457 W is 9.6 kWp, and 450–500 W modules are mainstream. `PANEL_POWER_WP` is configurable, but `required_panel_count` insists a system size be a whole number of panels, so the `ALLOWED_SYSTEM_SIZES_KWP` ladder has to move with it.

### Zero roof-edge setback
`ROOF_EDGE_SETBACK_M=0.0` is the brief's figure, not a safe default. Most jurisdictions require fire/access setbacks at ridge and eaves, which materially reduce capacity. The value is configurable and a 1 m setback is what the capacity-warning tests use.

### Planar facets with level eaves (A-GEO-1)
Documented and tested, but it is still an assumption. Dormers, valleys, multi-pitch and curved roofs break it. `validate_level_eave()` returns `True` when vertex heights are unknown — the assumption is then **recorded as taken on trust**, not proven.

### Imagery age and geometry
The imagery date is unknown and may predate changes to the property. Near-nadir capture is assumed; off-nadir parallax displaces a roof relative to its footprint and is not corrected.

### Calibration method
The footprint started as a minimum-area rectangle fit. It is no longer one: an operator inspected the raster and moved `v_corner_a` and `v_ridge_0`, so the outline is a general quadrilateral and the hips are no longer all at 45° in plan. Two consequences are worth stating plainly.

- **The automated derivation script no longer reproduces the committed geometry.** `scripts/derive_roof_calibration.py` would discard both corrections. The calibration's `derivation` field says so.
- **The plan geometry and the single 25° pitch are now ~1.7° inconsistent.** Uniform pitch on a rectangle forces `ridge = long − short` and 45° hips; the corrected outline satisfies neither exactly. Pitch was never measured from imagery — it is the brief's figure — so per-facet tilt is not derived from two hand-placed marks. The residual is recorded, not modelled.

The gradient-response method that produced the original registration was *not* trusted for these two points: it locks onto the shadow bands beside the true edges, so its preferred positions are not reliably the correct ones. A human **should** confirm the outline against current imagery before any commercial use.

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

### Live Google imagery is a hard runtime dependency
The application cannot show a roof, or measure one, without reaching Google
Static Maps. There is no fixture mode and no substituted raster on failure: a
stored image served in place of a failed fetch is how a correct-looking outline
ends up drawn over the wrong picture, which happened here once already. A Google
Maps API key is therefore required to run the product; tests are unaffected,
because they point `GOOGLE_STATIC_MAPS_BASE_URL` at a local stub.

**The calibration is pinned to one exact imagery configuration** — by a strict
request signature and by a perceptual hash of the raster itself. If Google
re-flies this tile, and it will, the map keeps rendering while measurement,
panel layout and finalisation stop with a re-trace instruction. That is the
intended failure direction and it is worth writing down because it will fire.

Re-tracing is a **human** step. `/dev/roof-calibration` exists for it, the
committed profile records `status: verified` with who verified it and when, and
a profile still marked provisional fails its own test rather than shipping.

The two providers do not agree: the Esri fixture and Google's capture place this
building about **1.2 m** apart. That is why the fixture is test-replay data only
and no longer stands in for live imagery anywhere in the product.

### Live PVGIS is a hard runtime dependency
**An offline deployment cannot complete an analysis.** This is by design, and it
is the largest single trade-off in the system.

There is no fixture mode, no fallback to a captured payload, no synthetic
estimate and no partial answer: if any facet probe fails after its retry budget,
the whole analysis fails, the project is marked `failed` with the reason, and
finalisation is blocked. The alternative was worse — until this change, a
customer could receive a proposal quoting an annual production figure that had
never been observed for their roof. The substitution was labelled in the
snapshot; nothing downstream read the label.

The test suites remain fully offline, but not because the application has an
offline path: they point `PVGIS_BASE_URL` at a local replay server. The
*application* always makes a real call.

Trust is pinned to `https://re.jrc.ec.europa.eu/api/v5_3`, by exact origin and
exact path segments. If PVGIS ever moves origin or bumps its API version,
proposals stop being classified `live` and finalisation starts refusing until
the constant is updated deliberately. That is the intended failure direction and
it will eventually fire — better a refused document than a mislabelled one.

`ALLOW_REPLAY_PROPOSALS` is the only thing standing between a replayed capture
and an issued proposal. It is false everywhere but the test stacks, start-up
refuses it outside a recognised test environment, and `docker-compose.yml` states
it explicitly as false so a reader can see that the container cannot issue one.

### Caches are process-local
The FX cache is database-backed and survives a restart. There is **no PVGIS
cache at all** any more: the four probes are at four distinct aspects and all at
1 kWp, so no two lookups within a request could ever collide, and there is no
cross-request reuse by design — every new project issues fresh calls. What the
cache actually did was relabel a result as `cache`, which meant a snapshot could
report a source that was not where its numbers came from.

Probes *are* reused within one project when a system size changes, because they
are taken at 1 kWp before any size is chosen. That reuse is refused the moment
anything sent to PVGIS differs — location, pitch, aspect, loss, technology,
mounting, API version or origin — and refused outright for a replayed
observation unless the override above is set.

### A dependency outage makes the customer wait, and then fails
This used to be a degraded-tier curiosity; it is now the default failure cost,
because there is nothing to fall back to.

A PVGIS outage costs the full retry budget before the analysis fails: at most
`PVGIS_MAX_ATTEMPTS` (4) attempts within `PVGIS_RETRY_BUDGET_SECONDS` (30) **per
facet**. The four facets are probed concurrently on one connection pool, so a bad
day costs roughly one budget in wall-clock rather than four — about 30 seconds,
not two minutes. Backoff is exponential with full jitter, and a `Retry-After`
header is honoured but clamped to the remaining budget, so a server-suggested
300 s cannot hold a request open.

Not every status spends the whole ladder. 429/502/503/504/529, connection errors,
timeouts and a body that is not JSON at all get the full budget. **HTTP 500 gets
exactly one extra attempt** — it is ambiguous enough to be worth one retry and
not worth four. A permanent 4xx, a 3xx redirect, and a 200 whose body parses as
JSON but fails schema validation all raise immediately.

A production system would want a circuit breaker so the *second* customer during
an outage does not pay the same cost as the first.

### One analysis per project, enforced by a 120-second lease
Two concurrent `run-analysis` requests for one project used to issue two full
sets of probes and race to write, so the stored snapshot could describe inputs
that had already been superseded. A conditional UPDATE now grants the claim to
one and answers the other `409 ANALYSIS_IN_PROGRESS`, before any probe is issued.

The limitation is the lease. A hard process kill mid-analysis leaves the claim
held until it expires, so that project refuses a new analysis for up to
`ANALYSIS_LEASE_SECONDS` (120). The lease is validated at start-up against the
worst-case external-call window — a lease shorter than the work it protects is
worse than none, because it hands the project to a second batch while the first
is still holding a snapshot it means to write. That case is caught by a fencing
token (`analysis_run_id`) on every terminal write, which answers
`409 ANALYSIS_SUPERSEDED` rather than clobbering the fresher result.

### Revisions chain; they do not branch
Editing a project whose proposal has been finalised forks a **revision** — a new editable project with the change applied, only the dependent sections recomputed, and no proposal of its own. The issued proposal and its share link are immutable and permanent; finalising the revision mints a new link. `projects.revision_of_project_id` is UNIQUE, so a parent may have at most one direct child and a retried or concurrent change cannot fork two drafts.

The limitation is the shape: revisions form a **chain**, not a tree. There is no way to hold two alternative drafts of the same finalised proposal side by side, and no UI for browsing the chain — a customer sees only the current end of it. Both are deliberate for one calibrated property and one salesperson; a multi-quote workflow would want branching, and that would need the unique constraint replaced with a different idempotency key.

### Share links do not expire
Tokens carry 192 bits of entropy and are unguessable, but there is no expiry, no revocation and no rate limiting on the public route. A leaked link is permanent.

### SQLite
One file, one writer. The engine runs in WAL mode with a 5-second `busy_timeout`, so concurrent writers queue instead of failing instantly — verified by the E2E concurrency specs — but the write path is still serialised. Correct for this workload; a real deployment would want Postgres. The SQLAlchemy layer is portable and Alembic migrations use batch mode, so the move is mechanical.

Schema changes need care beyond the parity test. SQLite rebuilds a table to
alter a constraint, and a rebuild of a table other rows reference will cascade
their deletion unless foreign keys are suspended for the migration —
`migrations/env.py` does that, and `test_a_migration_never_deletes_dependent_rows`
holds it there. Postgres would not need the rebuild at all.

The serialised write path also makes *how long a transaction is held* load-bearing. Both slow paths — the first analysis and a selective recompute — commit their in-progress status marker **before** the network work rather than flushing it, so the write lock is not held across three PVGIS calls and an FX lookup. Holding it there queued every other writer behind the analysis and, past `busy_timeout`, produced "database is locked" as a 500 on an unrelated request. Found on 2026-07-29 by two E2E files running concurrently against the degraded stack; a second replica would surface the same class of problem elsewhere.

---

## Customers and email delivery

### `sent` means the provider accepted the message
Not that it arrived, not that it reached an inbox, and certainly not that anyone read it. SMTP offers no way to know any of those, so the schema has four statuses — `pending`, `sending`, `sent`, `failed` — and deliberately no `delivered`, `bounced` or `opened`. A status column that offered those would outlive whoever added it and would eventually be filled in with guesses.

### Delivery is at-least-once, not exactly-once
Every send is keyed on a deterministic idempotency key, so a double click, a refresh mid-send and a client retry collapse into one row. What that cannot cover is an **ambiguous timeout**: a relay may accept a message and then go quiet, and a later retry then produces a second copy in a real inbox. Avoiding it needs a transaction spanning the database and the mail server, which does not exist. The row stays `sending` and the UI says *"may still be in flight"* rather than guessing.

### Console mode records; it does not send
`EMAIL_MODE=console` renders the message, validates its headers, logs it and transmits nothing. The provider name travels on the delivery record, so every surface that reports it says *"recorded locally (console mode)"* rather than *"sent"*. This is the likeliest honesty failure in the feature, because every development run exercises it.

### There is no email-open tracking
The bonus the brief asks for is implemented as **proposal-page views**: a request to `POST /proposals/{token}/view`. Nothing observes whether an email was opened, and no tracking pixel is embedded — it would be both invasive and a poor measure of what it claims. The UI and the notification both say *"page view"* and never *"open"*.

### View counts are best-effort, not analytics
A repeat from the same reader within `PROPOSAL_VIEW_DEDUP_MINUTES` is not counted, and a user-agent deny-list drops crawlers and link unfurlers. Both are heuristics. A customer on a different device counts twice; a crawler with a novel user agent counts once. The count is a signal for a salesperson, not a metric.

### Deleting a customer destroys their proposals
`DELETE /customers/{id}` cascades: their projects go, and with them every proposal, transcript, view record and delivery. **An issued proposal's share link stops resolving**, and somebody may be holding it — this is the one operation in the application that can invalidate a document already sent. The confirmation counts exactly what will be destroyed before it happens, and `POST /customers/{id}/archive` is the non-destructive alternative that keeps every document intact. Deleting a *project* is refused outright once it has issued a proposal, or once a revision has been built on it.

### Customer email addresses are globally unique
There is no tenancy to scope them by, so two people sharing a household inbox need one record. Chosen because this screen picks who receives an email and ambiguity there is worse than the restriction. Reversing it means dropping the unique index and adding a disambiguating step to the picker.

### The send happens inside the request
There is no job queue. `smtplib` is blocking, so the call runs in a worker thread with an explicit timeout, and the delivery row is committed as `sending` *before* the provider is called so a process that dies mid-send leaves evidence rather than silence. A slow relay still occupies a worker. A queue is the obvious next step.

### Email validation is conservative, not RFC-complete
The domain module requires an unquoted dot-atom local part and a dotted domain with an alphabetic TLD. Legal-but-exotic addresses — quoted local parts, domain literals like `user@[192.168.0.1]` — are refused. Adding `pydantic[email]` would relax this in one line if it ever bites.

---

## Data and privacy

- Proposals contain a home address, consumption and financial position. Access control is the unguessable token and nothing else.
- **There is no authentication anywhere in this application.** Customer records, project routes and the delivery endpoints are exactly as open as `GET /projects/{id}` has always been. Adding customer names, addresses and email addresses raises the stakes of that considerably. It is mitigated — the public proposal projection is an allow-list that publishes a display name and nothing else, and the send routes take the internal proposal id rather than the share token — but it is **not solved**, and it is the first thing a real deployment needs.
- The customer email address is stored, and it is deliberately *not* published: it never appears in a public URL, in the public proposal payload, in the rendered PDF, in an activity event, or in a log line. Logs and audit rows carry a masked form (`a***@example.com`).
- Activity metadata is a per-event-type allow-list of scalars. Message bodies, subjects, provider responses, credentials, raw IP addresses and unmasked recipients cannot be stored there even by a caller that passes them.
- View tracking hashes IPs and never stores them raw. The hash is salted per deployment via `VIEW_HASH_SALT` — without a salt, `sha256(ip)` is a lookup table over a four-billion-value space and identifies the address exactly. There is still no retention policy and no disclosure to the viewer.
- There is no rate limiting on any route. The idempotency key and the delivery claim bound *duplicate* sends; they do not bound abuse.
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
