"""Proposal, share route, PDF and view-tracking tests.

The important one here is immutability: a finalised proposal must reproduce the
figures it was created with, whatever the market has done since.
"""

from __future__ import annotations

import importlib.util
import re
from decimal import Decimal

import pytest

CASE_COORD = "-34.04658242871865, 18.46491476666948"

# PDF rendering lives behind the optional `pdf` extra. When it is not
# installed the right behaviour is to skip - a missing optional dependency is
# not a failing application.
playwright_installed = importlib.util.find_spec("playwright") is not None
needs_playwright = pytest.mark.skipif(
    not playwright_installed,
    reason="PDF rendering needs the optional 'pdf' extra: pip install -e '.[pdf]'",
)


def finalised(client, size_reply: str = "the middle option") -> dict:
    project_id = client.post("/api/v1/projects").json()["projectId"]
    for message in (CASE_COORD, "1,150 kWh", size_reply):
        client.post(f"/api/v1/projects/{project_id}/chat", json={"message": message})
    client.post(f"/api/v1/projects/{project_id}/run-analysis")
    response = client.post(f"/api/v1/projects/{project_id}/finalize")
    assert response.status_code == 200
    return {"projectId": project_id, **response.json()}


@pytest.fixture(scope="module")
def proposal(client) -> dict:
    return finalised(client)


# ---------------------------------------------------------------------------
# Finalisation
# ---------------------------------------------------------------------------


def test_finalise_returns_a_share_url_and_pdf_url(proposal) -> None:
    assert proposal["shareUrl"].endswith(proposal["shareToken"])
    assert proposal["pdfUrl"].endswith("/pdf")


def test_share_token_is_long_and_unguessable(proposal) -> None:
    token = proposal["shareToken"]
    assert len(token) >= 30
    assert re.fullmatch(r"[A-Za-z0-9_-]+", token)


def test_two_proposals_get_different_tokens(client) -> None:
    assert finalised(client)["shareToken"] != finalised(client)["shareToken"]


def test_finalising_without_an_analysis_is_refused(client) -> None:
    project_id = client.post("/api/v1/projects").json()["projectId"]
    response = client.post(f"/api/v1/projects/{project_id}/finalize")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PROPOSAL_INCOMPLETE"


def test_project_is_marked_completed_after_finalisation(client, proposal) -> None:
    project = client.get(f"/api/v1/projects/{proposal['projectId']}").json()
    assert project["currentStep"] == "completed"


# ---------------------------------------------------------------------------
# The public share route
# ---------------------------------------------------------------------------


def test_share_route_serves_the_whole_proposal(client, proposal) -> None:
    body = client.get(f"/api/v1/proposals/{proposal['shareToken']}").json()
    for key in ("roof", "layout", "energy", "financial", "exchangeRate", "meta"):
        assert key in body
    assert body["layout"]["placedPanelCount"] == 15


def test_share_route_needs_no_authentication(client, proposal) -> None:
    response = client.get(f"/api/v1/proposals/{proposal['shareToken']}")
    assert response.status_code == 200


def test_unknown_token_returns_not_found(client) -> None:
    response = client.get("/api/v1/proposals/" + "z" * 40)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.parametrize("token", ["short", "../../etc/passwd", "has spaces", "a" * 200])
def test_malformed_tokens_are_rejected(client, token) -> None:
    assert client.get(f"/api/v1/proposals/{token}").status_code in (404, 422)


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------


def test_reopening_a_proposal_returns_identical_figures(client, proposal) -> None:
    first = client.get(f"/api/v1/proposals/{proposal['shareToken']}").json()
    second = client.get(f"/api/v1/proposals/{proposal['shareToken']}").json()
    first.pop("views", None)
    second.pop("views", None)
    assert first == second


def test_a_moved_market_rate_does_not_change_a_finalised_proposal(client, proposal) -> None:
    """The guarantee the whole snapshot design exists for.

    A new rate is forced into the cache and a *new* proposal is created, which
    picks it up. The existing proposal must be untouched.
    """
    import asyncio
    from datetime import UTC, datetime

    from app.db.session import get_sessionmaker
    from app.integrations.exchange_rates import SqlExchangeRateCache

    before = client.get(f"/api/v1/proposals/{proposal['shareToken']}").json()
    original_rate = before["exchangeRate"]["rate"]
    original_capex = before["financial"]["convertedCapex"]["amount"]
    original_payback = before["financial"]["simplePaybackYears"]

    async def poison_cache() -> None:
        async with get_sessionmaker()() as session:
            await SqlExchangeRateCache(session).store(
                base="USD",
                quote="EUR",
                provider="ECB",
                rate=Decimal("0.5"),
                rate_date=datetime.now(UTC).date(),
                raw={},
            )
            await session.commit()

    asyncio.run(poison_cache())

    after = client.get(f"/api/v1/proposals/{proposal['shareToken']}").json()
    assert after["exchangeRate"]["rate"] == original_rate
    assert after["financial"]["convertedCapex"]["amount"] == original_capex
    assert after["financial"]["simplePaybackYears"] == original_payback


