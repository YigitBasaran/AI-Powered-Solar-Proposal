"""The project tariff, from the message that sets it to the frozen PDF.

A tariff is unusual among this system's inputs: it changes what the customer is
*told they will save* without changing anything physical. Nothing about the
roof, the panel layout or the modelled production depends on the price of
electricity, so a tariff change that quietly re-ran any of those would be
spending money and time to arrive back where it started - and a tariff change
that *failed* to move the savings would quote a payback for a price the
customer does not pay.

Both directions are asserted here, and so is the boundary that matters most: an
issued proposal is immutable, so a later tariff change must leave it exactly as
it was sent.
"""

from __future__ import annotations

import pytest

CASE_COORD = "-34.04658242871865, 18.46491476666948"


def _say(client, project_id: str, message: str) -> dict:
    response = client.post(f"/api/v1/projects/{project_id}/chat", json={"message": message})
    assert response.status_code == 200, response.text
    return response.json()


def _project(client, project_id: str) -> dict:
    return client.get(f"/api/v1/projects/{project_id}").json()


def _analysed(client, *, tariff: str | None = None) -> str:
    project_id = client.post("/api/v1/projects").json()["projectId"]
    for message in (CASE_COORD, "1,150 kWh", "6 kWp"):
        _say(client, project_id, message)
    if tariff is not None:
        _say(client, project_id, tariff)
    assert client.post(f"/api/v1/projects/{project_id}/run-analysis").status_code == 200
    return project_id


def _share_token(client, project_id: str) -> str:
    import asyncio

    from sqlalchemy import select

    from app.models.tables import Proposal

    async def _load() -> str:
        from app.db.session import get_sessionmaker

        async with get_sessionmaker()() as session:
            row = (
                await session.execute(select(Proposal).where(Proposal.project_id == project_id))
            ).scalar_one()
            return row.share_token

    return asyncio.run(_load())


# ---------------------------------------------------------------------------
# It is stored, and it comes back
# ---------------------------------------------------------------------------


def test_the_tariff_persists_and_is_returned_by_the_project_api(client) -> None:
    """Reopening the project must show the price the customer set, not the default."""
    project_id = _analysed(client, tariff="My tariff is actually 0.31 EUR/kWh")

    reopened = _project(client, project_id)
    assert reopened["electricityTariffEurPerKwh"] == pytest.approx(0.31)


def test_an_untouched_project_reports_no_tariff_and_uses_the_case_default(client) -> None:
    """Null means "the configured rate", and the API says null rather than guessing."""
    from app.core.config import get_settings

    project_id = _analysed(client)
    project = _project(client, project_id)

    assert project["electricityTariffEurPerKwh"] is None
    assert float(project["analysis"]["financial"]["electricityPriceEurPerKwh"]) == pytest.approx(
        get_settings().case_electricity_price
    )


def test_the_stored_tariff_reaches_the_financial_calculation(client) -> None:
    project_id = _analysed(client, tariff="Change my tariff to 0.40 EUR/kWh")

    financial = _project(client, project_id)["analysis"]["financial"]
    assert float(financial["electricityPriceEurPerKwh"]) == pytest.approx(0.40)

    # And it is genuinely used, not merely echoed: savings are covered energy
    # times price, so the arithmetic has to close.
    covered = float(financial["coveredEnergyKwh"])
    assert float(financial["annualSavingsEur"]) == pytest.approx(covered * 0.40, rel=1e-3)


# ---------------------------------------------------------------------------
# A tariff change moves the money and nothing else
# ---------------------------------------------------------------------------


def test_a_tariff_change_recalculates_the_financials(client) -> None:
    project_id = _analysed(client)
    before = _project(client, project_id)["analysis"]["financial"]

    _say(client, project_id, "My tariff is actually 0.31 EUR/kWh")
    after = _project(client, project_id)["analysis"]["financial"]

    assert float(after["annualSavingsEur"]) > float(before["annualSavingsEur"])
    assert float(after["simplePaybackYears"]) < float(before["simplePaybackYears"])
    assert float(after["twentyYearNetBenefitEur"]) > float(before["twentyYearNetBenefitEur"])


def test_a_tariff_change_leaves_the_roof_layout_and_production_untouched(client) -> None:
    """The physical half of the analysis has no dependency on price."""
    project_id = _analysed(client)
    before = _project(client, project_id)["analysis"]

    _say(client, project_id, "My tariff is actually 0.31 EUR/kWh")
    after = _project(client, project_id)["analysis"]

    assert after["roof"] == before["roof"]
    assert after["layout"] == before["layout"]
    assert after["energy"] == before["energy"], (
        "the price of electricity moved the modelled production"
    )


def test_a_tariff_change_makes_no_pvgis_request(client, stub_requests) -> None:
    """Production is already known; asking again would cost money for nothing."""
    project_id = _analysed(client)
    stub_requests.clear()

    _say(client, project_id, "My tariff is actually 0.31 EUR/kWh")

    assert stub_requests == [], f"a tariff change called PVGIS {len(stub_requests)} times"


