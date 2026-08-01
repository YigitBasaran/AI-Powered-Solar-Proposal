"""Seed the PVGIS fixtures from real responses at the resolved case coordinate.

Fixture mode must exercise the same parsing code as live mode, so the fixtures
are genuine PVGIS payloads rather than hand-written numbers. They are captured
once here and committed, which is what makes fixture-mode tests deterministic
enough to carry exact numeric assertions.

Two artefacts are produced:

``specific-yield-by-aspect.json``
    1 kWp specific yield at the case pitch across aspects. This is what the
    panel-placement optimiser ranks facets by, and it is why the optimiser can
    be built and tested before the live PVGIS client exists.

``pvcalc-<aspect>.json``
    Full PVcalc responses at each real facet aspect, used by the PVGIS client's
    fixture mode.

Usage
-----
    python scripts/fetch_pvgis_fixtures.py
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
OUT_DIR = REPO / "fixtures" / "pvgis"
# The profile the runtime actually measures from. `fixed_roof_calibration.json`
# is the superseded Esri tracing, which the signature guard refuses to serve -
# seeding fixtures from it captured aspects no request would ever ask for.
CALIBRATION = REPO / "apps" / "api" / "app" / "data" / "google_roof_calibration.json"

BASE_URL = "https://re.jrc.ec.europa.eu/api/v5_3/PVcalc"
LAT = -34.04658242871865
LON = 18.46491476666948
PITCH = 25.0
LOSS = 14.0
TECHNOLOGY = "crystSi"
MOUNTING = "building"

# A coarse sweep for generality, plus the exact aspects of the case roof's
# facets so the fixture reproduces live behaviour for this property exactly.
COARSE_ASPECTS = [-180.0, -135.0, -90.0, -45.0, 0.0, 45.0, 90.0, 135.0]


def facet_aspects() -> dict[str, float]:
    """Exact PVGIS aspects of the calibrated facets."""
    data = json.loads(CALIBRATION.read_text(encoding="utf-8"))
    out: dict[str, float] = {}
    for f in data["facets"]:
        compass = float(f["approx_compass_azimuth_deg"])
        aspect = compass - 180.0
        while aspect <= -180.0:
            aspect += 360.0
        while aspect > 180.0:
            aspect -= 360.0
        out[f["id"]] = round(aspect, 2)
    return out


def fetch(aspect: float, peakpower: float = 1.0) -> dict[str, Any]:
    params = {
        "lat": LAT,
        "lon": LON,
        "peakpower": peakpower,
        "loss": LOSS,
        "angle": PITCH,
        "aspect": aspect,
        "pvtechchoice": TECHNOLOGY,
        "mountingplace": MOUNTING,
        "outputformat": "json",
    }
    url = f"{BASE_URL}?{urllib.parse.urlencode(params)}"
    last: Exception | None = None
    for attempt, delay in enumerate((0.5, 1.0, 2.0)):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "solarvis-case-study/1.0"}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload: dict[str, Any] = json.loads(resp.read())
            return payload
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
            print(f"    attempt {attempt + 1} failed ({exc}); retrying in {delay}s")
            time.sleep(delay)
    raise RuntimeError(f"PVGIS failed for aspect {aspect}: {last}")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    aspects = facet_aspects()
    print("case facet aspects:", aspects)

    table: dict[str, Any] = {
        "description": (
            "1 kWp specific yield at the case site, by PVGIS aspect, at the case "
            "pitch. Captured from live PVGIS 5.3 so the optimiser can rank facets "
            "without depending on the live client."
        ),
        "source": "PVGIS 5.3 PVcalc (European Commission JRC)",
        "location": {"latitude": LAT, "longitude": LON},
        "pitch_deg": PITCH,
        "system_loss_percent": LOSS,
        "pv_technology": TECHNOLOGY,
        "mounting_place": MOUNTING,
        "peak_power_kwp": 1.0,
        "by_aspect": {},
        "case_facets": {},
    }

    wanted = sorted({*COARSE_ASPECTS, *aspects.values()})
    radiation_db = None

    for aspect in wanted:
        print(f"  aspect {aspect:>7.2f} ...", end=" ", flush=True)
        payload = fetch(aspect)
        totals = payload["outputs"]["totals"]["fixed"]
        monthly = [round(m["E_m"], 3) for m in payload["outputs"]["monthly"]["fixed"]]
        radiation_db = payload["inputs"]["meteo_data"]["radiation_db"]
        e_y = float(totals["E_y"])
        table["by_aspect"][f"{aspect:.2f}"] = {
            "specific_yield_kwh_per_kwp": round(e_y, 3),
            "annual_kwh_per_kwp": round(e_y, 3),
            "monthly_kwh_per_kwp": monthly,
        }
        print(f"{e_y:8.2f} kWh/kWp")

        if aspect in aspects.values():
            path = OUT_DIR / f"pvcalc-aspect{aspect:+.2f}.json".replace(
                "+", "p"
            ).replace("-", "m")
            path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    table["radiation_database"] = radiation_db
    for facet_id, aspect in aspects.items():
        table["case_facets"][facet_id] = {
            "pvgis_aspect_deg": aspect,
            "specific_yield_kwh_per_kwp": table["by_aspect"][f"{aspect:.2f}"][
                "specific_yield_kwh_per_kwp"
            ],
        }

    out = OUT_DIR / "specific-yield-by-aspect.json"
    out.write_text(json.dumps(table, indent=2) + "\n", encoding="utf-8")
    print(f"\nradiation database : {radiation_db}")
    print("case facet ranking (best first):")
    for fid, info in sorted(
        table["case_facets"].items(),
        key=lambda kv: -kv[1]["specific_yield_kwh_per_kwp"],
    ):
        print(
            f"   {fid:<9} aspect {info['pvgis_aspect_deg']:>8.2f}  "
            f"{info['specific_yield_kwh_per_kwp']:8.2f} kWh/kWp"
        )
    print(f"written            : {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