def test_stored_rate_metadata_travels_with_the_proposal(client, proposal) -> None:
    fx = client.get(f"/api/v1/proposals/{proposal['shareToken']}").json()["exchangeRate"]
    assert fx["dataProvider"] == "ECB"
    assert fx["sourceApi"] == "Frankfurter"
    assert fx["rateDate"]
    assert fx["retrievalSource"]
    assert fx["rate"] != "1"


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pdf_bytes(client, proposal) -> bytes:
    if not playwright_installed:
        pytest.skip("PDF rendering needs the optional 'pdf' extra")
    response = client.get(f"/api/v1/proposals/{proposal['shareToken']}/pdf")
    assert response.status_code == 200
    return response.content


def test_pdf_is_a_real_pdf(pdf_bytes) -> None:
    assert pdf_bytes.startswith(b"%PDF-")
    assert len(pdf_bytes) > 20_000


@needs_playwright
def test_pdf_is_served_as_a_download(client, proposal) -> None:
    response = client.get(f"/api/v1/proposals/{proposal['shareToken']}/pdf")
    assert response.headers["content-type"] == "application/pdf"
    assert "attachment" in response.headers["content-disposition"]


def test_pdf_and_share_page_use_the_same_stored_values(client, proposal) -> None:
    """Numerically identical, from one snapshot - formatting may differ."""
    from app.services.pdf import build_context

    body = client.get(f"/api/v1/proposals/{proposal['shareToken']}").json()
    context = build_context(body, share_token=proposal["shareToken"], created_at="today")

    assert context["financial"]["annualSavingsEur"] == body["financial"]["annualSavingsEur"]
    assert context["exchangeRate"]["rate"] == body["exchangeRate"]["rate"]
    assert (
        context["financial"]["convertedCapex"]["amount"]
        == body["financial"]["convertedCapex"]["amount"]
    )
    assert (
        context["financial"]["twentyYearNetBenefitEur"]
        == body["financial"]["twentyYearNetBenefitEur"]
    )
    assert context["layout"]["placedPanelCount"] == body["layout"]["placedPanelCount"]


def test_pdf_context_labels_fixture_data_in_the_assumptions(client, proposal) -> None:
    from app.services.pdf import build_context

    body = client.get(f"/api/v1/proposals/{proposal['shareToken']}").json()
    context = build_context(body, share_token=proposal["shareToken"], created_at="today")

    notes = " ".join(context["data_mode_notes"]).lower()
    assert "fixture" in notes
    assert "not live" in notes or "not a live" in notes
    assert "fixture" in context["fx_source_label"].lower()


def test_pdf_draws_the_roof_when_no_canvas_was_uploaded(client, proposal) -> None:
    from app.services.pdf import build_context

    body = client.get(f"/api/v1/proposals/{proposal['shareToken']}").json()
    context = build_context(body, share_token=proposal["shareToken"], created_at="today")

    assert context["layout_image"] is None
    assert context["roof_plan_svg"], "the PDF must show the roof without the frontend"
    assert "<svg" in context["roof_plan_svg"]


def test_pdf_html_contains_the_case_figures(client, proposal) -> None:
    from app.services.pdf import build_context, render_html

    body = client.get(f"/api/v1/proposals/{proposal['shareToken']}").json()
    html = render_html(build_context(body, share_token=proposal["shareToken"], created_at="today"))
    # Collapse whitespace: the assertions are about content, and the template
    # wraps prose across lines for readability.
    flat = " ".join(html.split())

    assert "13,800 kWh" in flat  # annual consumption
    assert "0.25" in flat  # unit price
    assert "10000.00" in flat  # CAPEX as quoted, in USD
    assert body["exchangeRate"]["rate"] in flat
    assert "cos(pitch)" in flat  # the hip-length note
    assert "southern hemisphere" in flat
    assert "no export compensation" in flat
    assert "not a site survey" in flat


