"""Regenerate `sample-output/` through a real PVGIS retrieval.

The committed sample PDF is the one artefact a reader is most likely to take at
face value: it looks like a proposal that was actually issued, so it has to have
been. This drives the real application end to end - intake, four live 1 kWp
probes at Ispra, layout, FX, finalisation, PDF - and writes:

* `example-proposal.pdf`      the document
* `example-proposal.json`     the snapshot it was rendered from, which is what
                              makes the provenance checkable rather than
                              asserted. `test_sample_output.py` reads it.

Deliberately opt-in and deliberately not part of any suite. It makes real
requests to an external service, and running it in CI would put a third party's
availability on the critical path of a test run.

    cd apps/api
    ./.venv/Scripts/python.exe ../../scripts/regenerate_sample_output.py

Refuses to write anything unless every probe came from the canonical origin.
A stub-generated sample committed by accident is exactly the confusion this
whole change exists to prevent, and the check belongs here rather than only in
the test - by the time the test runs, the wrong file is already on disk.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "apps" / "api"
OUT = ROOT / "sample-output"

sys.path.insert(0, str(API))

CASE_COORD = "-34.04658242871865, 18.46491476666948"
SIZE = "6 kWp"
CONSUMPTION = "1,150 kWh"


def _configure() -> None:
    """A throwaway database, the canonical endpoint, and no replay override."""
    tmp = Path(tempfile.mkdtemp(prefix="solarvis-sample-"))
    os.environ.update(
        {
            "DATABASE_URL": f"sqlite+aiosqlite:///{(tmp / 'sample.db').as_posix()}",
            "PVGIS_BASE_URL": "https://re.jrc.ec.europa.eu/api/v5_3",
            # Not set to true, and that is the point: if the analysis somehow
            # came back as replay, finalisation would refuse and this script
            # would fail rather than produce a mislabelled document.
            "ALLOW_REPLAY_PROPOSALS": "false",
            "LLM_PROVIDER": os.environ.get("LLM_PROVIDER", "rules"),
        }
    )


async def main() -> int:
    _configure()

    from fastapi.testclient import TestClient

    from app.core.config import get_settings
    from app.integrations.pvgis import classify_endpoint
    from app.main import create_app

    get_settings.cache_clear()
    settings = get_settings()

    trust = classify_endpoint(settings.pvgis_base_url)
    if not trust.is_trusted:
        print(f"refusing: {settings.pvgis_base_url} is not the canonical PVGIS endpoint")
        print(f"          ({trust.reason})")
        return 1

    print(f"probing {settings.pvgis_base_url} ...")

    with TestClient(create_app()) as client:
        project_id = client.post("/api/v1/projects").json()["projectId"]
        for message in (CASE_COORD, CONSUMPTION, SIZE):
            response = client.post(
                f"/api/v1/projects/{project_id}/chat", json={"message": message}
            )
            response.raise_for_status()

        analysis = client.post(f"/api/v1/projects/{project_id}/run-analysis")
        if analysis.status_code != 200:
            print(f"analysis failed: {analysis.status_code} {analysis.text}")
            return 1

        snapshot = analysis.json()["analysis"]
        provenance = snapshot["energy"]["pvgis"]
        if provenance["source"] != "live":
            print(f"refusing: the analysis reported source={provenance['source']!r}")
            return 1

        print(f"  {len(provenance['probes'])} probes from {provenance['origin']}")
        for probe in provenance["probes"]:
            print(
                f"    {probe['facetId']:9s} aspect {probe['pvgisAspectDeg']:>11.6f}"
                f"  {probe['specificYieldKwhPerKwp']:>8.2f} kWh/kWp"
                f"  {probe['radiationDatabase']}"
            )

        finalised = client.post(f"/api/v1/projects/{project_id}/finalize")
        if finalised.status_code != 200:
            print(f"finalisation refused: {finalised.status_code} {finalised.text}")
            return 1

        token = finalised.json()["shareToken"]
        pdf = client.get(f"/api/v1/proposals/{token}/pdf")
        pdf.raise_for_status()

        # The frozen snapshot as the share page serves it - the same projection
        # the PDF was rendered from, so the two cannot disagree.
        issued = client.get(f"/api/v1/proposals/{token}")
        issued.raise_for_status()

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "example-proposal.pdf").write_bytes(pdf.content)
    (OUT / "example-proposal.json").write_text(
        json.dumps(issued.json(), indent=2) + "\n", encoding="utf-8"
    )

    print(f"wrote {len(pdf.content):,} bytes to sample-output/example-proposal.pdf")
    print("wrote sample-output/example-proposal.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
