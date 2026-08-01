# Testing

What is tested, what deliberately is not, and how to run each layer.

The guiding rule: **exact numeric assertions belong to replayed captures; live tests assert invariants and ranges.** PVGIS revises its radiation datasets and the ECB rate moves daily — pinning either would make the suite fail for reasons unrelated to this code.

A second rule, learned the hard way: **assert the transport, not the figures.** A replayed PVGIS response and a live one produce identical numbers, so every value assertion in this suite passes whether or not the call was made. Twice during this work the suite ran green while making zero HTTP requests. What catches that is `test_pvgis_is_a_real_call.py`, which watches the transport, and the `data-tone="replay"` badge in `energy-and-fx.spec.ts`, which only the stub path can produce.

---

## Running it

```bash
# API — offline, deterministic, no credentials
cd apps/api && ./.venv/Scripts/python -m pytest -q -m "not live"

# API — the live-marked set (hits PVGIS and Frankfurter for real)
cd apps/api && ./.venv/Scripts/python -m pytest -q -m live

# Web unit
cd apps/web && npm run typecheck && npm run test && npm run build

# Static analysis
cd apps/api && ./.venv/Scripts/python -m ruff check app tests
cd apps/api && ./.venv/Scripts/python -m mypy app
cd apps/api && ./.venv/Scripts/python -m alembic heads
```

```bash
# Regenerate the committed sample proposal. Opt-in, and the only thing in the
# repo that must talk to the real PVGIS: the sample PDF is the artefact a reader
# is most likely to take at face value, so it has to be a real one. Refuses to
# write unless every probe came from the canonical origin, and writes the frozen
# snapshot beside the PDF so `test_sample_output.py` can check that offline.
cd apps/api && ./.venv/Scripts/python.exe ../../scripts/regenerate_sample_output.py
```

```bash
# End-to-end. No servers to start first — the suite starts its own.
cd apps/web
npx playwright test --grep "@p0"              # the mandatory set (87)
npx playwright test --grep-invert "@live"     # deterministic + degraded, everything
npx playwright test --headed --grep "@p0"     # watch it happen
npx playwright show-report                    # the HTML report from the last run

# Tier C, opt-in. Name only the dependencies you want live; anything unnamed
# stays on fixtures, so a live run is never live in more ways than it says.
# Without E2E_LIVE these skip themselves, with the reason printed.
E2E_LIVE=pvgis,fx,llm npx playwright test --grep "@live"

# Against a stack the suite does not own — the Docker containers, say.
E2E_TARGET_URL=http://127.0.0.1:3000 npx playwright test --grep "@p0"
```

Integration tests run against a **throwaway database** with Maps, FX and the LLM on committed fixtures — the configuration a reviewer gets from a clean clone with no credentials and no model pulled.

**PVGIS and imagery are different, and deliberately so.** Neither has a fixture mode: the application always makes a real HTTP call for both. The suite stays offline by starting one local stub (`tests/support/pvgis_stub.py`) and pointing `PVGIS_BASE_URL` and `GOOGLE_STATIC_MAPS_BASE_URL` at it, so every test exercises the same transport, retry and parse code a production call does.

The stub serves a **synthetic** raster, because Google's imagery is deliberately not committed to this repository. It also writes a roof calibration profile bound to that synthetic raster, which is how the suite satisfies the imagery-verification guard *for real* rather than switching it off. There is deliberately no "skip verification" flag anywhere — that is the flag that eventually ships enabled. One mechanism serves pytest, all three Playwright launchers and both verification scripts, and because the stub keeps a request log, "a consumption change makes zero PVGIS calls" is an assertable count rather than a claim.

---

## The end-to-end suite

### It owns its servers

There is no step where a human has to remember to start something first. Playwright's `webServer` starts **two complete stacks**, each with its own temporary SQLite database created empty at start-up, and stops them at the end:

