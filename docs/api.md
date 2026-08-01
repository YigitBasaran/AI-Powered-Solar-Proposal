# API

Base path `/api/v1`. Interactive docs at `http://localhost:8000/docs`.

Route functions translate HTTP to domain calls and back. No business logic lives in a handler — it is all in `services/`, so nothing is duplicated between an endpoint and the orchestration path.

---

## Error shape

Every failure the client can see has the same shape, so the frontend never has to guess whether a body is an error:

```json
{
  "error": {
    "code": "FX_RATE_UNAVAILABLE",
    "message": "The USD/EUR reference rate could not be retrieved.",
    "details": { "cacheAvailable": false, "fixtureAvailable": true },
    "requestId": "6f1c…"
  }
}
```

`X-Request-ID` is echoed on every response, and honoured if the client supplies one.

| Code | Status | Meaning |
|---|---|---|
| `NOT_FOUND` | 404 | Unknown project or share token |
| `VALIDATION_ERROR` | 422 | Request failed validation |
| `INVALID_STEP_TRANSITION` | 409 | Action not available at the current step |
| `PROPOSAL_INCOMPLETE` | 409 | Finalisation attempted before the analysis completed |
| `MAPS_UNAVAILABLE` | 502 | Imagery could not be retrieved |
| `PVGIS_UNAVAILABLE` | 502 | Production data unavailable and fallback disabled |
| `FX_RATE_UNAVAILABLE` | 502 | No live, cached or fixture rate — **parity is never substituted** |
| `ROOF_CALIBRATION_MISSING` | 500 | Calibration file missing or invalid |
| `LLM_UNAVAILABLE` | 502 | Model unreachable and fallback disabled |
| `CUSTOMER_NOT_FOUND` | 404 | Unknown customer |
| `CUSTOMER_EMAIL_EXISTS` | 409 | Address already held — `details.customerId` names the record it collided with |
| `RECIPIENT_UNAVAILABLE` | 409 | The proposal has no customer, so there is nobody to email |
| `SEND_CONFIRMATION_REQUIRED` | 409 | `confirm` was not literally `true` |
| `DELIVERY_IN_PROGRESS` | 409 | Another request already holds this send; no provider call was made |
| `PROPOSAL_ALREADY_SENT` | 409 | Already sent to this address — pass `resendNonce` for a deliberate resend |
| `DELIVERY_NOT_FOUND` | 404 | Unknown delivery for this proposal |
| `EMAIL_PROVIDER_UNAVAILABLE` | 503 | Email is not configured. **Never falls back to console** |
| `EMAIL_SEND_FAILED` | 502 | The relay refused the message |
| `EMAIL_SEND_TIMEOUT` | 504 | The relay did not answer — the outcome is genuinely **unknown**, not "not sent" |

---

## Customers

| Method | Route | Notes |
|---|---|---|
| `POST` | `/customers` | `{firstName,lastName,email,phone?,companyName?,address?,displayName?}` → `201`. Email is stored lower-cased and is globally unique |
| `GET` | `/customers?q=&limit=&cursor=&includeArchived=` | Substring search over name, email and company. Keyset pagination |
| `GET` | `/customers/{id}` | |
| `PATCH` | `/customers/{id}` | Partial. An absent key is untouched; an explicit `null` clears |
| `POST` | `/customers/{id}/archive` | Soft delete. Issued proposals are unaffected |

## Projects, extended

| Method | Route | Notes |
|---|---|---|
| `POST` | `/projects` | Body is **optional**: `{customerId?, name?}`. Posting no body still works |
| `POST` | `/projects/{id}/customer` | Assign or change. After finalisation this **forks a revision** and returns it |
| `GET` | `/projects?customerId=` | Narrows the list to one person. What lets a customer's own screen serve its projects from the paged project route rather than from the unpaginated array on `GET /customers/{id}` — one query, one page size, one delete affordance |
| `GET` | `/projects/{id}/revisions` | The whole lineage, oldest first. `isSuperseded` is derived, never stored |
| `GET` | `/projects/{id}/activity` | Append-only timeline across the whole revision chain |

## Proposal delivery

These take the **internal proposal id**, never the public share token — a send
endpoint reachable with the token would let anyone forwarded a proposal link
cause mail to be sent from this system.

| Method | Route | Notes |
|---|---|---|
| `GET` | `/proposals/{proposalId}/email-preview` | Renders exactly what would be sent. **Sends nothing, writes nothing.** `to` is null when there is no customer |
| `POST` | `/proposals/{proposalId}/send` | `{confirm: true, resendNonce?}`. `confirm` must be literally `true` |
| `GET` | `/proposals/{proposalId}/deliveries` | History. The recipient is **masked** here |
| `POST` | `/proposals/{proposalId}/deliveries/{deliveryId}/retry` | Requires confirmation again |

