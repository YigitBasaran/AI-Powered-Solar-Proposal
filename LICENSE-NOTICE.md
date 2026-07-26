# Third-Party Asset and Licence Notice

This repository is a **case-study submission**. It bundles a small number of third-party assets so the application runs end-to-end without API credentials. Those assets are included for **evaluation of this submission only**.

> **None of the bundled imagery is cleared for unrestricted public redistribution.** Do not treat inclusion here as a grant of rights. Anyone reusing this repository beyond reviewing the submission should replace the fixtures with imagery they are licensed to use, or run in `MAPS_MODE=live` with their own Google Maps API key.

---

## 1. Satellite map fixture

| | |
|---|---|
| **File** | `fixtures/maps/satellite-fixture.png` (+ `satellite-fixture.json`) |
| **Source** | Esri World Imagery, via the ArcGIS Online `World_Imagery/MapServer` export endpoint |
| **Attribution** | Imagery © Esri, Maxar, Earthstar Geographics, and the GIS User Community |
| **Retrieved** | 2026-07-26 |
| **Scope** | Case-study submission only. Not cleared for unrestricted public redistribution. |

**Why this asset exists.** The brief specifies Google Maps Static API, which requires an API key. To keep the application runnable with **zero credentials**, the fixture is rendered from an openly reachable imagery service on the **exact Web Mercator bounding box** that Google Static Maps `zoom=20 / scale=2 / size=640x640` covers at the resolved coordinate, at the identical `1280 × 1280` raster size.

Because the fixture sits on the same grid as the live raster, the fixture→source-map transform is the **identity**, and roof calibration performed on the fixture remains valid unchanged when `MAPS_MODE=live` substitutes genuine Google imagery. The fixture is a stand-in for the *pixels*, never for the *geometry*: all real-world measurement derives from the verified Web Mercator configuration, not from the image.

**Attribution handling.** The attribution string is stored in `satellite-fixture.json`, rendered as a persistent layer in the roof workspace, and therefore baked into every exported layout snapshot and PDF. It is not removable from the UI.

**Live mode.** Setting `MAPS_MODE=live` with a `GOOGLE_MAPS_API_KEY` bypasses this fixture entirely and uses Google imagery under Google's own terms, with Google's attribution baked into the returned raster.

---

## 2. Case brief reference images

| | |
|---|---|
| **Files** | `fixtures/reference/case-roof-photo.png`, `fixtures/reference/case-roof-overlay.png` (+ `README.json`) |
| **Source** | The solarVis case-study brief (Notion page) |
| **Underlying imagery** | Map data © Google |
| **Retrieved** | 2026-07-26 |
| **Scope** | Case-study submission only. Reproduced from material supplied with the assignment. |

These are the brief's own authoritative illustrations of the target property and its expected four-facet roof reconstruction. They are retained because the brief requires the reconstruction to match them.

They are used **strictly as topology references** — to confirm the facet layout, edge classification and building identity. They are **never** used as the map substrate and **never** as a source of scale: they are cropped, resized screenshots at roughly 3.4× the magnification of the z20/scale2 grid, and their pixel dimensions carry no reliable geometric information. See `docs/location-verification.md` §5.

---

## 3. Other third-party services used at runtime

Called live over the network; no data redistributed in this repository.

| Service | Use | Terms |
|---|---|---|
| **PVGIS 5.3** (European Commission JRC) | Facet-level PV yield | Free non-commercial use; JRC attribution retained in proposal assumptions |
| **Frankfurter** (`api.frankfurter.dev`) | USD→EUR reference rate | Open API; underlying data © European Central Bank |
| **European Central Bank** | Reference rate provider | ECB euro foreign exchange reference rates |
| **Google Maps Static API** | Satellite imagery in `MAPS_MODE=live` | Google Maps Platform Terms of Service; requires the user's own API key |
| **Nominatim / OpenStreetMap** | One-off coordinate verification during development | © OpenStreetMap contributors, ODbL 1.0 — used for research only, not called at runtime |
| **Ollama + Qwen3.5** | Optional local LLM | Model weights are **not** committed; pulled on demand by the user |

Cached PVGIS and FX responses under `fixtures/pvgis/` and `fixtures/exchange-rates/` are small, explicitly labelled development fixtures used only when the corresponding `*_MODE=fixture` is set. They are always surfaced in the UI and in the PDF assumptions section as fixture data, never presented as live.

---

## 4. Application source

All application source code, geometry, optimisation, financial and documentation work in this repository is original and written for this case study.