| Tier | Web | API | Configuration |
|---|---|---|---|
| **A — deterministic** | `:3100` | `:8100` | `FX_MODE=fixture`, `LLM_PROVIDER=rules`, PVGIS and imagery at the replay stub on `:8102` |
| **B — degraded** | `:3101` | `:8101` | FX and Ollama pointed at hosts that cannot resolve; PVGIS at the replay stub, because an unreachable PVGIS now fails the analysis outright and there would be no proposal left to test |
| **C — live** | `:3100` | `:8100` | `@live`; skips itself unless the stack really is live. `E2E_LIVE=pvgis` *omits* the base-URL override rather than flipping a mode |
| **D — pvgis-down** | — | `:8103` | API only, no browser, no Next build. PVGIS pointed at a fault path that always answers 503, for `e2e/pvgis-failure.spec.ts` |

The ports are deliberately clear of 3000/8000 so a developer's running dev server is neither disturbed nor accidentally tested against, and `reuseExistingServer` is `false` everywhere. Each launcher refuses to start if its port is occupied rather than attaching to a stranger's process — a degraded stack silently answered by a healthy one would produce green tests that prove nothing.

Turn tier B off while iterating with `E2E_DEGRADED=0` (it costs a second Next build), and reuse an existing build with `E2E_SKIP_BUILD=1`. Neither is used in a verification run.

### How failure is simulated

**PVGIS and FX are called by the backend, not the browser.** Playwright's route interception cannot reach them, so no amount of browser-level mocking would test those fallbacks. Tier B is a second stack genuinely configured to fail: `FX_BASE_URL=http://fx.invalid/...`, `OLLAMA_BASE_URL=http://ollama.invalid`.

Tier B's PVGIS points at the replay stub rather than at `pvgis.invalid`, which is
a change worth explaining. An unreachable PVGIS no longer degrades — it fails the
analysis outright — so pointing tier B's PVGIS at nothing would leave no proposal
with which to test the FX and LLM fallbacks; the tier would stop covering what it
exists to cover. The genuine-outage case moved to tier D, whose PVGIS answers 503
to everything and where the expected outcome *is* the failure.

Faults there are carried in the **URL path**, never in stub state: `PVGIS_BASE_URL`
is per-process, so fault mode is a property of the stack and parallel workers
cannot poison each other's expectations. There is no arming endpoint, deliberately.

The `.invalid` TLD is reserved and never resolves, so every attempt fails in ~200 ms on every OS. An unbound local port was the first choice and was wrong: Windows drops the SYN rather than refusing it, so each attempt burned the full connect timeout and a single analysis took 87 seconds.

Browser-level interception is used **only** for genuinely browser-originated requests — the satellite raster, and one deliberate 500 on the chat endpoint to prove the error banner appears. Golden-path tests never intercept internal APIs.

A degraded analysis takes roughly 10 seconds: the FX call burns its retry budget before falling back, while PVGIS answers immediately from the stub. It was ~25 seconds when PVGIS also had to time out. The degraded project still carries longer timeouts than the others — longer waits for a slower stack, not looser assertions: every expectation is identical.

### Golden values are independent of the code under test

`e2e/fixtures/expected-values.ts` holds a reviewed literal per system size. The suite never imports a production function to build an expectation, never reads the current analysis response and asserts it equals itself, and never recomputes a figure with the formula the backend uses. Any of those would make the test agree with the implementation *by construction*.

<a id="golden-value-derivation"></a>

#### Golden value derivation

Reproducible with a calculator from the committed captures. `E_y` is `outputs.totals.fixed.E_y` in `fixtures/pvgis/pvcalc*.json` — served by the replay stub, never read off disk by the application; the rate is `fixtures/exchange-rates/usd-eur-ecb.json`.

The goldens are pinned against the **replayed** captures, not against live PVGIS, and that distinction is real: a live run on 2026-07-29 returned 1119.83 for the south facet where the capture says 1119.82. Live tests therefore assert invariants and ranges, and only the replay tier asserts exact kWh.

Re-captured on 2026-08-01: correcting the roof moved three of the four facet aspects, so the previous captures described angles the application no longer requests. `scripts/fetch_pvgis_fixtures.py` re-seeded them, and the three superseded files were deleted — two captures whose aspects round to the same integer would otherwise collide silently in the stub's index.

| Facet | PVGIS aspect | 1 kWp yield |
|---|---:|---:|
| North | −169.47° | 1678.77 kWh |
| West | 98.05° | 1503.89 kWh |
| East | −79.38° | 1367.24 kWh |
| South | 7.59° | 1114.85 kWh |

