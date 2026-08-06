"""FxService — resolve rates and convert Money without inventing identity FX."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import lru_cache

from app.core.config import get_settings
from app.core.exceptions import FxUnavailableError
from app.domain.fx import (
    CurrencyConversion,
    FXRateProvider,
    FxRateQuote,
    FxRateStatus,
    convert_money,
)
from app.domain.money import Money, normalise_currency
from app.services.fx.providers import StubFXRateProvider, UnavailableFXRateProvider


class FxService:
    """Application-facing FX API used by the pricing engine."""

    def __init__(
        self,
        provider: FXRateProvider,
        *,
        max_age_minutes: int = 60,
    ) -> None:
        self._provider = provider
        self._max_age = timedelta(minutes=max_age_minutes)

    @property
    def provider_name(self) -> str:
        return self._provider.provider_name

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
        quote_row = await self._provider.get_rate(base, quote, at_or_before=at_or_before)
        if quote_row is None:
            return None
        return self._apply_freshness(quote_row)

    def _apply_freshness(self, quote: FxRateQuote) -> FxRateQuote:
        stamp = quote.source_timestamp or quote.retrieved_at
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        age = datetime.now(UTC) - stamp
        if age > self._max_age:
            return FxRateQuote(
                base_currency=quote.base_currency,
                quote_currency=quote.quote_currency,
                rate=quote.rate,
                provider_name=quote.provider_name,
                retrieved_at=quote.retrieved_at,
                source_timestamp=quote.source_timestamp,
                expires_at=quote.expires_at,
                status=FxRateStatus.STALE,
            )
        return quote

    async def convert(
        self,
        source: Money,
        *,
        to_currency: str,
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
            raise FxUnavailableError(
                "Pricing cannot be calculated because a valid currency "
                f"conversion is not available ({source.currency} → {target}).",
                details={
                    "from": source.currency,
                    "to": target,
                    "provider": self.provider_name,
                },
            )
        converted, evidence = convert_money(source, to_currency=target, quote=quote)
        return converted, evidence, quote


@lru_cache(maxsize=1)
def get_fx_service() -> FxService:
    settings = get_settings()
    provider_name = (settings.fx.provider or "unavailable").strip().lower()
    if provider_name == "stub":
        provider: FXRateProvider = StubFXRateProvider()
    else:
        # frankfurter / openexchangerates land behind the same interface later.
        provider = UnavailableFXRateProvider()
    return FxService(provider, max_age_minutes=settings.fx.rate_max_age_minutes)
