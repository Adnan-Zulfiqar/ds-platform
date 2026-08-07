"""Exchange-rate domain types.

A rate multiplies an amount in ``base_currency`` to produce ``quote_currency``.
Never invent a 1.0 rate when currencies differ.

``provider_timestamp`` is the FX market/source time from the provider.
``fetched_at`` is when *our* server retrieved the quote. They are not the same.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from app.core.exceptions import FxUnavailableError, ValidationError
from app.domain.money import Money, normalise_currency


class FxRateStatus(StrEnum):
    CURRENT = "current"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class FxRateQuote:
    """One auditable conversion rate.

    ``rate`` must be a ``Decimal`` produced without a binary-float intermediate.
    """

    base_currency: str
    quote_currency: str
    rate: Decimal
    provider_name: str
    fetched_at: datetime
    provider_timestamp: datetime | None
    expires_at: datetime | None
    status: FxRateStatus
    #: How the rate was obtained: direct | inverted | via_usd
    derivation: str = "direct"

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_currency", normalise_currency(self.base_currency))
        object.__setattr__(self, "quote_currency", normalise_currency(self.quote_currency))
        if not isinstance(self.rate, Decimal):
            raise ValidationError(
                "FX rate must be Decimal (binary float is forbidden).",
                details={"code": "fx_rate_invalid", "type": type(self.rate).__name__},
            )
        if self.base_currency == self.quote_currency:
            raise ValidationError(
                "FX quotes must not be identity pairs — use a direct Money amount.",
                details={"code": "fx_identity_quote_forbidden"},
            )
        if self.rate <= 0 or not self.rate.is_finite():
            raise ValidationError(
                "FX rate must be a positive finite Decimal.",
                details={"code": "fx_rate_invalid"},
            )

    @property
    def is_stale(self) -> bool:
        return self.status is FxRateStatus.STALE


@dataclass(frozen=True, slots=True)
class CurrencyConversion:
    """Evidence that one Money was converted into another."""

    source: Money
    target: Money
    quote: FxRateQuote
    conversion_type: str = "fx"


class FXRateProvider(Protocol):
    """Production FX source — implementations must never invent 1:1 rates."""

    @property
    def provider_name(self) -> str: ...

    async def get_rate(
        self,
        base_currency: str,
        quote_currency: str,
        *,
        at_or_before: datetime | None = None,
    ) -> FxRateQuote | None:
        """Return a quote or ``None`` when unavailable (never a fake 1.0)."""
        ...


def convert_money(
    source: Money,
    *,
    to_currency: str,
    quote: FxRateQuote | None,
) -> tuple[Money, CurrencyConversion | None]:
    """Convert ``source`` into ``to_currency``.

    Same-currency is a direct pass-through (no FX). Differing currencies require
    a valid quote; a missing quote raises ``FxUnavailableError``.
    """
    target_code = normalise_currency(to_currency)
    if source.currency == target_code:
        return source, None
    if quote is None or quote.status is FxRateStatus.UNAVAILABLE:
        raise FxUnavailableError(
            "Pricing cannot be calculated because a valid currency conversion "
            f"is not available ({source.currency} → {target_code}).",
            details={
                "from": source.currency,
                "to": target_code,
                "status": "unavailable",
            },
        )
    if quote.base_currency != source.currency or quote.quote_currency != target_code:
        raise FxUnavailableError(
            "FX quote currencies do not match the conversion request.",
            details={
                "from": source.currency,
                "to": target_code,
                "quote_base": quote.base_currency,
                "quote_quote": quote.quote_currency,
            },
        )
    target = Money(amount=source.amount * quote.rate, currency=target_code).quantize()
    return target, CurrencyConversion(source=source, target=target, quote=quote)