```
consumption = 1150 × 12                = 13 800 kWh
capex(EUR)  = round(10 000 × 0.87897)  = €8 789.70
covered     = min(production, consumption)
savings     = round(covered × 0.25)    to cents
payback     = capex(EUR) / savings
20-year net = −capex(EUR) + 20 × savings
```

| Size | Requested | Placed | Allocation | Production | Coverage | Savings | Payback | 20-year |
|---|---:|---:|---|---:|---:|---:|---:|---:|
| 3.6 kWp | 9 | 9 | N 6, W 3 | 2.4×1678.77 + 1.2×1503.89 = **5833.72** | 42.27 % | €1458.43 | 6.03 yr | €20 378.90 |
| 6 kWp | 15 | 15 | N 6, W 3, E 4, S 2 | + 1.6×1367.24 + 0.8×1114.85 = **8913.18** | 64.59 % | €2228.30 | 3.94 yr | €35 776.30 |
| 9.6 kWp | 24 | **22** | N 6, S 9, W 3, E 4 | + 2.8×1114.85 = **12 034.76** | 87.21 % | €3008.69 | 2.92 yr | €51 384.10 |

The production column is literally the formula the code evaluates: the four 1 kWp probes give a specific yield per facet, and production is `installed kWp × specific yield`. It used to agree only because linear scaling of a capture reproduced the same arithmetic.

The 6 kWp row is the load-bearing one, and modelling the chimney has made it harder to satisfy by accident. South is now the **largest** facet (31.0 m² to north's 30.0) *and* the one with the **most** free bays (9 to north's 6), so a fill ranked on area or on capacity would use it first. It receives two panels, the leftovers, because at −34° latitude north (1679), west (1504) and east (1367) all out-produce it (1115). East takes four rather than three because its array is turned 45°.

The 9.6 kWp row exercises the capacity warning on the real calibration rather than only under an artificial setback: 24 panels are requested, 22 fit, and every figure to its right is computed from the 8.8 kWp that fits.

Alongside the goldens sit invariants that are safe to compute because each restates a *rule* rather than the calculation: `capacity == panels × 0.4`, `coverage ≤ 100 %`, `savings ≤ consumption × price`, `cashFlow[0] < 0`, `cashFlow[20] == twentyYearNetBenefit`.

#### Tolerance policy

| Class | Rule |
|---|---|
| Panel counts, capacity, FX rate and date, snapshot strings | **Exact** |
| Displayed currency | **Cent-exact** (`expectMoney`) |
| Display-rounded figures | Must round-trip to the golden value at the displayed precision |
| Panel containment | 0.02 source px, because coordinates are published to 2 dp. Measured worst case on the real payload: **0.0037 px = 0.23 mm** |
| Pointer round-trip on the calibration canvas | Exact against the integer mouse position used; ±1.5 px against the point aimed at, which is sub-device-pixel |

Nothing looser. A tolerance that is not derived from a measured cause is a weakened assertion.

### Parallelism and SQLite

One API process serves every worker, so they share one SQLite file. `fullyParallel` is **off**: files run in parallel, tests inside a file run in order in a single worker, which serialises each workflow's own writes. The engine additionally runs SQLite in **WAL mode with a 5-second `busy_timeout`** (`apps/api/app/db/session.py`), so a concurrent writer waits rather than failing instantly with `database is locked`.

Per-worker databases were the first preference and are not practical here: Next bakes the API origin into `routes-manifest.json` at build time, so a per-worker API needs a per-worker Next build. That is four-plus production builds to remove contention WAL already handles. Reliability was the goal, not maximum parallelism.

### Inventory

129 tests across three Playwright projects and 21 spec files, measured 2026-07-31.

| Tag | Count | What runs it |
|---|---:|---|
| `@p0` | 88 | 73 chromium · 13 degraded · 2 mobile-chromium |
| `@p1` | 34 | chromium |
| `@live` | 7 | opt-in; skipped on a fixture stack, with the reason printed |

`customer-proposal.spec.ts` is the newest: the whole journey through the
browser — create a customer, link a project, analyse, finalise, preview,
confirm, open the link as the customer, then edit and prove the issued
revision is untouched. It also asserts the customer's address appears nowhere
in the served JSON or the rendered page, and that three page loads count as
one view. It runs against the console provider, and the settings *refuse* to
construct with `EMAIL_MODE=smtp` in a test environment, so it cannot send real
mail even if misconfigured.