def test_a_tariff_change_fetches_no_imagery(client, pvgis_stub) -> None:
    """The roof was measured once; a price cannot have moved it.

    Counted at the stub, because the imagery request is a real HTTP call in
    every environment - there is no mode that makes it a local read.
    """
    _, stub = pvgis_stub
    project_id = _analysed(client)

    stub.requests.clear()
    _say(client, project_id, "My tariff is actually 0.31 EUR/kWh")

    imagery = [r for r in stub.requests if r.get("endpoint") == "staticmap"]
    assert imagery == [], f"a tariff change fetched imagery {len(imagery)} times"


def test_a_tariff_change_does_not_rebuild_the_roof_model(client, monkeypatch) -> None:
    """Asserted on the call, not on the output.

    Comparing the resulting geometry would pass even if the roof were rebuilt,
    because rebuilding it is deterministic and lands byte-identical. The point
    is that the work is not done at all.
    """
    from app.services import analysis as analysis_service

    project_id = _analysed(client)

    calls: list[str] = []
    original = analysis_service.build_roof_model

    def _counted(*args, **kwargs):
        calls.append("build_roof_model")
        return original(*args, **kwargs)

    monkeypatch.setattr(analysis_service, "build_roof_model", _counted)
    _say(client, project_id, "My tariff is actually 0.31 EUR/kWh")

    assert calls == [], "a tariff change rebuilt the roof model"


def test_a_tariff_change_does_not_regenerate_the_panel_layout(client, monkeypatch) -> None:
    from app.services import analysis as analysis_service

    project_id = _analysed(client)

    calls: list[str] = []
    original = analysis_service.generate_layout

    def _counted(*args, **kwargs):
        calls.append("generate_layout")
        return original(*args, **kwargs)

    monkeypatch.setattr(analysis_service, "generate_layout", _counted)
    _say(client, project_id, "My tariff is actually 0.31 EUR/kWh")

    assert calls == [], "a tariff change regenerated the panel layout"


# ---------------------------------------------------------------------------
# The proposal freezes it
# ---------------------------------------------------------------------------


def test_finalising_freezes_the_tariff_in_the_snapshot(client) -> None:
    project_id = _analysed(client, tariff="My tariff is actually 0.31 EUR/kWh")
    finalised = client.post(f"/api/v1/projects/{project_id}/finalize")
    assert finalised.status_code == 200, finalised.text

    served = client.get(f"/api/v1/proposals/{_share_token(client, project_id)}").json()
    assert float(served["financial"]["electricityPriceEurPerKwh"]) == pytest.approx(0.31)


def test_the_pdf_shows_the_project_tariff(client) -> None:
    project_id = _analysed(client, tariff="My tariff is actually 0.31 EUR/kWh")
    assert client.post(f"/api/v1/projects/{project_id}/finalize").status_code == 200

    token = _share_token(client, project_id)
    served = client.get(f"/api/v1/proposals/{token}").json()

    from app.core.config import get_settings
    from app.services.pdf import build_context, render_html

    html = render_html(
        build_context(
            served,
            share_token=token,
            created_at=served["createdAt"],
            settings=get_settings(),
        )
    )
    assert "0.31" in html, "the PDF does not show the tariff the customer set"
    # And the derived figure it labels, so the two cannot drift apart.
    assert f"{float(served['financial']['annualSavingsEur']):,.0f}" in html.replace(",", ",")


def test_a_later_tariff_change_does_not_alter_an_issued_proposal(client) -> None:
    """The immutability boundary, tested with the one input that is cheapest to change.

    A proposal is a document someone has already been sent. Recalculating it
    under a new price would silently restate what they were promised.
    """
    project_id = _analysed(client, tariff="My tariff is actually 0.31 EUR/kWh")
    assert client.post(f"/api/v1/projects/{project_id}/finalize").status_code == 200

    token = _share_token(client, project_id)
    issued = client.get(f"/api/v1/proposals/{token}").json()

    _say(client, project_id, "Change my tariff to 0.45 EUR/kWh")

    after = client.get(f"/api/v1/proposals/{token}").json()
    assert after["financial"] == issued["financial"], "the issued proposal was rewritten"
    assert float(after["financial"]["electricityPriceEurPerKwh"]) == pytest.approx(0.31)


def test_the_pdf_of_an_issued_proposal_is_unchanged_by_a_later_tariff(client) -> None:
    project_id = _analysed(client, tariff="My tariff is actually 0.31 EUR/kWh")
    assert client.post(f"/api/v1/projects/{project_id}/finalize").status_code == 200
    token = _share_token(client, project_id)

    from app.core.config import get_settings
    from app.services.pdf import build_context, render_html

    def _html() -> str:
        served = client.get(f"/api/v1/proposals/{token}").json()
        return render_html(
            build_context(
                served,
                share_token=token,
                created_at=served["createdAt"],
                settings=get_settings(),
            )
        )

    before = _html()
    _say(client, project_id, "Change my tariff to 0.45 EUR/kWh")

    assert _html() == before, "the issued PDF changed after the project moved on"
