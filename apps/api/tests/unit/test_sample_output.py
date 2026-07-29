"""The committed sample proposal must be a real one.

`sample-output/example-proposal.pdf` is the artefact a reader is most likely to
take at face value - it looks like a document that was issued to a customer, so
it has to have been. Every other suite in this repo runs against a replay stub,
which means the easy mistake is committing a stub-generated sample and never
noticing: it renders identically, and the only difference is a line of
provenance nobody reads.

So the provenance is read here. `example-proposal.json` is the frozen snapshot
the PDF was rendered from, written by the same script in the same run, which is
what makes this checkable rather than a matter of trusting whoever ran it.

Offline: this reads two committed files. Regenerating them is deliberately not
part of any suite - see `scripts/regenerate_sample_output.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

#: apps/api/tests/unit/ -> repo root.
SAMPLE = Path(__file__).resolve().parents[4] / "sample-output"
PDF = SAMPLE / "example-proposal.pdf"
SNAPSHOT = SAMPLE / "example-proposal.json"


@pytest.fixture(scope="module")
def snapshot() -> dict:
    assert SNAPSHOT.is_file(), (
        f"{SNAPSHOT} is missing. Regenerate it with "
        "scripts/regenerate_sample_output.py, which requires live PVGIS."
    )
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def test_the_sample_pdf_exists_and_is_a_pdf() -> None:
    assert PDF.is_file(), f"{PDF} is missing"
    assert PDF.read_bytes()[:5] == b"%PDF-"
    assert PDF.stat().st_size > 20_000, "suspiciously small for a full proposal"


def test_the_sample_is_backed_by_a_live_retrieval(snapshot) -> None:
    """The assertion this file exists for."""
    from app.integrations.pvgis import classify_endpoint

    provenance = snapshot["energy"]["pvgis"]

    assert provenance["source"] == "live", (
        f"the committed sample reports source={provenance['source']!r}. "
        "It was generated against a replay endpoint; regenerate it with "
        "scripts/regenerate_sample_output.py against the real PVGIS."
    )

    trust = classify_endpoint(provenance["endpoint"])
    assert trust.is_trusted, f"the sample's endpoint is not trusted: {trust.reason}"
    assert provenance["origin"] == "https://re.jrc.ec.europa.eu"
    assert provenance["apiVersion"] == "v5_3"


def test_every_facet_in_the_sample_was_probed(snapshot) -> None:
    provenance = snapshot["energy"]["pvgis"]
    probes = {p["facetId"]: p for p in provenance["probes"]}

    assert set(probes) == {"facet_n", "facet_s", "facet_e", "facet_w"}
    for probe in probes.values():
        assert probe["specificYieldKwhPerKwp"] > 0
        assert len(probe["monthlySpecificYieldKwhPerKwp"]) == 12
        assert probe["retrievedAt"]


def test_the_sample_agrees_with_the_southern_hemisphere(snapshot) -> None:
    """A sanity check on the live figures themselves, not just their label.

    The case roof is in Cape Town, so its north-facing plane must out-yield its
    south-facing one. If that ever inverts, either the aspect convention has
    been mixed up or the sample was generated for somewhere else.
    """
    yields = {
        p["facetId"]: p["specificYieldKwhPerKwp"] for p in snapshot["energy"]["pvgis"]["probes"]
    }
    assert yields["facet_n"] > yields["facet_s"]
    assert yields["facet_n"] > yields["facet_e"]
    assert yields["facet_n"] > yields["facet_w"]


def test_the_sample_carries_no_replay_or_fixture_note(snapshot) -> None:
    """The PDF's own assumptions must not disclaim its production figures."""
    assert snapshot["energy"]["dataSource"] == "live"
