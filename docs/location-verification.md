# Location Verification

Required by the case brief: the fixed coordinate must be verified against the authoritative source before the map configuration is locked, and the resolution must be evidenced rather than guessed.

**Status: RESOLVED — sign correction applied, verified by three independent sources.**

---

## 1. The coordinate as written

The case brief (Notion page, retrieved 2026-07-26) states:

```
For this case, we use a fixed location with a 4-facet (hipped) roof:

Fixed location:

34.04658242871865, 18.46491476666948
```

The brief itself writes the latitude **without a minus sign**. This is not a transcription error introduced downstream — the authoritative source carries it.

## 2. Why it cannot be used as written

`+34.04658242871865, 18.46491476666948` falls in the **open Mediterranean Sea**, roughly 300 km north of the Libyan coast.

```
GET https://nominatim.openstreetmap.org/reverse?lat=34.04658242871865&lon=18.46491476666948&format=jsonv2
→ {"error":"Unable to geocode"}
```

There is no land, no building and no roof. Every downstream measurement — edge lengths, facet areas, azimuths, panel positions — would be fiction.

## 3. The resolved coordinate

```
-34.04658242871865, 18.46491476666948
```

### Evidence A — reverse geocoding returns a real address

```
GET https://nominatim.openstreetmap.org/reverse?lat=-34.04658242871865&lon=18.46491476666948&format=jsonv2
→ "Galway Road, Cape Town Ward 73, Cape Town, City of Cape Town,
   Western Cape, 7945, South Africa"
```

### Evidence B — PVGIS resolves it as land

`GET https://re.jrc.ec.europa.eu/api/v5_3/PVcalc?lat=-34.04658&lon=18.46491&...` returns HTTP 200 with `elevation = 17.0 m` and radiation database `PVGIS-SARAH3`. PVGIS returns an error for sea locations outside its land grid.

### Evidence C — the imagery matches the brief's reference photographs

Independent imagery (Esri World Imagery) rendered on the exact Web Mercator bbox for this coordinate shows a **housing estate of identical hipped-roof houses**, a railway line to the east, and a parking area to the north-east. This matches:

- the brief's reference photograph (`fixtures/reference/case-roof-photo.png`) — same house type, same dense row layout;
- OpenStreetMap features within 60 m — public-transport shelters, public toilets, a 55 m platform canopy (consistent with the adjacent railway station);
- Nominatim's `amenity=parking` classification at the exact point.

The house at the image centre is a hipped roof measuring approximately **10.4 m × 6.7 m**, rotated **≈9.7°** clockwise from image-horizontal. The brief's overlay reference (`fixtures/reference/case-roof-overlay.png`) shows the same roof at a rotation of **≈9.8°**, independently corroborating that the two images depict the same building.

### Conclusion

The missing minus sign is a defect in the case brief. The application stores **both** values: the raw coordinate exactly as the brief wrote it, and the resolved coordinate actually used. Nothing is silently modified.

```python
CaseLocationSettings(
    raw_case_latitude   =  34.04658242871865,   # as printed in the brief
    raw_case_longitude  =  18.46491476666948,
    resolved_latitude   = -34.04658242871865,   # used by the application
    resolved_longitude  =  18.46491476666948,
    resolution_note     = "Brief omits the minus sign; +34.0466 is open sea "
                          "(Nominatim: unable to geocode). -34.0466 resolves to "
                          "Galway Road, Cape Town, ZA; PVGIS confirms land at 17 m; "
                          "imagery matches the brief's reference photographs.",
    source_verified     = True,
)
```

---

## 4. Consequence: southern hemisphere

The site is at latitude **−34°**, so solar optimality is **inverted** relative to the northern-hemisphere assumptions the brief is written with. Measured live at the resolved coordinate (6 kWp, 25° tilt, 14 % loss, crystSi, building-mounted):

| Facet orientation | PVGIS `aspect` | Annual production | Specific yield |
|---|---|---|---|
| **North-facing** | `180` | **10,122.31 kWh** | ≈1,687 kWh/kWp |
| South-facing | `0` | 6,646.46 kWh | ≈1,108 kWh/kWp |

North-facing produces **≈52 % more** than south-facing here. The panel allocator must therefore prefer north-facing facets. No "optimal aspect" is hardcoded anywhere — ranking comes from a per-facet 1 kWp PVGIS probe, so the correct answer emerges from data rather than assumption.

