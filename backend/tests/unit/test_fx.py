"""FX conversion integrity — never invent 1:1 across currencies."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.exceptions import FxUnavailableError
from app.domain.fx import convert_money
from app.domain.money import Money
from app.services.fx.providers import StubFXRateProvider, UnavailableFXRateProvider
from app.services.fx.service import FxService

pytestmark = pytest.mark.unit


class TestConvertMoney:
    def test_same_currency_is_direct(self) -> None:
        source = Money.of("5.00", "GBP")
        target, evidence = convert_money(source, to_currency="GBP", quote=None)
        assert target == source
        assert evidence is None

    async def test_stub_converts_cny_to_gbp(self) -> None:
        fx = FxService(StubFXRateProvider())
        converted, evidence, quote = await fx.convert(Money.of("45.00", "CNY"), to_currency="GBP")
        assert evidence is not None
        assert quote is not None
        assert converted.currency == "GBP"
        assert converted.amount == Decimal("4.9500")  # 45 * 0.11

    async def test_unavailable_provider_blocks(self) -> None:
        fx = FxService(UnavailableFXRateProvider())
        with pytest.raises(FxUnavailableError):
            await fx.convert(Money.of("23.74", "USD"), to_currency="CNY")

    async def test_no_identity_fallback(self) -> None:
        fx = FxService(UnavailableFXRateProvider())
        with pytest.raises(FxUnavailableError):
            await fx.convert(Money.of("23.74", "USD"), to_currency="GBP")