`npx playwright test --grep-invert "@live"` → **129 passed**, 2.4 min, from a clean `.next` build. (124 before this increment.) (2026-07-30; 117 before mandatory live PVGIS added `pvgis-failure.spec.ts` and `analysis-failure-ui.spec.ts` and removed the fixture-fallback spec. Faster than the 4.1 min recorded on 2026-07-29 because the probe refactor makes four PVGIS calls per analysis where the old path made seven.)

`E2E_LIVE=pvgis,fx,llm npx playwright test --grep "@live"` → **6 passed, 1 skipped**, 38.9 s, against real PVGIS, the real ECB feed and a locally pulled `qwen3.5:2b`. The skip is live Google Static Maps, which has no API key. That run is what caught the `think: false` defect — see [`local-ai.md`](local-ai.md).

`E2E_LIVE=llm npx playwright test --grep "@live"` → **4 passed, 3 skipped**, 1.2 min, on 2026-07-29 (PVGIS and FX left on fixtures, so their live specs skip with a stated reason). Two probe phrases were retired from that run rather than weakened: `conversation/numbers.py` now parses *"eleven hundred and fifty units a month"* and *"the one that fits fifteen panels"* deterministically, so they never reach the model. That is a win in the parser; keeping them would have recorded it as a regression here. **Live results are recorded, never gated** — the deterministic flow completes entirely on `LLM_PROVIDER=rules`.

### Architecture

```
apps/web/e2e/
├── fixtures/       environment probe, golden values, API client, test fixtures
├── pages/          solar-flow, roof-view, proposal, calibration page objects
├── helpers/        polygon geometry, PDF extraction, assertions, console capture
├── scripts/        the two stack launchers (port probe, temp DB, health wait)
├── degraded/       tier B specs
└── live/           tier C specs
```

Every wait is on an observable application state — a message rendered, a busy overlay gone, an element carrying a real value. There is not one `waitForTimeout` in the suite: a fixed sleep is a guess that either wastes time or flakes, and on a slower machine it does both.

### Notable specs

| Spec | What it actually proves |
|---|---|
| `panel-placement.spec.ts` | Containment and non-overlap computed with independent ray-casting and SAT on the polygons the backend published — not screenshot heuristics, and not the production containment routine agreeing with itself. Determinism = two independent projects, full serialised geometry compared. |
| `calibration.spec.ts` | The **410 px regression**. Hovering a known source-map pixel must read back that same pixel; resizing the window must not move a single committed coordinate. Dimensions, aspect ratios and azimuths are all *relative*, so a constant translation leaves them looking correct — which is why only a coordinate-space assertion catches it. |
| `proposal-pdf.spec.ts` | Text extracted from the real bytes with `pdfjs-dist`, then asserted: location as entered and as analysed, panel count, annual production, FX rate/date/provider, CAPEX in both currencies, annual saving, payback, 20-year result, and four cash-flow rows. Also that the naive `5.573 m` hip length appears nowhere. |
| `proposal-immutability.spec.ts` | Page, PDF and API all read one snapshot; running further analyses afterwards moves none of them. |
| `accessibility.spec.ts` | axe over three screens, plus keyboard-only completion, heading order, and that provenance is never carried by colour alone. |
| `degraded/fallbacks.spec.ts` | A complete proposal with every external dependency unreachable, each degraded number labelled, and parity never substituted for a failed rate lookup. |

### One thing that had to be measured, not assumed

The axe scans wait for `document.getAnimations()` to settle. Chat bubbles fade in over 180 ms, and a partly transparent bubble composites to `#78797a` on white — 4.21:1, which axe correctly reports. It is a real measurement of a state that lasts a sixth of a second and is exempt from the contrast rule anyway, and the result depended on machine load. That is the worst kind of test: right sometimes, for no visible reason. The scan measures the settled page instead. The animation already honours `prefers-reduced-motion`.

### Accessibility: what axe does and does not prove

