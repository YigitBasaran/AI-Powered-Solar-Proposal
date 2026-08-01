# Geometry

How a satellite image becomes metres, and the assumptions that make each step valid.

This is the highest-risk part of the system, because **a wrong measurement still renders as a beautiful roof**. Nothing looks broken. Every rule below exists because breaking it produces a plausible wrong answer rather than an obvious failure.

Implementation: [`apps/api/app/domain/geometry.py`](../apps/api/app/domain/geometry.py) — pure functions, no I/O.

---

## 1. Three coordinate spaces

| Space | Definition | Role |
|---|---|---|
| **Geographic** | WGS84 latitude/longitude | Input to Maps and PVGIS |
| **Source-map pixels** | The canonical raster defined by a *verified* `centre / zoom / size / scale`. Origin top-left, `y` increasing **downwards** | **Authoritative.** Calibration, edges, areas and panel geometry all live here |
| **Projected metric** | Ground-plane metres. Origin at raster centre, `x` east, `y` **north** | Measurement and plan-view rendering |
| **Facet surface** | Metres on the sloped plane of one facet: `u` along the eave, `v` up the slope | Panel placement — a panel is 1 × 2 m here and nowhere else |
| *(derived)* Display | Render-time transform | Never stored |

### The one rule

> **Metres-per-pixel is derived from Web Mercator and the verified `zoom`/`scale` only — never from the pixel dimensions of whatever image is on screen.**

An image may have been cropped, resized or re-encoded. Its dimensions carry no scale information.

Concretely: the case brief's own reference images measure roughly **3.4× the magnification** of the source-map grid. Using their pixels as source pixels would have inflated every length by 3.4× and every **area by 11.6×** — while producing a roof that looked entirely normal.

### The bug this discipline caught

During calibration, a segmentation result in *window* coordinates was passed through as *source-map* pixels, offsetting the entire calibration by the 410 px window origin.

The footprint dimensions were correct. The aspect ratio was correct. All four facet azimuths were correct. Unit tests passed. It was only visible when the overlay was rendered on the imagery — the outline sat over a road.

That is why the derivation script emits a debug overlay, and why nothing in this codebase converts between spaces outside `geometry.py`.

---

## 2. Pixel to metre

Web Mercator ground resolution, with `R = 6,378,137 m` and a 256 px tile:

```
metersPerLogicalPixel     = cos(latitude) × 2πR / (256 × 2^zoom)
metersPerSourceImagePixel = metersPerLogicalPixel / scale
```

`scale=2` returns a raster at twice the logical dimensions, so each source pixel covers **half** the ground of a logical pixel.

At the case site (`lat −34.04658`, `zoom 20`, `scale 2`):

| Quantity | Value |
|---|---|
| Mercator m per source px | `0.0746455` |
| **Ground m per source px** | **`0.0618500`** |
| Raster | `1280 × 1280` |
| Ground span | `79.168 m` |

`cos` is even, so the southern hemisphere resolves identically — the sign matters for *orientation*, not for *scale*.

> **Mercator metres are not ground metres.** They differ by `cos(latitude)` — 21 % at this site. A bounding box specified in EPSG:3857 is in Mercator units. Mixing the two is an easy and invisible error; this build made it once while fetching imagery and caught it by cross-checking the raster's ground span.

## 3. Image `y` is inverted

Source pixels increase downwards; metric `y` increases north. The conversion negates it:

```
metric_x = (px − centre_x) × m_per_px
metric_y = (centre_y − py) × m_per_px
```

Getting this wrong mirrors **every azimuth** — north becomes south — while every length and area stays correct. Tests pin the direction explicitly.

---

## 4. A-GEO-1 — the planar-facet assumption

> **Each facet is planar, and its assigned eave is level. Therefore the in-plane direction perpendicular to the eave is the true line of maximum slope.**

Everything below holds **only** under this assumption.

