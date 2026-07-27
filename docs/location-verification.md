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

Favourably, the reference roof's **largest facet (the north trapezoid, calibrated azimuth 10.6°) is also its best-producing one** — 1,678.7 kWh/kWp against south's 1,119.8.

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

### The fixture must not be used as a scale source

`MAPS_MODE=fixture` is the default (no API key required). The committed fixture is rendered on the **identical Web Mercator bbox** that Google Static Maps `z20 / scale=2 / 640x640` covers, at the identical `1280 × 1280` raster size. Therefore `FixtureImageTransform` is the **identity**, `verified = true`, and calibration performed against the fixture remains valid unchanged when `MAPS_MODE=live` swaps in real Google imagery.

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

Calibrated geometry (`apps/api/app/data/fixed_roof_calibration.json`):

| Quantity | Value |
|---|---|
| Footprint | 11.216 m × 7.143 m = 80.11 m² |
| Ridge length | 4.073 m |
| Eaves | 11.216 m ×2, 7.143 m ×2 |
| Hips | 5.051 m in plan, **5.319 m true** (not 5.573 m — see A-GEO-1) |
| Total sloped area @ 25° | 88.40 m² |
| North / south trapezoid | 27.30 m² projected, 30.12 m² sloped each |
| East / west triangle | 12.76 m² projected, 14.08 m² sloped each |
| Facet azimuths | N 10.6° · E 100.6° · S 190.6° · W 280.6° |

### Capacity, as measured

Earlier in this build I forecast from rough hand-measurements that 9.6 kWp would be infeasible. **That forecast was wrong.** With the calibrated geometry and the real packing algorithm, the roof holds exactly 24 panels:

| Facet | Sloped area | Panels (landscape) |
|---|---|---|
| North trapezoid | 30.1 m² | 9 |
| South trapezoid | 30.1 m² | 9 |
| West triangle | 14.1 m² | 3 |
| East triangle | 14.1 m² | 3 |
| **Total** | **88.4 m²** | **24** |

So all three case system sizes are physically satisfiable, with 9.6 kWp consuming every available slot:

| System | Panels | Placed | Allocation |
|---|---|---|---|
| 3.6 kWp | 9 | 9 | North only |
| 6.0 kWp | 15 | 15 | North 9 + West 3 + East 3 |
| 9.6 kWp | 24 | 24 | All four facets |

The 6 kWp allocation is the one that demonstrates the optimiser is genuinely production-driven. North and south are the same size and hold 9 panels each, so an allocator ranking facets by area would place the remaining 6 panels on south. The correct answer is to fill both small triangles instead and leave south empty, because at this southern-hemisphere site west (1,515 kWh/kWp) and east (1,367) out-produce south (1,120).

The honest capacity-warning path is still implemented and tested — it triggers under a realistic 1 m safety setback, which does make 24 panels impossible.