`@axe-core/playwright` is an **automated safety net**. It reliably catches contrast, naming, landmark and ARIA-shape problems, and it catches roughly a third of WCAG failures overall. **A clean axe run is not a claim of WCAG compliance and none is made here.** The manual assertions cover three things axe cannot see: that the intake is completable from the keyboard alone, that heading levels never skip, and that every provenance badge states its meaning in words.

Both `@axe-core/playwright` and `pdfjs-dist` are **devDependencies**, injected by the test runner. Neither is imported by application code. See "Dependency containment" below for the executed proof.

---

## The layers

### API — unit

| Suite | What it pins |
|---|---|
| `test_geometry.py` | Web Mercator scale, the north/south sign convention, winding invariance, cardinal azimuths, PVGIS aspect conversion, surface-frame round-trips, and the **hip-edge guard** |
| `test_roof_service.py` | The committed calibration itself — topology, that the facets tile the footprint exactly, the asymmetry the operator correction introduced, A-GEO-1 on real data |
| `test_obstructions.py` | The chimney: loaded onto the right facet, refused when declared on the wrong one, absent-key compatibility, and that **no placed panel stands on it** at any system size. The exclusion is proved by rebuilding the roof without the obstruction and watching capacity return from 21 to 24, and the post-condition is proved by moving a panel onto the chimney by hand and asserting it is rejected by name. Array rotation is pinned off here, so these tests measure the obstruction and nothing else |
| `test_panel_rotation.py` | The per-facet array-angle search: that it **never places fewer panels than lying parallel to the eave**, that only the east triangle gains on this roof, that a turned panel is still exactly 2 × 1 m *on the surface* while becoming a 95.6°/84.4° parallelogram in plan, and that every placement post-condition still holds |
| `test_layout.py` | Panel physical size, full-footprint containment, overlap, gaps, both orientations, production-first allocation, determinism, honest capacity limits |
| `test_pvgis.py` | Request parameters, response parsing, monthly/annual consistency, retries, 429/529/5xx, cache, fixture fallback |
| `test_exchange_rates.py` | Endpoint and ECB provider, every rejection case, the fallback chain, and that **parity is unreachable** |
| `test_financial.py` | The case scenario end to end, the coverage cap, Decimal handling, degenerate inputs |
| `test_rules_parser.py` | Every phrasing the brief demonstrates, step-awareness, refusal of unsupported sizes, and that **an energy unit decides which number is the consumption**. All 80 cases pass unchanged over the rewritten router — the compatibility seam exists so this suite keeps testing the real classifier rather than a reimplementation of it |
| `test_chat.py` | Rules-first ordering, model fallback, and that a model cannot supply a value the rules would refuse |
| `test_ollama.py` | Schema-constrained requests, invalid JSON, timeouts, unavailable model |
| `test_conversation_normalise.py` | Contraction expansion, punctuation folding, and that `raw` survives every transform verbatim |
| `test_conversation_numbers.py` | The number-word state machine, the colloquial-pair rule (`eleven fifty` = 1150 but `twenty four` = 24), the four vagueness gates, and that `fifteen panels` is never 15 kWh |
| `test_conversation_questions.py` | Q1–Q4 detection and topic classification, with **each of the five classification defects as a named regression** |
| `test_conversation_extractors.py` | The tri-state matrix per step, the 10 m case-location tolerance, and that a bare adjective or a time word is not a size selection |
| `test_conversation_router.py` | The priority order, and that a question carries no value and wants no mutation at any of the nine steps |
| `test_conversation_knowledge.py` | That **no help entry contains a hardcoded engineering number** — every figure is a placeholder resolved from `Settings`, checked by rejecting any digit in a body and by re-checking every number in a rendered entry |
| `test_conversation_answers.py` | The source hierarchy, the six answer states, and the five paraphrase gates — including that the model is never sent the snapshot |
| `test_corrections.py` | The six review corrections, permanently. The dependency map is **derived by differential experiment** and asserted for both safety and tightness |
| `test_summary.py` | That generated prose containing an invented, recalculated or altered number is **discarded** |
| `test_config.py` | That `.env.example` actually loads, and that no exchange-rate setting exists |
| `test_schema_parity.py` | Alembic migrations and ORM metadata describe an identical schema |

### API — integration

