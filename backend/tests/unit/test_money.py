"""Money value-object integrity."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.exceptions import CurrencyMismatchError, ValidationError
from app.domain.money import Money

pytestmark = pytest.mark.unit


class TestMoney:
    def test_same_currency_add(self) -> None:
        total = Money.of("10.00", "GBP") + Money.of("2.50", "GBP")
        assert total.amount == Decimal("12.50")
        assert total.currency == "GBP"

    def test_rejects_cross_currency_add(self) -> None:
        with pytest.raises(CurrencyMismatchError):
            _ = Money.of("23.74", "USD") + Money.of("5.00", "GBP")

    def test_rejects_cross_currency_sub(self) -> None:
        with pytest.raises(CurrencyMismatchError):
            _ = Money.of("23.74", "USD") - Money.of("5.00", "CNY")

    def test_equality_requires_currency(self) -> None:
        assert Money.of("5", "GBP") != Money.of("5", "USD")

    def test_invalid_currency_code(self) -> None:
        with pytest.raises(ValidationError):
            Money.of("1", "US")
