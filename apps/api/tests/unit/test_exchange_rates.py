"""Exchange-rate tests.

The failure this suite exists to prevent is a silent USD/EUR parity
assumption. At the current rate that would understate the payback period by
about 12% while every number on screen still looked reasonable.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
import respx

from app.core.config import FxMode, get_settings
from app.core.errors import FxRateUnavailableError
from app.domain.models import ExchangeRateSource
from app.integrations.exchange_rates import (
    ExchangeRateService,
    InMemoryExchangeRateCache,
    parse_frankfurter_rate,
)

LIVE_PAYLOAD = {"date": "2026-07-24", "base": "USD", "quote": "EUR", "rate": 0.87897}
FX_URL = "https://api.frankfurter.dev/v2/rate/USD/EUR"


@pytest.fixture
def settings():
    return get_settings().model_copy(update={"fx_mode": FxMode.LIVE})


@pytest.fixture
def cache():
    return InMemoryExchangeRateCache()


# ---------------------------------------------------------------------------
# Payload validation
# ---------------------------------------------------------------------------


def test_valid_payload_parses_to_decimal_and_date() -> None:
    rate, rate_date = parse_frankfurter_rate(
        LIVE_PAYLOAD, expected_base="USD", expected_quote="EUR"
    )
    assert rate == Decimal("0.87897")
    assert isinstance(rate, Decimal)
    assert rate_date == date(2026, 7, 24)


def test_rate_is_parsed_without_binary_float_error() -> None:
    """Decimal(str(x)) rather than Decimal(x), so 0.87897 stays 0.87897."""
    rate, _ = parse_frankfurter_rate(LIVE_PAYLOAD, expected_base="USD", expected_quote="EUR")
    assert str(rate) == "0.87897"


@pytest.mark.parametrize(
    "payload",
    [
        {"date": "2026-07-24", "base": "USD", "quote": "EUR"},
        {"date": "2026-07-24", "base": "USD", "quote": "EUR", "rate": None},
        {"date": "2026-07-24", "base": "USD", "quote": "EUR", "rate": 0},
        {"date": "2026-07-24", "base": "USD", "quote": "EUR", "rate": -0.9},
        {"date": "2026-07-24", "base": "USD", "quote": "EUR", "rate": float("nan")},
        {"date": "2026-07-24", "base": "USD", "quote": "EUR", "rate": float("inf")},
        {"date": "2026-07-24", "base": "USD", "quote": "EUR", "rate": "abc"},
        {"date": "2026-07-24", "base": "USD", "quote": "EUR", "rate": True},
        {"date": "2026-07-24", "base": "GBP", "quote": "EUR", "rate": 0.9},
        {"date": "2026-07-24", "base": "USD", "quote": "GBP", "rate": 0.9},
        {"date": "not-a-date", "base": "USD", "quote": "EUR", "rate": 0.9},
        {"base": "USD", "quote": "EUR", "rate": 0.9},
        [],
        "nope",
    ],
)
def test_malformed_payloads_are_rejected(payload) -> None:
    with pytest.raises(FxRateUnavailableError):
        parse_frankfurter_rate(payload, expected_base="USD", expected_quote="EUR")


# ---------------------------------------------------------------------------
# Live retrieval
# ---------------------------------------------------------------------------


@respx.mock
async def test_live_rate_calls_the_right_endpoint_with_ecb_provider(settings, cache) -> None:
    route = respx.get(FX_URL).mock(return_value=httpx.Response(200, json=LIVE_PAYLOAD))

    result = await ExchangeRateService(settings, cache=cache).get_usd_to_eur_rate()

    assert route.called
    request = route.calls.last.request
    assert request.url.params["providers"] == "ECB"
    assert str(request.url).startswith(FX_URL)

    assert result.rate == Decimal("0.87897")
    assert result.rate_date == date(2026, 7, 24)
    assert result.base_currency == "USD"
    assert result.quote_currency == "EUR"
    assert result.data_provider == "ECB"
    assert result.retrieval_source is ExchangeRateSource.LIVE


@respx.mock
async def test_live_success_populates_the_cache(settings, cache) -> None:
    respx.get(FX_URL).mock(return_value=httpx.Response(200, json=LIVE_PAYLOAD))
    await ExchangeRateService(settings, cache=cache).get_usd_to_eur_rate()
    assert await cache.latest("USD", "EUR", "ECB") is not None


@respx.mock
async def test_non_json_content_type_is_rejected(settings, cache) -> None:
    respx.get(FX_URL).mock(
        return_value=httpx.Response(200, text="<html/>", headers={"content-type": "text/html"})
    )
    service = ExchangeRateService(
        settings.model_copy(update={"fx_fallback_enabled": False}), cache=cache
    )
    with pytest.raises(FxRateUnavailableError):
        await service.get_usd_to_eur_rate()


@respx.mock
async def test_http_error_without_fallback_raises(settings, cache) -> None:
    respx.get(FX_URL).mock(return_value=httpx.Response(503))
    service = ExchangeRateService(
        settings.model_copy(update={"fx_fallback_enabled": False}), cache=cache
    )
    with pytest.raises(FxRateUnavailableError):
        await service.get_usd_to_eur_rate()


@respx.mock
async def test_timeout_without_fallback_raises(settings, cache) -> None:
    respx.get(FX_URL).mock(side_effect=httpx.ConnectTimeout("timed out"))
    service = ExchangeRateService(
        settings.model_copy(update={"fx_fallback_enabled": False}), cache=cache
    )
    with pytest.raises(FxRateUnavailableError):
        await service.get_usd_to_eur_rate()


# ---------------------------------------------------------------------------
# Fallback order: live -> cache -> fixture. Never parity.
# ---------------------------------------------------------------------------


@respx.mock
async def test_falls_back_to_cache_and_labels_it(settings, cache) -> None:
    await cache.store(
        base="USD",
        quote="EUR",
        provider="ECB",
        rate=Decimal("0.9012"),
        rate_date=datetime.now(UTC).date(),
        raw={},
    )
    respx.get(FX_URL).mock(side_effect=httpx.ConnectTimeout("down"))

    result = await ExchangeRateService(settings, cache=cache).get_usd_to_eur_rate()

    assert result.rate == Decimal("0.9012")
    assert result.retrieval_source is ExchangeRateSource.LIVE_FALLBACK_CACHE
    assert not result.retrieval_source.is_live


@respx.mock
async def test_stale_cache_is_rejected_and_falls_through_to_fixture(settings, cache) -> None:
    stale = datetime.now(UTC).date() - timedelta(days=90)
    await cache.store(
        base="USD",
        quote="EUR",
        provider="ECB",
        rate=Decimal("0.5"),
        rate_date=stale,
        raw={},
    )
    respx.get(FX_URL).mock(side_effect=httpx.ConnectTimeout("down"))

    result = await ExchangeRateService(settings, cache=cache).get_usd_to_eur_rate()

    assert result.rate != Decimal("0.5"), "a 90-day-old rate must not be used"
    assert result.retrieval_source is ExchangeRateSource.LIVE_FALLBACK_FIXTURE
    assert result.retrieval_source.is_fixture


@respx.mock
async def test_falls_back_to_fixture_and_labels_it(settings, cache) -> None:
    respx.get(FX_URL).mock(side_effect=httpx.ConnectTimeout("down"))
    result = await ExchangeRateService(settings, cache=cache).get_usd_to_eur_rate()
    assert result.retrieval_source is ExchangeRateSource.LIVE_FALLBACK_FIXTURE
    assert result.rate == Decimal("0.87897")


async def test_fixture_mode_is_labelled_as_fixture(cache) -> None:
    fixture_settings = get_settings().model_copy(update={"fx_mode": FxMode.FIXTURE})
    result = await ExchangeRateService(fixture_settings, cache=cache).get_usd_to_eur_rate()
    assert result.retrieval_source is ExchangeRateSource.FIXTURE
    assert result.retrieval_source.is_fixture
    assert not result.retrieval_source.is_live


@respx.mock
async def test_no_path_ever_returns_parity(settings, cache) -> None:
    """The central guarantee of this module."""
    respx.get(FX_URL).mock(side_effect=httpx.ConnectTimeout("down"))
    result = await ExchangeRateService(settings, cache=cache).get_usd_to_eur_rate()
    assert result.rate != Decimal("1")
    assert result.rate != Decimal("1.0")


def test_settings_expose_no_hardcoded_rate_option() -> None:
    """A parity assumption must not be configurable into existence."""
    fields = set(type(get_settings()).model_fields)
    for forbidden in ("case_usd_eur_rate", "usd_eur_rate", "fx_rate", "fx_fixed_rate"):
        assert forbidden not in fields


def test_source_repository_contains_no_parity_literal() -> None:
    """Guards against a well-meaning `or 1.0` creeping in later."""
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "app"
    offenders = []
    for path in src.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for marker in ("USD_EUR_RATE", "usd_to_eur = 1.0", "rate = 1.0", "rate or 1"):
            if marker in text:
                offenders.append(f"{path.name}: {marker}")
    assert not offenders, offenders


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------


@respx.mock
async def test_a_market_move_does_not_alter_an_already_retrieved_rate(settings, cache) -> None:
    """A retrieved ExchangeRate is a value, not a live view."""
    respx.get(FX_URL).mock(return_value=httpx.Response(200, json=LIVE_PAYLOAD))
    first = await ExchangeRateService(settings, cache=cache).get_usd_to_eur_rate()
    snapshot = first.model_dump_json()

    respx.get(FX_URL).mock(
        return_value=httpx.Response(
            200,
            json={"date": "2026-08-01", "base": "USD", "quote": "EUR", "rate": 0.5},
        )
    )
    second = await ExchangeRateService(settings, cache=cache).get_usd_to_eur_rate()

    assert second.rate == Decimal("0.5")
    assert first.model_dump_json() == snapshot
    assert first.rate == Decimal("0.87897")