`test_workflow_api.py` drives the chat flow over HTTP. `test_proposal_api.py` covers finalisation, the public share route, the PDF, view tracking, **immutability**, and that finalising twice returns the same proposal.

Three suites cover the conversational layer end to end:

| Suite | What it pins |
|---|---|
| `test_chat_questions_api.py` | A question at every step: the step does not move, no column is written, and the stored analysis is **byte-identical** afterwards — `json.dumps(..., sort_keys=True)`, not merely equal-looking, because a recomputed snapshot with the same inputs would still be a recomputation that should not have happened |
| `test_chat_change_and_reset_api.py` | A correction recomputes only its dependents, with the untouched sections compared byte for byte; a failed recompute leaves the project `stale` and unfinalisable; a stale project withholds the affected figures but still answers about the roof; reset asks first and honours the confirmation only when it answers the immediately preceding message |
| `test_chat_telemetry_api.py` | That `rules_sufficient` and a genuine failure are distinguishable, that every `interpretation` key is present, and that a model-supplied value still faces the state machine |
| `test_corrections_api.py` | The revision fork: the parent's proposal and link are untouched, the revision finalises to a new token, a repeated change reuses the one child |

Five more cover customers and delivery. The theme running through them is that
most of the assertions are about **what did not happen**:

| Suite | What it pins |
|---|---|
| `test_customers_api.py` | Case-insensitive uniqueness; a duplicate names the record it collided with; a `%` in a search is matched literally rather than matching every customer; a partial update touches only the keys that were sent; archiving hides without deleting |
| `test_customer_projects.py` | `POST /projects` with no body still works; a revision carries the customer forward; changing the customer after finalisation forks, and the fork is immediately finalisable because nothing needs recomputing |
| `test_proposal_customer_snapshot.py` | The snapshot is frozen — editing or archiving the customer does not restate an issued proposal; the address appears nowhere in the public payload or the PDF, asserted against the whole serialised body rather than named keys; the *internal* snapshot does keep it, so a "fix" that stopped storing it would fail |
| `test_proposal_send.py` | The preview sends nothing and creates no delivery row; an unconfirmed request sends nothing; a duplicate is refused; a deliberate resend is allowed; an unavailable provider refuses **without creating a row**; the send route is 404 on a share token; two concurrent claims resolve one-granted-one-refused; a customer display name is escaped in the HTML body |
| `test_chat_send_api.py` | A question about sending is answered rather than acted on; a bare "yes" with no offer sends nothing; a "yes" separated from the offer by any other turn sends nothing; a message naming a different address cannot redirect the recipient — and `ExtractedValues` has no field an address could go in, asserted on the type |

`test_view_tracking.py` covers the bonus: first/last/count derived rather than
stored, refreshes deduplicated, link unfurlers and crawlers not counted, the IP
hash salted, and the open-notification firing once per counted view. One test
asserts an **ordinary browser is counted** — without it, a deny-list that
matched everything would pass every other test in the file.

### Web — unit

`format.test.ts` (money as strings, provenance labels), `components.test.tsx` (chat, progress rail, accessible badges), `calibration.test.ts` (measurement, validation, JSON round-trip), `telemetry.test.ts` and `message-telemetry.test.tsx` (that the fallback chip appears **only** when a model was attempted and failed, and that provider detail stays behind a disclosure).

`send-proposal.test.tsx` covers the two ways the send panel could mislead its
operator: showing success before the provider has accepted (asserted by holding
the promise open and checking the success state is absent), and reporting
console mode as though a message had left the building. The second is the
likeliest honesty failure in the feature, because every development run
exercises it.

---

## Tests that exist because something actually broke

Each of these was written after a real defect, not in anticipation of one.