This used to be favourable: north was the largest facet as well as the best-producing one. It is not any more. Correcting `v_ridge_0` against the raster made the **south** trapezoid the largest (31.0 m² sloped against north's 30.0), and the chimney then took three of north's six remaining bays. So the roof's biggest facet now points at its worst aspect — 1,114.9 kWh/kWp against north's 1,678.8 — which is precisely why the allocator ranks on yield rather than on area.

---

## 5. Map configuration

| Parameter | Value |
|---|---|
| Centre | `-34.04658242871865, 18.46491476666948` |
| Zoom | `20` |
| Requested size | `640x640` (logical) |
| Scale | `2` |
| Map type | `satellite` |
| **Actual source-image dimensions** | **`1280 × 1280`** |
| Mercator m per source px | `0.0746455` |
| **Ground m per source px** | **`0.0618500`** |
| Ground span of raster | `79.168 m` |
| EPSG:3857 bbox | `2055457.136, -4035106.398, 2055552.683, -4035010.851` |

Ground resolution is derived from Web Mercator and the verified `zoom`/`scale` **only**:

```
metersPerLogicalPixel     = cos(|lat|) × 2πR / (256 × 2^zoom)
metersPerSourceImagePixel = metersPerLogicalPixel / scale
                          = 0.0618500 m
```

### No image is used as a scale source

Ground resolution comes from Web Mercator and the verified `zoom`/`scale`, never from the pixel dimensions of whatever raster is on screen. That rule is why the `0.0618500 m` above is derived rather than measured.

**There is no imagery mode.** The application always fetches the raster over HTTP from `GOOGLE_STATIC_MAPS_BASE_URL`, which in any real deployment is Google's own Static Maps service. Tests point that URL at a local stub, so they stay offline without the *application* having an offline path.

The committed Esri fixture used to stand in for live imagery, and its sidecar asserted that the fixture→source-map transform was the identity, `verified = true`. That claim was read by no code, and it did not survive contact with reality: when live Google imagery was first served, the roof sat about **1.2 m** from where the calibration said it was, because the two providers orthorectify this building differently. The calibration is now traced against Google's own raster and bound to it by a perceptual hash — see [`known-limitations.md`](known-limitations.md). The fixture remains only as test-replay data.

The brief's own reference images are **topology references only** and are deliberately *not* used as the map substrate:

| File | Size | Role |
|---|---|---|
| `fixtures/maps/satellite-fixture.png` | 1280 × 1280 | Map substrate. Exact z20/scale2 grid. Scale authoritative. |
| `fixtures/reference/case-roof-photo.png` | 730 × 684 | Reference only. Cropped screenshot. |
| `fixtures/reference/case-roof-overlay.png` | 1112 × 1098 | Reference only. Expected facet topology. |

> **Scale warning.** The brief's reference images are cropped, resized screenshots. Measurement puts them at roughly **3.4× the magnification** of the z20/scale2 grid. Treating their pixels as source-map pixels would inflate every length by ≈3.4× and every area by ≈11.6× while still looking entirely plausible. Their dimensions carry no scale information and are never used to derive metres-per-pixel.

---

## 6. Expected roof confirmed visible

The brief's overlay reference confirms the expected topology, and it is present in the fixture at the raster centre:

- 4 outer eave edges forming the roof boundary
- 1 central ridge edge
- 4 hip edges running from each outer corner to a ridge endpoint
- 4 facets: 2 trapezoids (north, south) + 2 triangles (east, west)
- Uniform pitch of 25° across all facets

Calibrated geometry (`apps/api/app/data/google_roof_calibration.json`), after the operator correction of 2026-08-01:

| Quantity | Value |
|---|---|
| Footprint | quadrilateral, 79.69 m² |
| Ridge length | 4.374 m |
| Eaves | 11.360 · 7.143 · 11.216 · 6.979 m |
| Hips | 4.775 / 4.893 / 5.051 / 5.051 m in plan; `hip_2` **5.312 m true** (not 5.573 m — see A-GEO-1) |
| Total sloped area @ 25° | 87.93 m² |
| Obstructions | chimney, 2.99 m² plan / 3.30 m² sloped, north facet |
| North / south trapezoid | 27.17 / 28.10 m² projected, 29.98 / 31.00 m² sloped |
| East / west triangle | 12.76 / 11.67 m² projected, 14.07 / 12.88 m² sloped |
| Facet azimuths | N 10.5° · E 100.6° · S 187.6° · W 278.0° |

The roof is no longer symmetric: `v_ridge_0` sits west of centre, so opposing facets differ by 3% (N/S) and 9% (E/W), and the **south** facet is now the largest while remaining the worst aspect.

### Capacity, as measured

Earlier in this build I forecast from rough hand-measurements that 9.6 kWp would be infeasible, then measured 24 panels with the calibrated geometry and recorded that the forecast had been wrong. Modelling the chimney took it to 21; turning the east array back to 45° recovers one, so the roof holds **22**.

| Facet | Sloped area | Panels (landscape) |
|---|---|---|
| North trapezoid | 30.0 m² | 6 |
| South trapezoid | 31.0 m² | 9 |
| West triangle | 12.9 m² | 3 |
| East triangle | 14.1 m² | 4 (array turned 45°) |
| **Total** | **87.9 m²** | **22** |

North loses three bays to a 2.99 m² chimney standing in the middle of it, and east gains one from array rotation. So the two smaller system sizes are satisfiable and the largest is not:

| System | Panels | Placed | Allocation |
|---|---|---|---|
| 3.6 kWp | 9 | 9 | North 6 + West 3 |
| 6.0 kWp | 15 | 15 | North 6 + West 3 + East 4 + South 2 |
| 9.6 kWp | 24 | **22** | All four facets, full — capacity warning |

The 6 kWp allocation is the one that demonstrates the optimiser is genuinely production-driven, and the chimney has made it a stronger demonstration. South is now both the **largest** facet and the one with the **most** free bays, so an allocator ranking by area *or* by capacity would fill it first. It is filled last and given only the two leftover panels, because at this southern-hemisphere site north (1,679 kWh/kWp), west (1,504) and east (1,367) all out-produce south (1,115).

The capacity-warning path is no longer hypothetical: 9.6 kWp triggers it on the calibrated roof, and every downstream figure is computed from the 8.8 kWp that fits. It also still triggers, far harder, under a realistic 1 m safety setback — which leaves room for 3 panels.