| Consequence | Why |
|---|---|
| `u_surface == u_projected` | `u` runs along a level eave, so it is not foreshortened at all |
| `v_surface = v_projected / cos(pitch)` | `v` runs up the true slope — the only axis the correction touches |
| `sloped_area = projected_area / cos(pitch)` | Uniform foreshortening across a planar facet |
| **A hip is *not* `projected / cos(pitch)`** | A hip runs *diagonally across* the slope, not up it |

### The hip, measured

On the case roof, at 25° pitch:

| Method | Length |
|---|---|
| Plan (projected) run | 5.051 m |
| **True 3-D** `√(dx² + dy² + dz²)` | **5.319 m** |
| Naive `projected / cos(25°)` | 5.573 m |

The naive form overstates each hip by **4.8 %**, and that error compounds into facet areas and panel counts. `edge_type_supports_slope_correction()` returns `False` for every roof edge type in this model, and a regression test asserts a hip's computed length is *not* equal to the naive value.

Eaves and the ridge are horizontal, so their true length equals their plan length — also asserted.

### Validating the assumption

`validate_level_eave()` checks that a facet's eave endpoints share an elevation. Where vertex heights are unknown it returns `True` and the assumption is **recorded as taken on trust** rather than silently assumed correct.

---

## 5. Areas

Shoelace on metric coordinates:

```
projected_area = |Σ (x_i · y_{i+1} − x_{i+1} · y_i)| / 2
```

Absolute value, so winding order is irrelevant. Sloped area divides by `cos(pitch)`.

**Cross-check:** a facet's outline transformed into surface coordinates and measured there equals its sloped area to within `1e-14` on the real roof. The surface frame is an isometry of the sloped plane, and that test proves it rather than assuming it.

---

## 6. Facet orientation

A pitched facet drains away from its ridge and towards its eave, so the downslope direction is **centroid → eave midpoint**:

```
east  =  Δx
north =  Δy                    (metric; already north-positive)
azimuth = (degrees(atan2(east, north)) + 360) mod 360
```

Compass: north 0°, east 90°, south 180°, west 270°.

### PVGIS aspect

PVGIS uses a different convention: **south 0°, west 90°, east −90°, north ±180°**.

```python
aspect = compass − 180
# wrapped into (−180, 180]
```

This is **hemisphere-agnostic** — a pure change of angular reference. What changes below the equator is which aspect is *good*, not how it is computed.

### The calibration is bound to the imagery it was traced on

Pixels only mean something relative to one raster, so a calibration is valid
only while the raster it was traced on is the raster being served. Nothing
enforced that, and the cost was real: the committed vertices were digitised on
**Esri** imagery re-projected onto Google's grid, and when live Google imagery
was first served the roof sat about **1.2 m** from where the calibration said it
was. Every number downstream computed cleanly — from the wrong outline.

Two signatures now, because two different things drift.

| | Covers | Check | On mismatch |
|---|---|---|---|
| **Request signature** | centre, zoom, size, scale, map type, and the raster geometry that follows | strict equality | start-up fails, naming the field that moved |
| **Imagery signature** | what Google actually *returns* | perceptual hash, Hamming ≤ 8 of 64 | map still renders; measurement, layout and finalisation stop |

The request being right is not enough, and that is exactly how the original bug
hid: centre, zoom, size and scale were all word-for-word correct while the
imagery underneath was another provider's capture.

The content check is perceptual rather than exact because a re-encode is not a
moved roof. Measured on the real service: two fetches of the same tile differ by
**0** bits; the Esri fixture and the live Google raster differ by **22**. The
SHA-256 is recorded for diagnostics and gates nothing — treating a re-compressed
identical view as a moved roof is a false alarm, and false alarms are how a check
ends up disabled.

The old guard compared the raster **width and nothing else**, so a changed zoom,
scale, centre or map type sailed through. Halving the zoom while doubling the
requested size leaves the raster 1280 px wide and covering four times the ground;
that now fails loudly.

### Degraded mode is deliberately asymmetric

