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
