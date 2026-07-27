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
    "pvgis":    { "mode": "live", "ready": true },
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
  "currentStep": "roof_reconstruction",
  "assistantMessage": "Selected system size: 6 kWp…",
  "accepted": true,
  "parserSource": "rules",
  "readyForAnalysis": true,
  "progress": [ … ]
}
```

The raw message is persisted verbatim **before** anything interprets it. `parserSource` is `rules` or `llm`. Messages are capped at 2,000 characters.

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