A delivery has four statuses — `pending`, `sending`, `sent`, `failed`. There is
no `delivered`, `bounced` or `opened`: SMTP provides none of them. `sent` means
*the provider accepted the message*. `providerSends` is `false` in console
mode, which is what lets a client avoid reporting a send that never happened.

---

## Health

### `GET /health/live`
```json
{ "status": "alive" }
```

### `GET /health/ready`

Reports **every operating mode explicitly**, because the one thing that must never happen is fixture data being mistaken for live.

```json
{
  "status": "ok",
  "checks": {
    "database": { "mode": "sqlite+aiosqlite", "ready": true },
    "maps":     { "mode": "fixture", "ready": true,
                  "detail": "Development fixture on the exact z20/scale2 grid. Not live imagery." },
    "pvgis":    { "endpoint": "https://re.jrc.ec.europa.eu/api/v5_3/PVcalc",
                  "origin": "https://re.jrc.ec.europa.eu", "apiVersion": "v5_3",
                  "trusted": true, "timeoutSeconds": 15, "maxAttempts": 4,
                  "retryBudgetSeconds": 30, "allowReplayProposals": false,
                  "ready": true, "detail": null },
    "fx":       { "mode": "live", "provider": "frankfurter", "dataProvider": "ECB", "ready": true },
    "llm":      { "provider": "rules", "model": null, "ready": true }
  },
  "sourceRaster": {
    "zoom": 20, "scale": 2, "sourceWidthPx": 1280,
    "groundMetresPerSourcePixel": 0.06185, "groundSpanM": 79.168
  }
}
```

`degraded` rather than `down` when a dependency is missing — the fixture modes exist so a full proposal still completes.

**PVGIS reports no mode, because it has none.** It reports the endpoint that will
be called and whether that endpoint could back a proposal at all. The probe makes
**no outbound call** — a readiness check must not — but it does validate the
configuration, which costs nothing and catches the faults that would otherwise
first surface when a customer runs an analysis: an unparseable URL, an untrusted
endpoint in a production environment, nonsensical retry settings, or
`ALLOW_REPLAY_PROPOSALS` set outside a test environment. Each sets
`ready: false` with a stated `detail`.

### `GET /health/case-location`

Returns **both** coordinates — what the brief printed and what is actually used — plus the reasoning. Nothing is silently substituted.

---

## Maps

### `GET /maps/config`

Everything the client needs to place an overlay, so it never has to derive scale from a rendered image:

```json
{
  "mode": "fixture", "isLive": false,
  "center": { "latitude": -34.04658242871865, "longitude": 18.46491476666948 },
  "zoom": 20, "scale": 2, "requestedSize": "640x640",
  "sourceWidthPx": 1280, "sourceHeightPx": 1280,
  "groundMetresPerSourcePixel": 0.0618499967, "groundSpanM": 79.168,
  "attribution": "Imagery © Esri, Maxar, Earthstar Geographics…",
  "imageUrl": "/api/v1/maps/satellite"
}
```

### `GET /maps/satellite` → `image/png`

Served from this origin in both modes. That keeps the Google key server-side and the Konva canvas same-origin, which is what makes `stage.toDataURL()` possible at all.

`X-Image-Source: fixture | live` on every response.

Live mode validates status, content type, and that the payload is not suspiciously small before returning it.

---

## Roof

### `GET /roof/fixed-model`

The calibrated roof with all derived measurements, in **source-map pixels** — the client applies its own display transform, so one payload renders at any canvas size.

Includes per-facet azimuth, PVGIS aspect, projected and sloped area; per-edge type, plan length and true 3-D length.

Layout facets carry `arrayRotationDeg`: how far that facet's array is turned from its eave. The panel polygons already carry the result, so it is for reading rather than for drawing.

Also `obstructionGeometry`: things standing on the roof that no panel may be placed over, each with `id`, `label`, `kind`, `facetId` and a `sourcePixelPolygon`. Facets carry `obstructedAreaM2` and `usableProjectedAreaM2` alongside their gross area, and the roof carries `totalObstructedAreaM2` — the roof's own size stays a statement about the roof, and how much of it is usable stays a separate, visible question. The array is empty when a calibration declares none.

---

## Projects

### `POST /projects` → `201`
Creates a project and returns the opening assistant message and the progress rail.