When the imagery cannot be verified the map **still renders** — a customer
looking at their own roof is not harmed by looking at it. What stops is
everything *measured* from it: roof measurements, panel layout and finalisation
return `409 ROOF_CALIBRATION_UNVERIFIED` with the Hamming distance and a
re-trace instruction. A misplaced outline yields an area, a panel count and a
payback that all look exactly as confident as correct ones.

Google will re-fly this tile eventually. That turns a silent regression into an
explicit, actionable failure.

### Both conventions are always named

A field called `aspect` could mean either, and the ambiguity is not academic —
the two readings are **different roof planes**, 180° apart. A figure recorded
under an ambiguous name is a figure that can be attributed to the wrong facet.

So nothing in the snapshot is called `aspect` alone. Every angle carries the
convention in its name, in `energy.facets[]` and in `energy.pvgis.probes[]`
alike:

| Field | Convention | Purpose |
|---|---|---|
| `compassAzimuthDeg` | N 0°, E 90°, S 180°, W 270° | human reading; matches a compass and the roof-view overlay |
| `pvgisAspectDeg` | S 0°, W 90°, E −90°, N ±180° | what is *sent* to PVGIS, and the equality key when deciding whether a stored probe may be reused |

`pvgisAspectDeg` is kept in provenance at **six decimal places** rather than the
two used for display: it is a comparison key, and rounding to two would collapse
−169.3798 and −169.38 into one value, quietly making two different facets look
like the same observation.

### The case roof

| Facet | Compass | PVGIS aspect | 1 kWp yield |
|---|---|---|---|
| North trapezoid | 10.5° | −169.5° | **1,678.8 kWh/kWp** |
| West triangle | 278.0° | 98.0° | 1,503.9 |
| East triangle | 100.6° | −79.4° | 1,367.2 |
| South trapezoid | 187.6° | 7.6° | 1,114.9 |

Azimuth is measured **centroid → eave midpoint**, so it depends on a facet's shape and not only on its eave. Correcting `v_ridge_0` reshaped the south and west facets and moved their bearings by 3.0° and 2.6° while their eaves did not move at all. That is a real property of this estimator: for a trapezoid whose ridge is parallel to its eave it is exactly the downslope direction, and this roof's ridge is no longer parallel to either long eave.

At −34° latitude the **north** face is the best, by 50 %. No optimal aspect is hardcoded anywhere; ranking comes from per-facet PVGIS probes, so the correct answer emerges from data rather than from an assumption that happens to be northern-hemisphere.

Those four yields are **live retrievals**, one per facet, taken at 1 kWp before
any system size is chosen — which is what lets a later size change reuse them
without asking PVGIS the same four questions again. The figures above are from
the committed captures; a live run agrees to within a hundredth of a kWh/kWp.

---

## 7. Surface coordinates

For each facet, built from its assigned eave:

- `origin` — the eave's first endpoint
- `eave_unit` — along the eave
- `inward_unit` — the eave normal, sign-flipped if needed so it points **into** the facet, which keeps `v ≥ 0` everywhere on the facet

```
u          = (p − origin) · eave_unit
v_projected = (p − origin) · inward_unit
v_surface   = v_projected / cos(pitch)
```

and back:

```
v_projected = v_surface × cos(pitch)
p = origin + u × eave_unit + v_projected × inward_unit
```

**Panel placement runs entirely in this space.** Placing in the plan view instead would shrink every panel by `cos(pitch)` along the slope and let far too many fit — at 25° that is an 11 % overcount of usable length, silently.

Tests assert that a 1 × 2 m panel in surface space projects to a rectangle unchanged along `u` and scaled by exactly `cos(25°)` along `v`.

---

## 8. Responsive rendering

```
display = source × (displayed_size / source_size)
```

One factor, applied to every layer on the way out. Display coordinates are **never stored** — which is what keeps the overlay aligned through any resize, and what prevents the window-origin class of bug from recurring.

---

## Related

- [`docs/panel-placement.md`](panel-placement.md) — how the surface frame is used
- [`docs/assumptions.md`](assumptions.md) — the full assumption list
- [`docs/location-verification.md`](location-verification.md) — how the raster configuration was verified
