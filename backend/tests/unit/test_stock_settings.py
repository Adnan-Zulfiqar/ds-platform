"""The listed-stock rule, its repository's tenant scoping, and how every
channel's offer terms pick it up.

Runs without a database: the rule is a pure function, the repository is
checked by compiling its statement, and ``offer_terms`` reads plain objects.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.core.context import MissingTenantContextError, clear_context, set_tenant_id
from app.integrations.ebay.listing_content import offer_terms
from app.repositories.inventory import StockSettingsRepository
from app.services.stock_settings import listed_quantity

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("supplier_stock", "setting", "listed"),
    [
        (500, None, 500),  # no setting: the supplier's stock, unchanged
        (0, None, 0),
        (None, None, 0),
        (500, 14, 14),  # a warehouse total is replaced by the merchant's number
        (3, 14, 14),  # fewer than the setting still lists the setting
        (0, 14, 0),  # a sell-out is never hidden
        (None, 14, 0),
        (-2, 14, 0),  # a negative supplier figure is treated as none
    ],
)
def test_listed_quantity(supplier_stock: int | None, setting: int | None, listed: int) -> None:
    assert listed_quantity(supplier_stock, setting) == listed


def _product(*, stock: int, variant_stock: int | None = None) -> SimpleNamespace:
    variants = []
    if variant_stock is not None:
        variants.append(
            SimpleNamespace(
                stock_quantity=variant_stock,
                sell_price=Decimal("9.99"),
                sell_price_currency="GBP",
                deleted_at=None,
                is_enabled=True,
            )
        )
    return SimpleNamespace(
        variants=variants, stock_quantity=stock, sell_price=Decimal("9.99"), currency="GBP"
    )


def test_offer_terms_list_the_setting_for_a_single_variant() -> None:
    product = _product(stock=9000, variant_stock=640)
    assert offer_terms(product, publish_quantity=14).quantity == 14  # type: ignore[arg-type]
    assert offer_terms(product).quantity == 640  # type: ignore[arg-type]


def test_offer_terms_list_the_setting_for_a_product_without_variants() -> None:
    assert offer_terms(_product(stock=9000), publish_quantity=14).quantity == 14  # type: ignore[arg-type]
    assert offer_terms(_product(stock=0), publish_quantity=14).quantity == 0  # type: ignore[arg-type]


def test_stock_settings_are_read_for_the_bound_tenant_only(tenant_id: uuid.UUID) -> None:
    set_tenant_id(tenant_id)
    query = StockSettingsRepository(MagicMock())._base_query()
    sql = str(query.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "stock_settings.tenant_id" in sql
    assert str(tenant_id) in sql
    assert "stock_settings.deleted_at IS NULL" in sql


def test_stock_settings_refuse_to_read_without_a_tenant() -> None:
    clear_context()
    with pytest.raises(MissingTenantContextError):
        StockSettingsRepository(MagicMock())._base_query()
