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
                                     └── Ollama                  (optional, opt-in)
```

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
| `MAPS_MODE` | `fixture` | — |
| `FX_MODE` | `live` | → cache → labelled fixture, **never parity** |
| `LLM_PROVIDER` | `rules` | → rules |

---

## Deliberately absent

The brief warns against unnecessary distributed infrastructure, and the workload does not justify it:

**No** Kafka, RabbitMQ, Redis, Celery, Kubernetes or microservices.

Instead: async HTTPX, bounded PVGIS concurrency, a cache keyed on every parameter that changes the answer, memoised layout candidates, and one SQLite file.

The honest limit: PDF rendering spawns Chromium in-request, which is fine at this scale and would need a worker queue under real load. Recorded in [`known-limitations.md`](known-limitations.md).

---

## Related

- [`geometry.md`](geometry.md) · [`panel-placement.md`](panel-placement.md) · [`exchange-rates.md`](exchange-rates.md)
- [`api.md`](api.md) · [`testing.md`](testing.md) · [`assumptions.md`](assumptions.md) · [`known-limitations.md`](known-limitations.md)
