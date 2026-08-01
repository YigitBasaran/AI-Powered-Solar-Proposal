"""The suite must reach the replay stub, not read captures off disk.

This file exists because the alternative failed silently. When `conftest.py`
stopped naming the PVGIS setting explicitly and left it to a default, a developer's own
`.env` supplied `fixture` instead - and the entire suite went on passing while
making **zero** HTTP requests. Every assertion still held, because the numbers
are the same either way; only the transport was different, and nothing looked at
the transport.

So: one test that looks at the transport. It is cheap, it runs early, and its
failure message says exactly what went wrong.
"""

from __future__ import annotations

CASE_COORD = "-34.04658242871865, 18.46491476666948"


def _analysed(client) -> str:
    project_id = client.post("/api/v1/projects").json()["projectId"]
    for message in (CASE_COORD, "1,150 kWh", "6 kWp"):
        client.post(f"/api/v1/projects/{project_id}/chat", json={"message": message})
    response = client.post(f"/api/v1/projects/{project_id}/run-analysis")
    assert response.status_code == 200, response.text
    return project_id


def test_the_suite_reaches_the_stub(client, stub_requests) -> None:
    """An analysis makes real HTTP requests, and they arrive here."""
    _analysed(client)

    assert stub_requests, (
        "an analysis made no HTTP request at all - the application is reading "
        "captures off disk instead of calling PVGIS. Check PVGIS_BASE_URL and "
        "PVGIS_BASE_URL in the test environment."
    )


def test_every_request_carries_the_case_geometry(client, stub_requests) -> None:
    """The stub validates the contract; this pins that it was actually asked."""
    _analysed(client)

    from app.services.roof import build_roof_model

    # Derived from the calibration, not transcribed: a literal set here would
    # need editing every time the roof is re-traced, and would pass vacuously
    # if it ever drifted to a superset of what is really requested.
    expected = {round(f.pvgis_aspect_deg) for f in build_roof_model().facets}
    aspects = {round(float(r["aspect"])) for r in stub_requests}
    assert aspects <= expected, f"unexpected aspects requested: {aspects}"

    for request in stub_requests:
        assert float(request["angle"]) == 25.0
        assert float(request["loss"]) == 14.0
        assert request["pvtechchoice"] == "crystSi"
        assert request["mountingplace"] == "building"


def test_the_configured_endpoint_is_the_stub(client) -> None:
    """Belt and braces: read it back from the application's own settings."""
    from app.core.config import get_settings

    base_url = get_settings().pvgis_base_url
    assert base_url.startswith("http://127.0.0.1:"), base_url