| Test | The defect |
|---|---|
| `test_hip_length_must_not_be_projected_over_cos_pitch` | `projected / cos(pitch)` applies only along maximum slope. On this roof it overstates every hip by 4.8 %. |
| `test_env_example_loads_without_error` | `SMTP_PORT=` (blank) failed integer validation, so copying `.env.example` to `.env` — line one of the quick start — broke the app **and** `docker compose up`. |
| `test_a_database_built_by_the_app_is_stamped_at_head` | `create_all` left `alembic_version` empty, so `alembic upgrade head` tried to re-create existing tables and failed in the container. |
| `test_cumulative_flow_is_internally_consistent` | Rounding each year independently made consecutive rows differ by €2,530.57 and €2,530.58 — every figure correctly rounded, the printed table not reconcilable. |
| `test_source_repository_contains_no_parity_literal` | Greps for `rate = 1.0` and friends, so a well-meaning fallback cannot creep back in. |
| `test_medium_system_prefers_small_high_yield_facets_over_a_large_poor_one` | An area-ranking allocator would fill the south trapezoid. At −34° the correct answer is the two small triangles. |
| `test_a_moved_market_rate_does_not_change_a_finalised_proposal` | Forces a new rate into the cache and asserts a finalised proposal is untouched. |
| `test_the_unit_decides_which_number_is_the_consumption` | "I pay 0.30 per kWh and use 1150 kWh" parsed as 0.3 kWh a month, which produced a 2,930-year payback. Found by the E2E prompt-injection spec. |
| `test_finalising_twice_returns_the_same_proposal` | A double-click issued two share links to two documents, splitting the view counts and creating two snapshots that could later disagree. Found by the E2E contract spec. |
| `test_request_is_schema_constrained_and_deterministic` (the `think` assertion) | Ollama puts a *reasoning* model's whole output in `thinking` and leaves `response` empty. The client read `response`, found it empty, and fell back to the rules parser — silently, every time, so the LLM layer contributed nothing while appearing to work. Only a live model could show it: a mock returns whatever the test author puts in `response`. |
| `concurrency.spec.ts` (intermittently) | FastAPI runs a `yield` dependency's exit code **after the response is sent**, so the session commit landed after the client already had its 200. A caller that immediately issued a dependent request could read a database without its own write — surfacing as an intermittent 409 from `run-analysis` for intake that had just been accepted. Mutating handlers now commit before returning. |
| `responsive.spec.ts` (both cases) | The Konva stage renders at a default 720 px until its `ResizeObserver` fires, which stretched the grid track and pushed a phone-width page 320 px sideways — permanently, because the observer then measured the widened container. |
| `test_only_the_calibrated_property_is_accepted` | A 200 m acceptance radius spans several plots at this latitude, so a neighbour's roof would have passed as "the calibrated property". 10 m is consumer-GPS error and still covers every truncation of the coordinate in the repository. |
| `test_the_declared_consumption_dependencies_are_the_real_ones` | "Only the section called `financial` depends on consumption" is plausible and was never checked. The test derives the set by experiment and asserts the declared map matches — for **safety** (nothing outside it moves) and **tightness** (everything in it moves for some pair), because either alone is satisfiable by a wrong map. |
| `test_last_is_a_time_word_unless_it_names_a_choice` | Bare `last` was in the size vocabulary, so "about the same as we used last winter" selected 9.6 kWp — the same shape as the `large` defect, found while writing the live probes. |
| `test_a_capitalised_question_still_reaches_the_right_help_entry` | The help registry was searched with the message exactly as typed, and its triggers are lowercase, so every capitalised question fell through to the topic default. "Why does a 6 kWp system have 15 panels?" was answered with the list of sizes. Found by live probing, not by any mocked test. |
| `llm-telemetry.spec.ts` + the `run-analysis` commit | Flushing the "running" status marker instead of committing held SQLite's write lock across three PVGIS calls and an FX lookup. Concurrent writers queued behind it and one past `busy_timeout` failed with "database is locked", surfacing as a 500 on an unrelated request. Exposed by adding a second E2E file to the degraded project — the first time two tests genuinely ran concurrently against that stack. |

---

## What only the container can prove

The local suite runs against the developer machine's Python, and its bundled
SQLite is **newer than the container's**: 3.45.3 locally against 3.40.1 in the
Debian-based image. That gap is not cosmetic, and it hid a real defect.

`ALTER TABLE ... DROP COLUMN` exists from SQLite 3.35, but whether it can drop a
column named in a foreign-key definition depends on the version. 3.45 rewrites
the clause and succeeds. 3.40.1 refuses:

```
error in table projects after drop column:
unknown column "customer_id" in foreign key definition
```

