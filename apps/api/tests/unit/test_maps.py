"""Google Static Maps client tests.

These prove the request matches the documented Static Maps contract, that the
key never leaves the server, and that a bad response is rejected rather than
rendered. They run against a mocked transport, so they do **not** prove that the
imagery Google returns is the imagery the roof was calibrated on - that is what
the perceptual-hash check and the `@live` alignment test are for.

There is no fixture branch to test any more: imagery is always fetched over
HTTP, and an offline test points `GOOGLE_STATIC_MAPS_BASE_URL` at a stub.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.api.v1.maps import build_request_params, satellite
from app.core.config import get_settings
from app.core.errors import MapsUnavailableError

GOOGLE_URL = "https://maps.googleapis.com/maps/api/staticmap"


def _png(width: int = 64, height: int = 64) -> bytes:
    """A real, decodable PNG.

    It has to actually decode now: the response is perceptually hashed to check
    it is the imagery the roof was traced on, so a handful of magic bytes is no
    longer a usable stand-in for a raster.
    """
    import io

    import numpy as np
    from PIL import Image

    # Noise, not flat colour: a solid image compresses to a few hundred bytes
    # and trips the "suspiciously small" guard, which is a real guard worth
    # keeping. Seeded, so the bytes and the perceptual hash are reproducible.
    rng = np.random.default_rng(20260730)
    pixels = rng.integers(0, 255, size=(height, width, 3), dtype=np.uint8)

    buffer = io.BytesIO()
    Image.fromarray(pixels, mode="RGB").save(buffer, format="PNG")
    return buffer.getvalue()


PNG = _png()


@pytest.fixture
def live_settings():
    return get_settings().model_copy(
        update={
            "google_static_maps_base_url": GOOGLE_URL,
            "google_maps_api_key": "TEST-KEY-123",
        }
    )


@respx.mock
async def test_request_matches_the_documented_static_maps_contract(live_settings) -> None:
    route = respx.get(GOOGLE_URL).mock(
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
    route = respx.get(GOOGLE_URL).mock(
        return_value=httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
    )
    await satellite(live_settings)
    assert route.calls.last.request.url.params["center"].startswith("-34.")


@respx.mock
async def test_the_api_key_never_reaches_the_client(live_settings) -> None:
    respx.get(GOOGLE_URL).mock(
        return_value=httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
    )
    response = await satellite(live_settings)
    assert b"TEST-KEY-123" not in response.body
    assert "TEST-KEY-123" not in str(response.headers)
    assert response.headers["X-Image-Source"] == "live"


async def test_calling_google_without_a_key_fails_loudly() -> None:
    keyless = get_settings().model_copy(
        update={"google_static_maps_base_url": GOOGLE_URL, "google_maps_api_key": ""}
    )
    with pytest.raises(MapsUnavailableError, match="GOOGLE_MAPS_API_KEY"):
        await satellite(keyless)


@respx.mock
@pytest.mark.parametrize("status", [400, 403, 429, 500, 502])
async def test_an_error_status_is_rejected(live_settings, status: int) -> None:
    respx.get(GOOGLE_URL).mock(return_value=httpx.Response(status))
    with pytest.raises(MapsUnavailableError):
        await satellite(live_settings)


@respx.mock
async def test_a_non_image_response_is_rejected(live_settings) -> None:
    """Google returns an HTML error page for some failures; it is not a map."""
    respx.get(GOOGLE_URL).mock(
        return_value=httpx.Response(
            200, text="<html>error</html>", headers={"content-type": "text/html"}
        )
    )
    with pytest.raises(MapsUnavailableError, match="content-type"):
        await satellite(live_settings)


@respx.mock
async def test_a_suspiciously_small_image_is_rejected(live_settings) -> None:
    respx.get(GOOGLE_URL).mock(
        return_value=httpx.Response(200, content=b"tiny", headers={"content-type": "image/png"})
    )
    with pytest.raises(MapsUnavailableError, match="small"):
        await satellite(live_settings)


@respx.mock
async def test_a_transport_failure_is_rejected(live_settings) -> None:
    respx.get(GOOGLE_URL).mock(side_effect=httpx.ConnectError("no route"))
    with pytest.raises(MapsUnavailableError, match="unreachable"):
        await satellite(live_settings)


@respx.mock
async def test_imagery_is_always_fetched_and_never_substituted(live_settings) -> None:
    """The inverse of the test this replaces.

    There used to be a fixture branch that served a committed raster with no
    outbound request. That is exactly how a correct-looking overlay came to be
    drawn over the wrong picture, so the branch is gone: every request for
    imagery is a real request, and a failed one is an error rather than a
    silently substituted file.
    """
    route = respx.get(GOOGLE_URL).mock(
        return_value=httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
    )
    response = await satellite(live_settings)

    assert route.called, "imagery was served without asking anyone for it"
    assert response.media_type == "image/png"


@respx.mock
async def test_a_failed_request_yields_an_error_not_a_fixture(live_settings) -> None:
    respx.get(GOOGLE_URL).mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(MapsUnavailableError):
        await satellite(live_settings)


@respx.mock
async def test_a_body_that_does_not_decode_is_rejected(live_settings) -> None:
    """`content-type: image/png` on a truncated transfer keeps the header."""
    respx.get(GOOGLE_URL).mock(
        return_value=httpx.Response(
            200,
            content=b"\x89PNG\r\n\x1a\n" + b"\x00" * 4096,
            headers={"content-type": "image/png"},
        )
    )
    with pytest.raises(MapsUnavailableError, match="decoded"):
        await satellite(live_settings)


def test_the_key_is_the_only_secret_and_is_added_in_one_place() -> None:
    keyed = get_settings().model_copy(update={"google_maps_api_key": "SECRET"})
    assert build_request_params(keyed)["key"] == "SECRET"

    keyless = get_settings().model_copy(update={"google_maps_api_key": ""})
    assert "key" not in build_request_params(keyless), (
        "an empty key must be omitted, not sent as an empty parameter"
    )


def test_the_raster_configuration_matches_what_is_published() -> None:
    """The client and /maps/config must describe the same raster."""
    cfg = get_settings().satellite_image_config
    assert cfg.source_width_px == cfg.requested_width_px * cfg.scale == 1280
    assert cfg.ground_m_per_source_px == pytest.approx(0.06185, abs=1e-5)
