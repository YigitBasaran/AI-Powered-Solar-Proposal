# Architecture

```
apps/web   Next.js 15 · React 19 · TypeScript strict · Tailwind 4 · React Konva · Recharts
apps/api   FastAPI · Pydantic 2 · SQLAlchemy 2 · SQLite · Alembic · Shapely · Jinja2 · Playwright
fixtures/  Committed satellite, PVGIS and FX captures — the app runs with no credentials
docs/      Verification, geometry, assumptions, API, testing, case answers, live build log
scripts/   Calibration derivation, fixture capture, setup, packaging, verification
```

---

## The shape of it

```
browser
  │  everything, including imagery
  ▼
Next.js  ── rewrite /api/v1/* ──►  FastAPI
                                     ├── Google Static Maps      (live mode)
                                     ├── PVGIS 5.3               (production)
                                     ├── Frankfurter / ECB       (FX)
                                     ├── SMTP relay              (optional; console by default)
                                     └── Ollama                  (optional, opt-in)
```

Nine tables, and the relationships that matter:

```
Customer 1 ──< Project 0..1 ──< Proposal 0..1 ──< ProposalDelivery 0..n
                 │  └── revision_of_project_id (UNIQUE self-FK: a chain, not a tree)
                 │
                 └──< ActivityEvent            Proposal ──< ProposalView 0..n
```

Three lifecycles are kept deliberately separate, none of them stored in another's column:

| Lifecycle | Where | Values |
|---|---|---|
| Analysis | `projects.analysis_status` | `pending · running · complete · recalculating · stale · failed` |
| Proposal | derived from the project chain | none / finalised / superseded |
| Delivery | `proposal_deliveries.status` | `pending · sending · sent · failed` |

`viewed` is not a status anywhere — it is `first_viewed_at`/`last_viewed_at` derived from `proposal_views`, so it can never overwrite the fact that a proposal was sent.

**The browser never talks to a third party.** That keeps the Google API key server-side, and — just as importantly — keeps the satellite raster **same-origin**, which is the only reason `stage.toDataURL()` works. A direct fetch from the browser would taint the canvas and the layout could never be exported for the PDF.

---

## Layers

| Layer | Rule |
|---|---|
| `api/v1/` | Translates HTTP to domain calls. **No business logic.** |
| `services/` | Orchestration and workflow. Owns transitions and sequencing. |
| `domain/` | Pure functions and data shapes. No I/O, no clock, no network. |
| `integrations/` | Everything that can fail: HTTP clients, caches, fallbacks. |
| `models/`, `db/` | Persistence. |

The direction of dependency is inward. `domain/geometry.py` imports nothing from `services/` or `integrations/`, which is what makes the measurement layer cheap to test exhaustively and safe to trust.

---

## Four invariants

### 1. Source-map pixels are the only authoritative space

Calibration, edges, areas and panel geometry are all defined on the canonical raster from a *verified* centre/zoom/size/scale. Screen coordinates are a render-time transform and are **never stored**.

Metres-per-pixel comes from Web Mercator and that configuration — never from an image's dimensions. See [`geometry.md`](geometry.md).

### 2. The geometry engine is pure

`domain/geometry.py` is a function of its arguments. That is why it carries 63 tests covering scale, sign conventions, winding invariance, cardinal azimuths and surface round-trips — none of which need a fixture, a database or a network.

### 3. Placement happens in surface coordinates

A panel is 1 × 2 m on the sloped plane and nowhere else. Shapely does containment and overlap there; only the *render* is foreshortened. See [`panel-placement.md`](panel-placement.md).

### 4. Proposals are immutable snapshots

`finalize` writes one JSON blob containing every derived number plus the exchange rate, its date and its source. The share page and the PDF renderer read **that blob and nothing else**.

Neither recomputes anything, so they cannot disagree, and a market move cannot rewrite a document already sent. Test-enforced.

**The customer is part of that snapshot.** `customer_snapshot_json` freezes who the proposal was addressed to, for the same reason the figures are frozen: a corrected surname or a new address six months later must not restate a document that has already been sent.

**A revision is a forked *project*, not a version row.** `projects.revision_of_project_id` is a UNIQUE self-reference, so revisions form a chain and the database — not application timing — refuses a second fork. Everything else is derived from that one mechanism: `revision_number` is the chain depth (computed at finalisation and then frozen, because it is printed in the email), and `isSuperseded` is computed at read time from whether a later proposal exists. Nothing is ever written back onto an issued proposal, which is what makes "supersede rather than edit" structural rather than a convention.

---

### 5. Sending is a separate act from issuing

A proposal is a document; a delivery is an attempt to put it in front of someone. They are separate tables and separate lifecycles, so a failed send leaves a perfectly valid proposal with a working public link — which is exactly the fallback the UI offers.

There is no job queue, so the provider call happens inside the request. What makes that safe is a database claim borrowed wholesale from `services/analysis_claim.py`, because the shape of the problem is identical: insert keyed on a deterministic idempotency key and let the UNIQUE index settle the race, take the row with a conditional `UPDATE … WHERE status IN (…)`, **commit before the slow part**, then write the terminal status fenced on still owning it.