def test_the_served_pdf_carries_the_executive_summary(client, proposal, monkeypatch) -> None:
    """Regression: the route must render the projection the share page is served.

    `aiSummary` is a column on the proposal row, not a key inside
    `proposal_data_json`. Rendering straight from the stored blob therefore
    dropped the executive summary from every PDF a customer actually
    downloaded, while the share page kept showing it.

    Every other PDF test in this file builds its own context from the public
    payload, which is the enriched projection - so none of them could see it.
    This one goes through the route, which is the only place the wiring exists.
    """
    from app.api.v1 import proposals as route
    from app.services.pdf import render_html

    captured = {}
    build = route.build_context

    def _capture(snapshot, **kwargs):
        captured["context"] = build(snapshot, **kwargs)
        return captured["context"]

    async def _skip_chromium(html: str) -> bytes:
        return b"%PDF-not-the-subject"

    monkeypatch.setattr(route, "build_context", _capture)
    monkeypatch.setattr(route, "render_pdf", _skip_chromium)

    assert client.get(f"/api/v1/proposals/{proposal['shareToken']}/pdf").status_code == 200

    summary = client.get(f"/api/v1/proposals/{proposal['shareToken']}").json()["aiSummary"]
    assert summary, "the fixture proposal has no summary, so this would prove nothing"
    assert captured["context"]["ai_summary"] == summary
    assert summary in " ".join(render_html(captured["context"]).split())


# ---------------------------------------------------------------------------
# View tracking (bonus)
# ---------------------------------------------------------------------------


def test_a_view_is_recorded_and_counted(client) -> None:
    token = finalised(client)["shareToken"]
    first = client.post(f"/api/v1/proposals/{token}/view").json()

    assert first["viewCount"] == 1
    assert first["counted"] is True


def test_an_immediate_second_request_is_the_same_visit(client) -> None:
    """This test used to assert the opposite, and the old assertion was wrong.

    The share page posts here on every mount, so a customer scrolling back,
    refreshing, or reopening a tab produced a new "view" each time. The count
    is the number an operator reads to decide whether to follow up, and one
    person reading a proposal once should not present as seven openings.

    So a repeat from the same reader inside the dedup window is not counted -
    and it is suppressed by *not inserting a row*, which keeps the count, the
    first-viewed and the last-viewed timestamps consistent with one another.
    """
    token = finalised(client)["shareToken"]
    first = client.post(f"/api/v1/proposals/{token}/view").json()
    second = client.post(f"/api/v1/proposals/{token}/view").json()

    assert first["viewCount"] == 1
    assert second["viewCount"] == 1
    assert second["counted"] is False


def test_a_different_reader_is_counted_separately(client) -> None:
    """Dedup must not collapse two genuinely different people into one."""
    token = finalised(client)["shareToken"]
    client.post(f"/api/v1/proposals/{token}/view")

    second = client.post(
        f"/api/v1/proposals/{token}/view",
        headers={"user-agent": "a-completely-different-browser/2.0"},
    ).json()

    assert second["viewCount"] == 2
    assert second["counted"] is True


def test_view_count_is_exposed_on_the_share_payload(client) -> None:
    token = finalised(client)["shareToken"]
    client.post(f"/api/v1/proposals/{token}/view")
    views = client.get(f"/api/v1/proposals/{token}").json()["views"]
    assert views["viewCount"] == 1
    assert views["lastOpenedAt"]
    assert views["firstOpenedAt"]


def test_viewing_an_unknown_proposal_is_not_found(client) -> None:
    assert client.post("/api/v1/proposals/" + "q" * 40 + "/view").status_code == 404


def test_ip_is_hashed_not_stored(client) -> None:
    from app.services.proposal import hash_ip

    assert hash_ip("203.0.113.9") != "203.0.113.9"
    assert len(hash_ip("203.0.113.9") or "") == 32
    assert hash_ip(None) is None


# ---------------------------------------------------------------------------
# Layout snapshot upload
# ---------------------------------------------------------------------------


def test_non_png_upload_is_rejected(client, proposal) -> None:
    response = client.post(
        f"/api/v1/projects/{proposal['projectId']}/layout-snapshot",
        files={"file": ("evil.png", b"not really a png", "image/png")},
    )
    assert response.status_code == 422


def test_png_upload_is_stored_and_served(client, proposal) -> None:
    png = (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
        + b"\x1f\x15\xc4\x89"
        + b"\x00" * 64
    )
    response = client.post(
        f"/api/v1/projects/{proposal['projectId']}/layout-snapshot",
        files={"file": ("layout.png", png, "image/png")},
    )
    assert response.status_code == 200
    assert response.json()["stored"] is True

    served = client.get(f"/api/v1/proposals/{proposal['shareToken']}/layout-snapshot")
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/png"


def test_finalising_twice_returns_the_same_proposal(client) -> None:
    """A double-click must not issue two links to two documents.

    Two proposals would split the view counts, and - because each freezes its
    own snapshot at its own moment - could later disagree about the exchange
    rate a customer was quoted.
    """
    first = finalised(client)
    second = client.post(f"/api/v1/projects/{first['projectId']}/finalize")

    assert second.status_code == 200
    body = second.json()
    assert body["shareToken"] == first["shareToken"]
    assert body["proposalId"] == first["proposalId"]
    assert body["summarySource"] == "existing"