So the downgrade of `b2c3d4e5f6a7` — which reverses an `ADD COLUMN … REFERENCES`
— passed every local test and would have stranded a *deployed* database at that
revision with no way back. It is now a `batch_alter_table` rebuild, which works
on both, and `test_this_chain_upgrades_and_downgrades_without_losing_a_proposal`
proves the rebuild does not cascade `proposals` away.

There is no local test that can catch this class of bug, because the local
engine does not have the limitation. The honest answer is that **migrations are
verified in the container**, not only in the suite:

```bash
docker compose up -d --build
docker compose exec api sh -c "cd /app && python -m alembic downgrade 9b2c4d6e8f10"
# seed rows, then:
docker compose restart api      # init_db must upgrade in place and keep them
```

Two other things were exercised in the container and nowhere else, because the
settings refuse `EMAIL_MODE=smtp` in a test environment and no automated test
can therefore reach them:

- A **real SMTP send** through the bundled Mailpit profile — correct `From`,
  `To` and subject (carrying the proposal reference), the share link present in
  both the text and HTML parts, and the recipient's own address absent from the
  body.
- The **open notification**, which fired once on a counted view and says *"This
  is a page view, not an email open"*.

---

## What is deliberately *not* asserted

### Exact live PVGIS numbers
Live-marked tests assert HTTP 200, a named radiation database, twelve monthly values, annual ≈ Σ monthly, a plausible 1,300–1,900 kWh/kWp band at this site, southern-hemisphere seasonality, and the **ordering invariant that north out-produces south**. Never exact kWh.

### Exact live FX rates
The rate moves daily — it changed from `0.87897` to `0.87804` between two runs during this build. `@live` asserts a 0.5–1.5 band, an ISO date not in the future, and that the conversion was actually applied. Fixture mode carries the exact assertions.

### Byte-identical PDF and web output
The requirement is **numerically identical values from the same immutable snapshot**. Rendered bytes and locale formatting legitimately differ; the tests extract the PDF's text and compare the underlying values.

### Energy sums to the cent
Facet production and the total are each rounded for display, so summing the parts can differ from the rounded whole by hundredths of a kWh. Money is canonicalised once because a cash-flow table must reconcile; energy is not, because 0.01 kWh on ~9,500 sits far below PVGIS's own uncertainty and forcing agreement would manufacture precision.

---

## Not covered

| Gap | Why |
|---|---|
| Live Google Static Maps | No API key. The path is unit-tested with a mocked transport; it has never received a real Google response. The `@live` imagery spec skips itself with that reason. |
| Ollama extraction *quality* | The model itself is verified — `qwen3.5:2b` was pulled and the `@live` tier passed against it on 2026-07-28. What is *not* asserted is that it extracts well: it refused every conversational phrasing tried. That is a property of whichever model is pulled, so it is recorded in [`local-ai.md`](local-ai.md) rather than gated on. The specs **never pull a model** — installation is a separate, explicit step (`scripts/pull-model.ps1` / `.sh`). |
| SMTP notifications | `EMAIL_MODE=console` is exercised; the SMTP branch is not. |
| Load and performance | The concurrency specs prove correctness under simultaneous use, not throughput. PDF rendering in-request remains a known scaling limit. |
| Visual regression | No screenshot diffing. The calibration overlay is checked by asserting the coordinate transform instead, which is what the 410 px defect actually was. |
| Broad browser matrix | Chromium desktop plus a Pixel 7 smoke pass. The brief warns against multiplying a heavy suite across browsers; what differs on a phone is layout, and that is what the mobile project checks. |

---

## Dependency containment

Both test-only packages must stay out of what ships. The proof is run against the **production client bundle**, not the API image:

```bash
cd apps/web && npm run build
grep -rlE "axe-core|pdfjs|pdf-parse" .next/static   # expect: no matches
grep -rlE "axe-core|pdfjs-dist|pdf-parse" .next/server
node -e "const p=require('./package.json'); console.log(Boolean(p.dependencies['pdfjs-dist']), Boolean(p.dependencies['@axe-core/playwright']))"
```

Neither package belongs to the API at all, so searching the API image is a secondary check only.

---

## CI

`.github/workflows/ci.yml` is committed and **has never executed** — there is no git remote. Its commands are verified locally. No green badge is claimed.
