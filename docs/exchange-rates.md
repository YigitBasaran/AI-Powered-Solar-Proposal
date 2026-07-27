# Exchange Rates

Why the conversion exists, where the rate comes from, and why parity is unreachable rather than merely discouraged.

Implementation: [`apps/api/app/integrations/exchange_rates.py`](../apps/api/app/integrations/exchange_rates.py)

---

## 1. Why convert at all

The case gives two figures in **different currencies**:

| Input | Currency |
|---|---|
| Capital cost | **$10,000 USD** |
| Electricity price | **€0.25 EUR** per kWh |

Payback is `CAPEX ÷ annual savings`. The savings accrue in euro. Dividing a dollar figure by a euro figure is a category error — the result is not a number of years, it is nothing at all.

### What assuming parity would cost

At the current rate (`1 USD = 0.87897 EUR`):

| | Payback |
|---|---|
| Correct: €8,789.70 ÷ €2,375.55 | **3.70 years** |
| Parity: $10,000 treated as €10,000 | 4.21 years |

A **13.8 % overstatement** — and the number still looks completely reasonable. Nobody reading the proposal would suspect it. That is exactly why this is enforced structurally rather than left to care.

---

## 2. Provider

```
GET https://api.frankfurter.dev/v2/rate/USD/EUR?providers=ECB
```

```json
{ "date": "2026-07-24", "base": "USD", "quote": "EUR", "rate": 0.87897 }
```

**Frankfurter** is an open API with no key, which matters for a submission that must run without credentials. **ECB is requested explicitly** via `providers=ECB` rather than accepting whatever default the service offers: the European Central Bank publishes an official daily reference rate, so the proposal can name its source precisely instead of citing "an exchange rate API".

The rate reads **`1 base = rate quote`**, so conversion is a multiplication:

```
capex_eur = capex_usd × rate
```

A test asserts the direction is not inverted — at a rate below 1, the euro figure must be *smaller* than the dollar figure. Inverting would be a 30 % error in the opposite direction and, again, would look plausible.

---

## 3. Validation

Every field is checked before a rate is allowed into the domain. A malformed payload must **fail loudly**, never degrade into a plausible number.

| Rejected | Why |
|---|---|
| Missing `rate` | Nothing to convert with |
| `rate == 0`, negative | Not a rate |
| `NaN`, `Infinity` | Would poison every downstream figure silently |
| Non-numeric, or boolean | `True` is numerically 1 in Python — i.e. parity by accident |
| `base != USD`, `quote != EUR` | Right shape, wrong pair |
| Missing or unparseable `date` | A rate without a date cannot be attributed |
| Non-JSON content type, malformed body | Not a response |

The rate is parsed as `Decimal(str(value))`, not `Decimal(value)`, so `0.87897` stays exactly `0.87897` instead of acquiring binary float error. Money is `Decimal` from here to the PDF.

---

## 4. Fallback order

```
1. Live Frankfurter response, filtered to ECB
2. Most recent cached ECB rate — rejected if older than FX_MAX_CACHED_RATE_AGE_DAYS (7)
3. Explicitly labelled development fixture
```

**There is no fourth step.** If all three fail, the request fails with `FX_RATE_UNAVAILABLE`.

### Parity is unreachable, not discouraged

- No setting exposes a fixed rate. `CASE_USD_EUR_RATE` does not exist, and `.env.example` says so in a comment.
- No code path defaults to `1.0`.
- A test greps the source tree for the usual offenders (`rate = 1.0`, `rate or 1`, `USD_EUR_RATE`).
- A test asserts that with live retrieval failing and no cache, the returned rate is still not `1`.

A convention can be forgotten by the next person to touch the file. An absent setting cannot be used.

### Stale cache

A cached rate older than the configured maximum is **rejected**, not used with a warning. A three-month-old rate is not a better answer than a labelled fixture — it is a worse one, because it looks current.

---

## 5. Provenance travels with the value

Every rate carries where it actually came from:

| `retrieval_source` | Displayed as |
|---|---|
| `live` | Live |
| `cache` | Cached |
| `live_fallback_cache` | Cached (live unavailable) |
| `fixture` | **Demo fixture** |
| `live_fallback_fixture` | **Demo fixture (live unavailable)** |

Surfaced in three places: the FX row in the UI, the stored proposal snapshot, and the PDF's assumptions section. Fixture mode additionally prints an explicit line — *"This rate is demo fixture data, not a live market rate."*

The label is **text**, never colour alone, so it survives a monochrome print and a colourblind reader.

---

## 6. Immutability

> **A finalised proposal never re-reads the rate.**

At finalisation, the rate, its date, its source and the converted CAPEX are written into the proposal snapshot. The share page and the PDF read that blob and nothing else.

Consequences:

- Reopening a proposal months later reproduces the figures that were quoted.
- The PDF and the web page cannot disagree, because there is one set of stored values and no recomputation on either path.
- A market move cannot silently rewrite a document a customer has already been sent.

A test proves it: finalise a proposal, force a different rate into the cache, reload — the stored rate, converted CAPEX and payback are all unchanged.

---

## 7. Fixture

[`fixtures/exchange-rates/usd-eur-ecb.json`](../fixtures/exchange-rates/usd-eur-ecb.json) is a verbatim capture of a real response, flagged `"fixture": true`. It is parsed by the **same validator** as a live response, so fixture mode cannot drift into being a second, subtly different implementation.

---

## 8. Configuration

| Setting | Default | Purpose |
|---|---|---|
| `FX_MODE` | `live` | `live` or `fixture` |
| `FX_PROVIDER` | `frankfurter` | Named in the proposal |
| `FX_DATA_PROVIDER` | `ECB` | Sent as `providers=` and named in the proposal |
| `FX_BASE_URL` | `https://api.frankfurter.dev/v2` | Fixed, trusted base — never user-supplied |
| `FX_TIMEOUT_SECONDS` | `5` | |
| `FX_CACHE_TTL_HOURS` | `24` | |
| `FX_MAX_CACHED_RATE_AGE_DAYS` | `7` | Beyond this a cached rate is refused |
| `FX_FALLBACK_ENABLED` | `true` | When false, a live failure raises instead of falling back |

The request URL is assembled from configuration only. No user input reaches it, so the client cannot be steered at an arbitrary host.

---

## Related

- [`docs/testing.md`](testing.md) — the 25 FX tests
- [`docs/assumptions.md`](assumptions.md) — the financial simplifications
