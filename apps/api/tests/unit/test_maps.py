"""Google Static Maps client tests.

**These do not prove the live integration works.** No API key was available, so
the client has never received a real Google response. What they do prove is
that the request matches the documented Static Maps contract, that the key
never leaves the server, and that a bad response is rejected rather than
rendered — which is the most that can be established without credentials.

Recorded honestly as such in `docs/known-limitations.md`.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.api.v1.maps import GOOGLE_STATIC_URL, satellite
from app.core.config import MapsMode, get_settings
from app.core.errors import MapsUnavailableError

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 4096


@pytest.fixture
def live_settings():
    return get_settings().model_copy(
        update={"maps_mode": MapsMode.LIVE, "google_maps_api_key": "TEST-KEY-123"}
    )


@respx.mock
async def test_request_matches_the_documented_static_maps_contract(live_settings) -> None:
    route = respx.get(GOOGLE_STATIC_URL).mock(
        return_value=httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
    )
    await satellite(live_settings)

    params = route.calls.last.request.url.params
    assert params["center"] == "-34.04658242871865,18.46491476666948"
    assert params["zoom"] == "20"
    assert params["size"] == "640x640"
    assert params["scale"] == "2"
    assert params["maptype"] == "satellite"
    assert params["key"] == "TEST-KEY-123"


@respx.mock
async def test_the_centre_is_the_resolved_not_the_raw_coordinate(live_settings) -> None:
    """The brief's positive latitude is open sea; it must never be requested."""
    route = respx.get(GOOGLE_STATIC_URL).mock(
        return_value=httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
    )
    await satellite(live_settings)
    assert route.calls.last.request.url.params["center"].startswith("-34.")


@respx.mock
async def test_the_api_key_never_reaches_the_client(live_settings) -> None:
    respx.get(GOOGLE_STATIC_URL).mock(
        return_value=httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
    )
    response = await satellite(live_settings)
    assert b"TEST-KEY-123" not in response.body
    assert "TEST-KEY-123" not in str(response.headers)
    assert response.headers["X-Image-Source"] == "live"


async def test_live_mode_without_a_key_fails_loudly() -> None:
    keyless = get_settings().model_copy(
        update={"maps_mode": MapsMode.LIVE, "google_maps_api_key": ""}
    )
    with pytest.raises(MapsUnavailableError, match="GOOGLE_MAPS_API_KEY"):
        await satellite(keyless)


@respx.mock
@pytest.mark.parametrize("status", [400, 403, 429, 500, 502])
async def test_an_error_status_is_rejected(live_settings, status: int) -> None:
    respx.get(GOOGLE_STATIC_URL).mock(return_value=httpx.Response(status))
    with pytest.raises(MapsUnavailableError):
        await satellite(live_settings)


@respx.mock
async def test_a_non_image_response_is_rejected(live_settings) -> None:
    """Google returns an HTML error page for some failures; it is not a map."""
    respx.get(GOOGLE_STATIC_URL).mock(
        return_value=httpx.Response(
            200, text="<html>error</html>", headers={"content-type": "text/html"}
        )
    )
    with pytest.raises(MapsUnavailableError, match="content-type"):
        await satellite(live_settings)


@respx.mock
async def test_a_suspiciously_small_image_is_rejected(live_settings) -> None:
    respx.get(GOOGLE_STATIC_URL).mock(
        return_value=httpx.Response(200, content=b"tiny", headers={"content-type": "image/png"})
    )
    with pytest.raises(MapsUnavailableError, match="small"):
        await satellite(live_settings)


@respx.mock
async def test_a_transport_failure_is_rejected(live_settings) -> None:
    respx.get(GOOGLE_STATIC_URL).mock(side_effect=httpx.ConnectError("no route"))
    with pytest.raises(MapsUnavailableError, match="unreachable"):
        await satellite(live_settings)


async def test_fixture_mode_makes_no_outbound_request() -> None:
    fixture_settings = get_settings().model_copy(update={"maps_mode": MapsMode.FIXTURE})
    with respx.mock:
        route = respx.get(GOOGLE_STATIC_URL)
        response = await satellite(fixture_settings)
        assert not route.called
    assert response.headers["X-Image-Source"] == "fixture"
    assert response.media_type == "image/png"


def test_the_raster_configuration_matches_what_is_published() -> None:
    """The client and /maps/config must describe the same raster."""
    cfg = get_settings().satellite_image_config
    assert cfg.source_width_px == cfg.requested_width_px * cfg.scale == 1280
    assert cfg.ground_m_per_source_px == pytest.approx(0.06185, abs=1e-5)