### `GET /projects/{id}`
Full state: raw and resolved location, consumption, selected size, derived panel count, analysis status, message history, and the analysis snapshot once it exists.

### `POST /projects/{id}/chat`
```json
{ "message": "the middle option" }
```
```json
{
  "projectId": "1c77…",
  "currentStep": "roof_reconstruction",
  "assistantMessage": "Selected system size: 6 kWp…",
  "accepted": true,
  "parserSource": "rules",
  "readyForAnalysis": true,
  "analysisStatus": "pending",
  "progress": [ … ],
  "interpretation": {
    "configuredProvider": "ollama",
    "attemptedProvider": null,
    "effectiveProvider": "rules",
    "fallbackReason": "rules_sufficient",
    "modelName": null,
    "latencyMs": null
  },
  "revisionOfProjectId": null,
  "recalculated": null
}
```

The raw message is persisted verbatim **before** anything interprets it. Messages are capped at 2,000 characters.

**`parserSource` is unchanged** (`"rules" | "llm"`) but is now **derived** from `interpretation.effectiveProvider` in one place, so the flat field can never contradict the object beside it.

**`interpretation`** says who actually handled the message. The distinction that matters is between *the model was never needed* and *the model was asked and could not answer*: `attemptedProvider` is non-null only when an HTTP call was genuinely issued, and `fallbackReason` names the failure — `rules_sufficient`, `not_configured`, `unreachable`, `timeout`, `http_error`, `empty_response`, `invalid_json`, `schema_rejected`, `domain_rejected`. The first two are ordinary operation. See [`conversation.md`](conversation.md#provider-telemetry).

**`projectId` may differ from the one you posted to.** Changing a value on a project whose proposal has been finalised forks a revision and moves the conversation to it; `revisionOfProjectId` names the parent. The issued proposal and its share link are untouched. A client that ignores this will send its next message to an immutable project.

**`recalculated`** lists the inputs this message recomputed, if any, so a client knows to re-read `GET /projects/{id}`. The whole snapshot is not returned on every chat reply — that would be a large payload for the one message in fifty that changes it.

`analysisStatus` is `pending` · `running` · `complete` · `recalculating` · `stale`. The last two mean the stored analysis does not describe the project's current inputs; `finalize` refuses both.

### `POST /projects/{id}/run-analysis`

One deterministic pass: roof → layout → facet-level PVGIS → FX → financials. Returns the full analysis and any `capacityWarning`.

PVGIS is called with the capacity that **actually fits**; if the request cannot be satisfied, the feasible capacity flows onward and nothing downstream sees the requested figure as installed.

---

## Proposals

### `POST /projects/{id}/finalize`
```json
{
  "shareToken": "prlwhS7puQMs…",
  "shareUrl": "http://localhost:3000/proposal/prlwhS7puQMs…",
  "pdfUrl": "/api/v1/proposals/prlwhS7puQMs…/pdf",
  "summarySource": "deterministic",
  "capacityWarning": null
}
```

Refuses to store an incomplete analysis (`PROPOSAL_INCOMPLETE`). Writes an **immutable snapshot** containing every derived number plus the exchange rate, its date and its source. Tokens carry 192 bits of entropy.

`summarySource` is `llm` or `deterministic` — the model's prose is only used if every number in it matches a computed value.

### `POST /projects/{id}/layout-snapshot`
`multipart/form-data`. PNG-magic checked, size-capped at 8 MB, written to a filename derived from the share token rather than from client text.

### `GET /proposals/{token}`
Public, read-only, no authentication. Serves the stored snapshot — nothing is recomputed, which is why this and the PDF cannot disagree. Includes view statistics.

### `GET /proposals/{token}/pdf` → `application/pdf`
Snapshot → Jinja2 → Chromium → A4, with page numbers and stable breaks. Charts are server-rendered inline SVG, so there is nothing for Chromium to wait on beyond fonts.

### `POST /proposals/{token}/view`
Records a view. IP is **hashed, never stored**. Notification failures are caught — tracking must never break a customer's page view.

### `GET /proposals/{token}/layout-snapshot` → `image/png`

---

## Security

- Secrets stay server-side; no third-party call originates in the browser.
- CORS restricted to the configured web origin.
- External base URLs are fixed in configuration — no user input reaches an outbound URL.
- Share tokens are shape-validated before any lookup, so a path-traversal string never reaches the database.
- Uploads are magic-checked, size-capped and given a derived filename.
- No raw SQL; templates escape by default.
- The public proposal route is strictly read-only.
