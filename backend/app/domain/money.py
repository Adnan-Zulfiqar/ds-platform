"""Currency-safe money value object.

Amounts are ``Decimal``; ISO 4217 codes are authoritative. Formatting belongs
at the presentation layer — never invent a currency from a symbol or country.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Self

from app.core.exceptions import CurrencyMismatchError, ValidationError

_MONEY_QUANT = Decimal("0.0001")


def normalise_currency(code: str | None) -> str:
    """Return an uppercase ISO currency code or raise."""
    if code is None or not str(code).strip():
        raise ValidationError(
            "A currency code is required for every monetary amount.",
            details={"code": "currency_required"},
        )
    normalised = str(code).strip().upper()
    if len(normalised) != 3 or not normalised.isalpha():
        raise ValidationError(
            f"Invalid ISO 4217 currency code: {code!r}.",
            details={"code": "currency_invalid", "currency": code},
        )
    return normalised


@dataclass(frozen=True, slots=True)
class Money:
    """An amount bound to exactly one currency."""

    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "currency", normalise_currency(self.currency))
        if not isinstance(self.amount, Decimal):
            object.__setattr__(self, "amount", Decimal(str(self.amount)))
        if not self.amount.is_finite():
            raise ValidationError(
                "Monetary amounts must be finite Decimals.",
                details={"code": "money_non_finite"},
            )

    @classmethod
    def of(cls, amount: Decimal | int | str, currency: str) -> Self:
        return cls(amount=Decimal(str(amount)), currency=currency)

    def quantize(self, quantum: Decimal = _MONEY_QUANT) -> Money:
        return Money(
            amount=self.amount.quantize(quantum, rounding=ROUND_HALF_UP),
            currency=self.currency,
        )

    def _require_same_currency(self, other: Money, *, operation: str) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatchError(
                f"Cannot {operation} {self.currency} and {other.currency} "
                "without an explicit currency conversion.",
                details={
                    "code": "currency_mismatch",
                    "left": self.currency,
                    "right": other.currency,
                    "operation": operation,
                },
            )

    def __add__(self, other: Money) -> Money:
        self._require_same_currency(other, operation="add")
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._require_same_currency(other, operation="subtract")
        return Money(self.amount - other.amount, self.currency)

    def __mul__(self, factor: Decimal | int) -> Money:
        return Money(self.amount * Decimal(str(factor)), self.currency)

    def __truediv__(self, divisor: Decimal | int) -> Money:
        return Money(self.amount / Decimal(str(divisor)), self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        return self.currency == other.currency and self.amount == other.amount