Committing first is the load-bearing step: an ambiguous timeout leaves a durable `sending` row rather than nothing at all, so the evidence that an attempt happened survives the process that made it. What it buys is at-least-once with a stable key — stated plainly in [`known-limitations.md`](known-limitations.md#delivery-is-at-least-once-not-exactly-once) rather than dressed up as exactly-once.

---

## Ports where an integration would otherwise leak

### `FacetYieldRankingProvider`

The panel optimiser needs a specific yield per facet to rank roof faces. That is PVGIS data — but making the optimiser depend on the PVGIS client would make it untestable offline and force the integration to exist before the algorithm that consumes it.

```python
class FacetYieldRankingProvider(Protocol):
    async def specific_yield_kwh_per_kwp(self, facet: RoofFacet) -> float: ...
```

A fixture implementation read captured probes; the live implementation arrived
later and changed no optimiser code or test.

**The port has since been retired, and it is worth recording why rather than
letting it vanish from the diagram.** It existed so the optimiser could be built
and tested before the integration behind it. That purpose is spent: every facet
is now probed at 1 kWp *before* layout begins, because production is
`installed kWp × specific yield` and the allocator needs all four yields to rank
the facets against each other. So `generate_layout` takes a plain
`Mapping[str, float]` of yields, is synchronous, and **raises** on a facet with
no yield — where the port's fixture implementation would have quietly scored it
zero and moved the panels somewhere else. The synthetic estimator that
interpolated yield between captured aspects is gone from the application
entirely; what remains of it lives in `tests/support/yields.py`, where it is
unambiguously test scaffolding.

### `ExchangeRateCache`

In-memory for tests, SQLAlchemy-backed in the app. The FX service does not know which it has.

---

## Request flow

```
POST /projects                    create, greet
POST /projects/{id}/chat          router → answer service or state machine → persist
POST /projects/{id}/run-analysis  roof → layout → PVGIS → FX → financials
POST /projects/{id}/finalize      validate → summarise → snapshot → share token
GET  /proposals/{token}           read-only projection of the snapshot
GET  /proposals/{token}/pdf       snapshot → Jinja2 → Chromium → A4
```

Analysis order is not arbitrary: PVGIS is called with the capacity that **actually fits**, and the financial model is driven by the production that capacity actually yields. Where the requested system does not fit, the *feasible* capacity flows onward and nothing downstream sees the requested figure as though it were installed.

---

## The language model's place

```
user message
   ├─ deterministic router (question · extractor · confirm) ──► ConversationAction
   └─ (only if that fails) Ollama ──► JSON schema ──► Pydantic ──► domain whitelist
```

The model may **parse intent** and **write prose**. It may not produce a number that reaches the domain:

- `LlmAction` has no field for money, production, geometry or exchange rates, and none for the next workflow step — there is no channel to express one.
- Model-supplied values are re-checked against the same whitelist the rules parser uses.
- The router never mutates; the state machine never composes an answer; the route may write only the columns in its `ASSIGNABLE` whitelist. See [`conversation.md`](conversation.md).
- The executive summary is validated: every number in the generated prose must be one the backend computed, or the summary is discarded.

`LLM_PROVIDER=rules` is a complete implementation, not a degraded one. See [`local-ai.md`](local-ai.md).

---

## Operating modes

Every mode is surfaced in the UI, the snapshot and the PDF. **Fixture data is never presented as live.**

| Setting | Default | Fallback |
|---|---|---|
| `FX_MODE` | `live` | → cache → labelled fixture, **never parity** |
| `LLM_PROVIDER` | `rules` | → rules |

---

## Deliberately absent

The brief warns against unnecessary distributed infrastructure, and the workload does not justify it:

**No** Kafka, RabbitMQ, Redis, Celery, Kubernetes or microservices.

Instead: async HTTPX, bounded PVGIS concurrency, a cache keyed on every parameter that changes the answer, memoised layout candidates, and one SQLite file.

The honest limit: PDF rendering spawns Chromium in-request, which is fine at this scale and would need a worker queue under real load. Recorded in [`known-limitations.md`](known-limitations.md).

The same applies to outbound email: `smtplib` is blocking, so the send runs in a worker thread with an explicit timeout rather than on a queue. Adding one for a single outbound call would have been a larger change than the feature.

**No email vendor.** `EMAIL_MODE` is `console` or `smtp`, both stdlib, behind a `ProposalEmailSender` protocol. A Resend or Postmark integration would give real message ids and delivery webhooks; it would also add a vendor, a secret, and a webhook ingestion surface larger than the feature itself. The protocol is where that swap would go.

---

## Related

- [`geometry.md`](geometry.md) · [`panel-placement.md`](panel-placement.md) · [`exchange-rates.md`](exchange-rates.md)
- [`api.md`](api.md) · [`testing.md`](testing.md) · [`assumptions.md`](assumptions.md) · [`known-limitations.md`](known-limitations.md)
