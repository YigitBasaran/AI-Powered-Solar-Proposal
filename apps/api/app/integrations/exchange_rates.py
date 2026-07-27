"""USD to EUR reference rate, via Frankfurter with ECB as the data provider.

The case gives CAPEX in USD and the electricity price in EUR. Mixing them
directly would silently assume parity and misstate the payback period by
roughly 12% at current rates, so the conversion is explicit, validated and
recorded.

Three rules are enforced structurally rather than by convention:

* **Parity is never a fallback.** There is no configuration option for a fixed
  rate and no code path that defaults to 1.0. If live, cache and fixture all
  fail, the request fails loudly.
* **Fallback data is never presented as live.** Every rate carries the source
  it actually came from, which is surfaced in the UI and in the PDF's
  assumptions.
* **A finalised proposal never re-reads the rate.** The snapshot holds it, so
  reopening a proposal months later reproduces the same numbers.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import FxMode, Settings, get_settings
from app.core.errors import FxRateUnavailableError
from app.domain.models import ExchangeRate, ExchangeRateSource
from app.models.tables import ExchangeRateCache as ExchangeRateCacheRow

logger = logging.getLogger("solarvis.fx")


class ExchangeRateCache(Protocol):
    async def latest(
        self, base: str, quote: str, provider: str
    ) -> tuple[Decimal, date, dict[str, Any]] | None: ...

    async def store(
        self,
        *,
        base: str,
        quote: str,
        provider: str,
        rate: Decimal,
        rate_date: date,
        raw: dict[str, Any],
    ) -> None: ...


class InMemoryExchangeRateCache:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str, str], tuple[Decimal, date, dict[str, Any]]] = {}

    async def latest(
        self, base: str, quote: str, provider: str
    ) -> tuple[Decimal, date, dict[str, Any]] | None:
        return self._rows.get((base, quote, provider))

    async def store(
        self,
        *,
        base: str,
        quote: str,
        provider: str,
        rate: Decimal,
        rate_date: date,
        raw: dict[str, Any],
    ) -> None:
        existing = self._rows.get((base, quote, provider))
        if existing is None or rate_date >= existing[1]:
            self._rows[(base, quote, provider)] = (rate, rate_date, raw)


class SqlExchangeRateCache:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def latest(
        self, base: str, quote: str, provider: str
    ) -> tuple[Decimal, date, dict[str, Any]] | None:
        stmt = (
            select(ExchangeRateCacheRow)
            .where(
                ExchangeRateCacheRow.base_currency == base,
                ExchangeRateCacheRow.quote_currency == quote,
                ExchangeRateCacheRow.provider == provider,
            )
            .order_by(ExchangeRateCacheRow.rate_date.desc())
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        return Decimal(str(row.rate)), row.rate_date, dict(row.raw_response_json or {})

    async def store(
        self,
        *,
        base: str,
        quote: str,
        provider: str,
        rate: Decimal,
        rate_date: date,
        raw: dict[str, Any],
    ) -> None:
        stmt = select(ExchangeRateCacheRow).where(
            ExchangeRateCacheRow.base_currency == base,
            ExchangeRateCacheRow.quote_currency == quote,
            ExchangeRateCacheRow.provider == provider,
            ExchangeRateCacheRow.rate_date == rate_date,
        )
        if (await self._session.execute(stmt)).scalar_one_or_none() is not None:
            return
        self._session.add(
            ExchangeRateCacheRow(
                base_currency=base,
                quote_currency=quote,
                provider=provider,
                rate=rate,
                rate_date=rate_date,
                raw_response_json=raw,
            )
        )
        await self._session.flush()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def parse_frankfurter_rate(
    payload: Any, *, expected_base: str, expected_quote: str
) -> tuple[Decimal, date]:
    """Validate a Frankfurter v2 `/rate/{base}/{quote}` payload.

    Expected shape::

        {"date": "YYYY-MM-DD", "base": "USD", "quote": "EUR", "rate": 0.87897}

    Everything is checked. A malformed payload must fail rather than degrade
    into a plausible-looking number.
    """
    if not isinstance(payload, dict):
        raise FxRateUnavailableError("FX response was not a JSON object")

    base = str(payload.get("base", "")).upper()
    quote = str(payload.get("quote", "")).upper()
    if base != expected_base.upper():
        raise FxRateUnavailableError(f"FX response base is {base!r}, expected {expected_base!r}")
    if quote != expected_quote.upper():
        raise FxRateUnavailableError(f"FX response quote is {quote!r}, expected {expected_quote!r}")

    raw_rate = payload.get("rate")
    if raw_rate is None:
        raise FxRateUnavailableError("FX response has no rate")
    if isinstance(raw_rate, bool):
        raise FxRateUnavailableError("FX rate must be numeric")
    if isinstance(raw_rate, float) and (math.isnan(raw_rate) or math.isinf(raw_rate)):
        raise FxRateUnavailableError("FX rate is not finite")
    try:
        # Via str so a float literal does not pick up binary rounding.
        rate = Decimal(str(raw_rate))
    except (InvalidOperation, ValueError) as exc:
        raise FxRateUnavailableError(f"FX rate is not a number: {raw_rate!r}") from exc
    if not rate.is_finite():
        raise FxRateUnavailableError("FX rate is not finite")
    if rate <= 0:
        raise FxRateUnavailableError(f"FX rate must be positive, got {rate}")

    raw_date = payload.get("date")
    if not raw_date:
        raise FxRateUnavailableError("FX response has no date")
    try:
        rate_date = date.fromisoformat(str(raw_date))
    except ValueError as exc:
        raise FxRateUnavailableError(f"FX date is invalid: {raw_date!r}") from exc

    return rate, rate_date


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ExchangeRateService:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        cache: ExchangeRateCache | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._cache = cache or InMemoryExchangeRateCache()
        self._client = client

    @property
    def _fixture_path(self) -> Path:
        return self._settings.fixtures_dir / "exchange-rates" / "usd-eur-ecb.json"

    def _build(self, rate: Decimal, rate_date: date, source: ExchangeRateSource) -> ExchangeRate:
        s = self._settings
        return ExchangeRate(
            source_api=s.fx_provider.capitalize(),
            data_provider=s.fx_data_provider,
            rate_date=rate_date,
            base_currency=s.fx_base_currency,
            quote_currency=s.fx_quote_currency,
            rate=rate,
            retrieval_source=source,
            retrieved_at=datetime.now(UTC),
        )

    async def _fetch_live(self) -> tuple[Decimal, date, dict[str, Any]]:
        s = self._settings
        # Built internally from configuration: no user input reaches the URL.
        url = f"{s.fx_base_url}/rate/{s.fx_base_currency}/{s.fx_quote_currency}"
        params = {"providers": s.fx_data_provider}

        async def _do(client: httpx.AsyncClient) -> tuple[Decimal, date, dict[str, Any]]:
            try:
                response = await client.get(url, params=params, timeout=s.fx_timeout_seconds)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                raise FxRateUnavailableError(f"FX request failed: {type(exc).__name__}") from exc

            if response.status_code != 200:
                raise FxRateUnavailableError(
                    f"FX provider returned HTTP {response.status_code}",
                    details={"status": response.status_code},
                )

            content_type = response.headers.get("content-type", "")
            if "json" not in content_type.lower():
                raise FxRateUnavailableError(f"FX provider returned content-type {content_type!r}")

            try:
                payload = response.json()
            except (json.JSONDecodeError, ValueError) as exc:
                raise FxRateUnavailableError("FX response was not valid JSON") from exc

            rate, rate_date = parse_frankfurter_rate(
                payload,
                expected_base=s.fx_base_currency,
                expected_quote=s.fx_quote_currency,
            )
            return rate, rate_date, dict(payload)

        if self._client is not None:
            return await _do(self._client)
        async with httpx.AsyncClient() as client:
            return await _do(client)

    def _load_fixture(self) -> tuple[Decimal, date]:
        path = self._fixture_path
        if not path.is_file():
            raise FxRateUnavailableError(
                "No FX fixture available and no live or cached rate could be used. "
                "USD/EUR parity is never assumed, so the request cannot proceed.",
                details={"fixturePath": str(path)},
            )
        payload = json.loads(path.read_text(encoding="utf-8"))
        rate, rate_date = parse_frankfurter_rate(
            payload,
            expected_base=self._settings.fx_base_currency,
            expected_quote=self._settings.fx_quote_currency,
        )
        return rate, rate_date

    async def _cached(self) -> tuple[Decimal, date] | None:
        s = self._settings
        row = await self._cache.latest(s.fx_base_currency, s.fx_quote_currency, s.fx_data_provider)
        if row is None:
            return None
        rate, rate_date, _ = row
        age_days = (datetime.now(UTC).date() - rate_date).days
        if age_days > s.fx_max_cached_rate_age_days:
            logger.warning("cached FX rate is %d days old; rejecting as stale", age_days)
            return None
        return rate, rate_date

    async def get_usd_to_eur_rate(self) -> ExchangeRate:
        """Live, then cache, then labelled fixture. Never parity."""
        s = self._settings

        if s.fx_mode is FxMode.FIXTURE:
            rate, rate_date = self._load_fixture()
            logger.info("FX fixture rate %s dated %s", rate, rate_date)
            return self._build(rate, rate_date, ExchangeRateSource.FIXTURE)

        try:
            rate, rate_date, raw = await self._fetch_live()
        except FxRateUnavailableError as live_error:
            logger.warning("FX live retrieval failed: %s", live_error.message)

            if not s.fx_fallback_enabled:
                raise

            cached = await self._cached()
            if cached is not None:
                logger.info("using cached ECB rate %s dated %s", cached[0], cached[1])
                return self._build(cached[0], cached[1], ExchangeRateSource.LIVE_FALLBACK_CACHE)

            fixture_rate, fixture_date = self._load_fixture()
            logger.warning("using labelled FX fixture %s dated %s", fixture_rate, fixture_date)
            return self._build(fixture_rate, fixture_date, ExchangeRateSource.LIVE_FALLBACK_FIXTURE)

        await self._cache.store(
            base=s.fx_base_currency,
            quote=s.fx_quote_currency,
            provider=s.fx_data_provider,
            rate=rate,
            rate_date=rate_date,
            raw=raw,
        )
        logger.info("FX live rate %s dated %s (%s)", rate, rate_date, s.fx_data_provider)
        return self._build(rate, rate_date, ExchangeRateSource.LIVE)
