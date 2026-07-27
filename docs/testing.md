# Testing

What is tested, what deliberately is not, and how to run each layer.

The guiding rule: **exact numeric assertions belong to fixtures; live tests assert invariants and ranges.** PVGIS revises its radiation datasets and the ECB rate moves daily — pinning either would make the suite fail for reasons unrelated to this code.

---

## Running it

```bash
# API — offline, deterministic, no credentials
cd apps/api && ./.venv/Scripts/python -m pytest -q -m "not live"

# API — the live-marked set (hits PVGIS and Frankfurter for real)
cd apps/api && ./.venv/Scripts/python -m pytest -q -m live

# Web
cd apps/web && npm run typecheck && npm run test && npm run build

# End-to-end (needs both servers running)
cd apps/web && npm run test:e2e

# Static analysis
cd apps/api && ./.venv/Scripts/python -m ruff check app tests
cd apps/api && ./.venv/Scripts/python -m mypy app
```

Integration tests run against a **throwaway database in fixture mode** — the exact configuration a reviewer gets from a clean clone with no credentials and no model pulled.

---

## The layers

### API — unit

| Suite | What it pins |
|---|---|
| `test_geometry.py` | Web Mercator scale, the north/south sign convention, winding invariance, cardinal azimuths, PVGIS aspect conversion, surface-frame round-trips, and the **hip-edge guard** |
| `test_roof_service.py` | The committed calibration itself — topology, areas, A-GEO-1 on real data |
| `test_layout.py` | Panel physical size, full-footprint containment, overlap, gaps, both orientations, production-first allocation, determinism, honest capacity limits |
| `test_pvgis.py` | Request parameters, response parsing, monthly/annual consistency, retries, 429/529/5xx, cache, fixture fallback |
| `test_exchange_rates.py` | Endpoint and ECB provider, every rejection case, the fallback chain, and that **parity is unreachable** |
| `test_financial.py` | The case scenario end to end, the coverage cap, Decimal handling, degenerate inputs |
| `test_rules_parser.py` | Every phrasing the brief demonstrates, step-awareness, refusal of unsupported sizes |
| `test_chat.py` | Rules-first ordering, model fallback, and that a model cannot supply a value the rules would refuse |
| `test_ollama.py` | Schema-constrained requests, invalid JSON, timeouts, unavailable model |
| `test_summary.py` | That generated prose containing an invented, recalculated or altered number is **discarded** |
| `test_config.py` | That `.env.example` actually loads, and that no exchange-rate setting exists |
| `test_schema_parity.py` | Alembic migrations and ORM metadata describe an identical schema |

### API — integration

`test_workflow_api.py` drives the chat flow over HTTP. `test_proposal_api.py` covers finalisation, the public share route, the PDF, view tracking and **immutability**.

### Web

`format.test.ts` (money as strings, provenance labels), `components.test.tsx` (chat, progress rail, accessible badges), `calibration.test.ts` (measurement, validation, JSON round-trip).

### End-to-end

Playwright drives the real UI against the real API in fixture mode: the acceptance flow, all three system sizes, refusal of an unsupported size, fixture labelling, layer toggles, and an unknown share token.

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

---

## What is deliberately *not* asserted

### Exact live PVGIS numbers
Live-marked tests assert HTTP 200, a named radiation database, twelve monthly values, annual ≈ Σ monthly, a plausible 900–2,000 kWh/kWp band, and the **ordering invariant that north out-produces south at this site**. Never exact kWh.

### Exact live FX rates
The rate moves daily — it changed from `0.87897` to `0.87804` between two runs during this build. Fixture mode carries the exact assertions.

### Byte-identical PDF and web output
The requirement is **numerically identical values from the same immutable snapshot**. Rendered bytes and locale formatting legitimately differ; a test compares the underlying values instead.

### Energy sums to the cent
Facet production and the total are each rounded for display, so summing the parts can differ from the rounded whole by hundredths of a kWh. Money is canonicalised once because a cash-flow table must reconcile; energy is not, because 0.01 kWh on ~9,500 sits far below PVGIS's own uncertainty and forcing agreement would manufacture precision.

---

## Not covered

| Gap | Why |
|---|---|
| Live Google Static Maps | No API key. The path is unit-tested with a mocked transport; it has never received a real Google response. |
| A real Ollama model | The adapter is fully tested against a mocked transport. Whether `qwen3.5:2b` extracts *well* is unmeasured — only that whatever it returns is validated. |
| SMTP notifications | `EMAIL_MODE=console` is exercised; the SMTP branch is not. |
| Load and concurrency | No performance testing. PDF rendering in-request is a known scaling limit. |
| Visual regression | The calibration overlay is checked by eye against a generated debug image. That is how the 410 px offset was caught — and a unit test would not have caught it. |

---

## CI

`.github/workflows/ci.yml` is committed and **has never executed** — there is no git remote. Its commands are verified locally. No green badge is claimed.
