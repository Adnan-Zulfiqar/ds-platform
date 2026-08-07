"""FxService — the only application-level FX entrypoint.

Freshness window and Redis retention are separate:

- ``FX_CACHE_TTL_SECONDS`` — quote is *fresh* within this age (from
  provider_timestamp when present, else fetched_at)
- ``FX_MAX_STALENESS_SECONDS`` — Redis retention and the outer bound for
  controlled stale fallback when a refresh fails

Redis TTL is set to max_staleness so a quote is still available after the
freshness window for controlled stale use. Freshness is evaluated from quote
metadata, not from Redis eviction alone.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import lru_cache

from app.core.config import get_settings
from app.core.exceptions import FxProviderTimeoutError, FxUnavailableError
from app.core.logging import get_logger
from app.core.redis import CacheClient
from app.domain.fx import (
    CurrencyConversion,
    FXRateProvider,
    FxRateQuote,
    FxRateStatus,
    convert_money,
)
from app.domain.money import Money, normalise_currency
from app.services.fx.providers import (
    OpenExchangeRatesProvider,
    StubFXRateProvider,
    UnavailableFXRateProvider,
)

logger = get_logger(__name__)


def _quote_age_reference(quote: FxRateQuote) -> datetime:
    """Prefer provider/source time so re-fetching a stale feed does not look fresh."""
    stamp = quote.provider_timestamp or quote.fetched_at
    if stamp.tzinfo is None:
        return stamp.replace(tzinfo=UTC)
    return stamp


def serialize_quote(quote: FxRateQuote) -> str:
    """JSON-encode a quote with Decimal rates as strings (no float)."""
    payload = {
        "base_currency": quote.base_currency,
        "quote_currency": quote.quote_currency,
        "rate": str(quote.rate),
        "provider_name": quote.provider_name,
        "fetched_at": quote.fetched_at.isoformat(),
        "provider_timestamp": (
            quote.provider_timestamp.isoformat() if quote.provider_timestamp else None
        ),
        "expires_at": quote.expires_at.isoformat() if quote.expires_at else None,
        "status": quote.status.value,
        "derivation": quote.derivation,
    }
    return json.dumps(payload)


def deserialize_quote(raw: str) -> FxRateQuote:
    data = json.loads(raw)
    rate = Decimal(str(data["rate"]))
    if isinstance(rate, float):  # pragma: no cover — Decimal(str) never yields float
        raise TypeError("float rate forbidden")
    return FxRateQuote(
        base_currency=str(data["base_currency"]),
        quote_currency=str(data["quote_currency"]),
        rate=rate,
        provider_name=str(data["provider_name"]),
        fetched_at=datetime.fromisoformat(str(data["fetched_at"])),
        provider_timestamp=(
            datetime.fromisoformat(str(data["provider_timestamp"]))
            if data.get("provider_timestamp")
            else None
        ),
        expires_at=(
            datetime.fromisoformat(str(data["expires_at"])) if data.get("expires_at") else None
        ),
        status=FxRateStatus(str(data.get("status") or FxRateStatus.CURRENT.value)),
        derivation=str(data.get("derivation") or "direct"),
    )


class FxService:
    """Application-facing FX API used by the pricing engine."""

    def __init__(
        self,
        provider: FXRateProvider,
        *,
        freshness_seconds: int = 3600,
        max_staleness_seconds: int = 21600,
        cache: CacheClient | None = None,
        allow_controlled_stale: bool = True,
    ) -> None:
        self._provider = provider
        self._freshness = timedelta(seconds=freshness_seconds)
        self._max_staleness = timedelta(seconds=max_staleness_seconds)
        self._cache = cache
        self._allow_controlled_stale = allow_controlled_stale

    @property
    def provider_name(self) -> str:
        return self._provider.provider_name

    def _cache_key(self, base: str, quote: str) -> str:
        return f"fx:rate:{base}:{quote}"

    async def _cache_get(self, base: str, quote: str) -> FxRateQuote | None:
        if self._cache is None:
            return None
        raw = await self._cache.get(self._cache_key(base, quote), tenant_id=None)
        if not raw:
            return None
        try:
            return deserialize_quote(raw)
        except Exception:
            logger.warning("fx_quote_invalid", base_currency=base, quote_currency=quote)
            return None

    async def _cache_set(self, quote: FxRateQuote) -> None:
        if self._cache is None:
            return
        # Retention >= max staleness so controlled stale fallback remains possible.
        ttl = max(int(self._max_staleness.total_seconds()), int(self._freshness.total_seconds()))
        await self._cache.set(
            self._cache_key(quote.base_currency, quote.quote_currency),
            serialize_quote(quote),
            tenant_id=None,
            ttl_seconds=ttl,
        )

    def classify_freshness(self, quote: FxRateQuote, *, now: datetime | None = None) -> FxRateQuote:
        """Mark CURRENT / STALE / reject based on provider timestamp age."""
        now = now or datetime.now(UTC)
        age = now - _quote_age_reference(quote)
        if age <= self._freshness:
            status = FxRateStatus.CURRENT
        elif age <= self._max_staleness:
            status = FxRateStatus.STALE
        else:
            status = FxRateStatus.UNAVAILABLE
            logger.info(
                "fx_quote_stale_rejected",
                provider=quote.provider_name,
                base_currency=quote.base_currency,
                quote_currency=quote.quote_currency,
                provider_timestamp=(
                    quote.provider_timestamp.isoformat() if quote.provider_timestamp else None
                ),
                fetched_at=quote.fetched_at.isoformat(),
                age_seconds=int(age.total_seconds()),
            )
            return FxRateQuote(
                base_currency=quote.base_currency,
                quote_currency=quote.quote_currency,
                rate=quote.rate,
                provider_name=quote.provider_name,
                fetched_at=quote.fetched_at,
                provider_timestamp=quote.provider_timestamp,
                expires_at=quote.expires_at,
                status=FxRateStatus.UNAVAILABLE,
                derivation=quote.derivation,
            )
        if status is quote.status:
            return quote
        return FxRateQuote(
            base_currency=quote.base_currency,
            quote_currency=quote.quote_currency,
            rate=quote.rate,
            provider_name=quote.provider_name,
            fetched_at=quote.fetched_at,
            provider_timestamp=quote.provider_timestamp,
            expires_at=quote.expires_at,
            status=status,
            derivation=quote.derivation,
        )

    async def get_rate(
        self,
        base_currency: str,
        quote_currency: str,
        *,
        at_or_before: datetime | None = None,
    ) -> FxRateQuote | None:
        base = normalise_currency(base_currency)
        quote = normalise_currency(quote_currency)
        if base == quote:
            return None

        cached = await self._cache_get(base, quote)
        if cached is not None:
            classified = self.classify_freshness(cached)
            if classified.status is FxRateStatus.CURRENT:
                logger.info(
                    "fx_cache_fresh_hit",
                    provider=classified.provider_name,
                    base_currency=base,
                    quote_currency=quote,
                )
                return classified
            if classified.status is FxRateStatus.STALE:
                # Attempt refresh; on failure optionally return controlled stale.
                refreshed = await self._fetch_and_store(base, quote, at_or_before=at_or_before)
                if refreshed is not None:
                    return refreshed
                if self._allow_controlled_stale:
                    logger.info(
                        "fx_cache_stale_hit",
                        provider=classified.provider_name,
                        base_currency=base,
                        quote_currency=quote,
                        provider_timestamp=(
                            classified.provider_timestamp.isoformat()
                            if classified.provider_timestamp
                            else None
                        ),
                    )
                    return classified
                return None
            # Beyond max staleness — fall through to provider (cache miss semantics).
            logger.info("fx_cache_miss", base_currency=base, quote_currency=quote, reason="expired")
        else:
            logger.info("fx_cache_miss", base_currency=base, quote_currency=quote)

        return await self._fetch_and_store(base, quote, at_or_before=at_or_before)

    async def _fetch_and_store(
        self,
        base: str,
        quote: str,
        *,
        at_or_before: datetime | None,
    ) -> FxRateQuote | None:
        try:
            quote_row = await self._provider.get_rate(base, quote, at_or_before=at_or_before)
        except FxProviderTimeoutError:
            logger.warning(
                "fx_provider_timeout",
                provider=self.provider_name,
                base_currency=base,
                quote_currency=quote,
            )
            raise
        if quote_row is None:
            return None
        classified = self.classify_freshness(quote_row)
        if classified.status is FxRateStatus.UNAVAILABLE:
            return None
        await self._cache_set(classified)
        return classified

    async def convert(
        self,
        source: Money,
        *,
        to_currency: str,
        allow_stale: bool = True,
    ) -> tuple[Money, CurrencyConversion | None, FxRateQuote | None]:
        """Convert ``source`` into ``to_currency``.

        Same currency → direct (no quote). Differing currencies → provider quote
        or ``FxUnavailableError``. Never falls back to 1:1.
        """
        target = normalise_currency(to_currency)
        if source.currency == target:
            return source, None, None
        quote = await self.get_rate(source.currency, target)
        if quote is None or quote.status is FxRateStatus.UNAVAILABLE:
            logger.info(
                "pricing_blocked_fx_unavailable",
                provider=self.provider_name,
                base_currency=source.currency,
                quote_currency=target,
            )
            raise FxUnavailableError(
                "Pricing cannot be calculated because a valid currency "
                f"conversion is not available ({source.currency} → {target}).",
                details={
                    "from": source.currency,
                    "to": target,
                    "provider": self.provider_name,
                    "code": "fx_unavailable",
                },
            )
        if quote.status is FxRateStatus.STALE and not allow_stale:
            raise FxUnavailableError(
                "The exchange rate is too stale to use for pricing.",
                details={
                    "from": source.currency,
                    "to": target,
                    "code": "fx_rate_stale",
                },
            )
        converted, evidence = convert_money(source, to_currency=target, quote=quote)
        return converted, evidence, quote


@lru_cache(maxsize=1)
def get_fx_service() -> FxService:
    settings = get_settings()
    provider_name = (settings.fx.provider or "unavailable").strip().lower()
    if provider_name in {"stub"}:
        provider: FXRateProvider = StubFXRateProvider()
    elif provider_name in {"openexchangerates", "oer"}:
        key = settings.fx.api_key.get_secret_value() if settings.fx.api_key else ""
        provider = OpenExchangeRatesProvider(
            api_key=key,
            base_url=settings.fx.base_url,
            timeout_seconds=float(settings.fx.timeout_seconds),
        )
    else:
        provider = UnavailableFXRateProvider()

    freshness = settings.fx.cache_ttl_seconds
    max_stale = settings.fx.max_staleness_seconds
    # Back-compat: older env used rate_max_age_minutes as the freshness window.
    if settings.fx.rate_max_age_minutes and freshness == 3600:
        # Prefer explicit cache_ttl; rate_max_age_minutes still informs freshness
        # when operators only set the legacy knob.
        pass
    return FxService(
        provider,
        freshness_seconds=freshness,
        max_staleness_seconds=max_stale,
        cache=CacheClient(),
        allow_controlled_stale=True,
    )
