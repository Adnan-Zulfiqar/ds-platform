"""FX rate provider implementations.

Production never invents a 1:1 rate when currencies differ. The default
``UnavailableFXRateProvider`` forces callers to surface a blocking error until
a real feed is configured. ``StubFXRateProvider`` exists only for tests.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.domain.fx import FxRateQuote, FxRateStatus
from app.domain.money import normalise_currency


class UnavailableFXRateProvider:
    """Default provider — always reports unavailable for cross-currency pairs."""

    @property
    def provider_name(self) -> str:
        return "unavailable"

    async def get_rate(
        self,
        base_currency: str,
        quote_currency: str,
        *,
        at_or_before: datetime | None = None,
    ) -> FxRateQuote | None:
        _ = base_currency, quote_currency, at_or_before
        return None


class StubFXRateProvider:
    """Deterministic rates for unit/integration tests — never used in production."""

    def __init__(self, rates: dict[tuple[str, str], Decimal] | None = None) -> None:
        self._rates = {
            (normalise_currency(b), normalise_currency(q)): Decimal(str(r))
            for (b, q), r in (rates or {}).items()
        }
        # Sensible defaults for common DropPilot pairs in tests.
        defaults = {
            ("CNY", "GBP"): Decimal("0.1100"),
            ("CNY", "USD"): Decimal("0.1400"),
            ("USD", "GBP"): Decimal("0.7800"),
            ("GBP", "USD"): Decimal("1.2800"),
            ("USD", "EUR"): Decimal("0.9200"),
        }
        for key, value in defaults.items():
            self._rates.setdefault(key, value)

    @property
    def provider_name(self) -> str:
        return "stub"

    async def get_rate(
        self,
        base_currency: str,
        quote_currency: str,
        *,
        at_or_before: datetime | None = None,
    ) -> FxRateQuote | None:
        _ = at_or_before
        base = normalise_currency(base_currency)
        quote = normalise_currency(quote_currency)
        if base == quote:
            return None
        rate = self._rates.get((base, quote))
        if rate is None:
            inverse = self._rates.get((quote, base))
            if inverse is None or inverse == 0:
                return None
            rate = (Decimal("1") / inverse).quantize(Decimal("0.00000001"))
        now = datetime.now(UTC)
        return FxRateQuote(
            base_currency=base,
            quote_currency=quote,
            rate=rate,
            provider_name=self.provider_name,
            retrieved_at=now,
            source_timestamp=now,
            expires_at=now + timedelta(minutes=60),
            status=FxRateStatus.CURRENT,
        )
