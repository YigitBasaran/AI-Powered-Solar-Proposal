# Assumptions

Everything this system takes on trust, and what each one costs if it is wrong.

An assumption stated is a decision the reader can check. An assumption buried in code is a defect waiting to be discovered by a customer.

---

## Location

| Assumption | Consequence if wrong |
|---|---|
| The case coordinate's latitude is missing a minus sign, and the intended point is `−34.04658242871865, 18.46491476666948` (Cape Town) | Everything downstream is measured on the wrong building. Evidenced three ways in [`location-verification.md`](location-verification.md): the positive reading is open sea and does not reverse-geocode; PVGIS returns land at 17 m for the negative one; imagery matches the brief's reference photographs to 0.1° of roof rotation |
| Geocoding is out of scope, so an address cannot be checked against the calibrated property | A location away from the case coordinate is **refused** and the case property offered instead, with nothing stored. Accepting it — which an earlier build did — labelled every figure downstream with a property that had never been measured. Acceptance is a 10 m equirectangular tolerance, which covers consumer-GPS error and every truncation of the coordinate in this repository, and rejects a neighbouring plot |
| The imagery shows the current state of the property | Imagery date is unknown. A recent extension, a new chimney or a removed tree would not appear |

## Raster and scale

| Assumption | Consequence if wrong |
|---|---|
| `zoom 20`, `size 640×640`, `scale 2` → a `1280 × 1280` raster | Every length scales by the same wrong factor; areas by its square |
| Ground resolution is `0.0618500 m` per source pixel, from Web Mercator | See above. Derived from configuration only — never from an image's dimensions |
| The committed fixture sits on the *exact* Google `z20/scale2` bounding box | If not, the fixture→source transform would not be the identity and calibration would not carry over to live imagery |
| The imagery is near-nadir | Off-nadir parallax displaces a roof relative to its footprint. Not corrected for |

## Roof geometry

| Assumption | Consequence if wrong |
|---|---|
| **A-GEO-1**: each facet is planar with a level eave | The pitch correction would be applied along the wrong axis. See [`geometry.md`](geometry.md) |
| Uniform **25° pitch** on all four facets, as the brief specifies | Sloped areas and panel capacity scale with `1/cos(pitch)` |
| Panels lie in the roof plane, so rotating one changes packing but not yield | Tilt and azimuth come from the facet, never from the panel. `PANEL_ROTATION_STEP_DEG` therefore buys panels, not efficiency — and the arrays it turns are not buildable with standard rails. See [`known-limitations.md`](known-limitations.md#rotated-arrays-are-not-buildable-as-drawn) |
| The roof is a hip: 4 eaves, 4 hips, 1 ridge, 2 trapezoids + 2 triangles | Matches the brief's reference overlay. It is **no longer symmetric** — see below |
| Uniform pitch is applied to all four facets even though the corrected plan geometry no longer implies it | Taking the operator's two marks literally leaves the hips at 45.20/47.22/44.16/45.84° in plan, and holding the ridge height the facets would want pitches spanning **1.68°**. Pitch here is a configured assumption that was never measured from imagery, so four pitches are not derived from two hand-placed dots. Recorded in the calibration's `operator_correction.unmodelled_residual` |
| The footprint is a general quadrilateral in plan | It **was** a fitted minimum-area rectangle; the operator moved `v_corner_a` against the raster, so opposite eaves now differ (11.360/11.216 and 7.143/6.979 m) |

### Obstructions

One is modelled: a **chimney of 2.99 m² in plan** on the north facet, outlined by the operator on the raster and stored in the calibration's `obstructions` block. It is subtracted from the facet before panel placement, drawn in red on the workspace and the proposal, and costs the roof three panel bays — capacity 24 → 21. Array rotation then wins one back on the east triangle, so the roof holds **22** and the largest offered system still does not fit.

That is the only one. Vents, skylights and HVAC are still unmodelled, and a panel could be placed over any of them.

## Panels

| Assumption | Value |
|---|---|
| Panel size | 1 m × 2 m, flush-mounted in the plane of the roof |
| Panel rating | 400 Wp |
| Required count | `size_kWp × 1000 / 400` — derived, never hardcoded |
| Inter-panel gap | 0.02 m |
| Roof-edge setback | **0.0 m** — the brief specifies none |
| Orientation | Portrait and landscape both evaluated per facet |
| Array angle | Each facet's array may be turned from its eave, searched in 5° steps. **Not buildable with standard rails** — set `PANEL_ROTATION_STEP_DEG=0` to disable |

> The zero setback is the brief's figure, not a safe default. Most jurisdictions require a fire/access setback at ridge and eaves, which would materially reduce capacity. `ROOF_EDGE_SETBACK_M` is configurable, and a 1 m setback is what the capacity-warning test uses.

## Production

| Assumption | Value |
|---|---|
| Source | PVGIS 5.3 `PVcalc`, one request per **occupied** facet |
| Radiation database | `PVGIS-SARAH3` at this site |
| System loss | 14 % |
| PV technology | Crystalline silicon |
| Mounting | Building-integrated |
| Tilt and azimuth | From the roof — PVGIS optimal-angle mode is deliberately **not** requested |
| **No shading** | No near-object or horizon shading is modelled. On a real site this is often the largest single error |
| Production scales linearly with installed capacity | Used to serve any system size from a cached 1 kWp probe |

## Financial

| Assumption | Value |
|---|---|
| Monthly consumption | 1,150 kWh → 13,800 kWh/year |
| Electricity price | €0.25/kWh, **held flat for 20 years** |
| Capital cost | $10,000 USD, converted at the ECB reference rate |
| Savings | `min(production, consumption) × price` |
| **No export compensation** | Generation beyond consumption earns nothing |
| No degradation | Real modules lose ~0.5 %/year |
| No inflation, no O&M, no financing cost, no tax effect | |
| Analysis period | 20 years |
| Money | `Decimal`, canonicalised to cents once |

### The one that matters most

**Savings are capped at annual consumption**, which implicitly assumes generation and consumption occur at the same moment. They do not. Real self-consumption without a battery is typically 30–50 %.

This follows the brief's methodology, and it is the **single largest source of optimism** in the savings figure. Hourly simulation is the first item in [`case-questions.md`](case-questions.md) for exactly this reason.

## Data sources

| Assumption | Note |
|---|---|
| Fixture data is never presented as live | Enforced: retrieval source travels with every value and is shown in UI, snapshot and PDF |
| USD/EUR parity is never substituted | Structurally unreachable — see [`exchange-rates.md`](exchange-rates.md) |
| A finalised proposal never re-reads a live rate | Immutable snapshot; test-enforced |
| The satellite fixture is licensed for evaluation only | See [`LICENSE-NOTICE.md`](../LICENSE-NOTICE.md) |

## Language model

| Assumption | Note |
|---|---|
| The LLM never produces a number that reaches the domain | It parses intent and writes prose. Output is schema-constrained, Pydantic-validated, then re-checked against the same whitelist the rules parser uses |
| The deterministic parser covers every phrasing the brief demonstrates | `LLM_PROVIDER=rules` is a complete implementation, not a degraded one |
| Model output is untrusted input | Prompt-injection content cannot move the workflow or alter a value |

---

## What a real deployment would need first

1. Shading and obstruction analysis — the largest missing physical effect
2. Hourly simulation and self-consumption — the largest missing financial effect
3. Jurisdiction-specific setbacks
4. Confirmation of imagery date and off-nadir angle
5. Module degradation and a tariff escalation curve
