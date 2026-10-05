"""The supplier-refresh snapshot notices what a channel would receive.

Real Postgres: the snapshot is two queries, and the point is that a stock
move, a price move, a disabled variant and a removed variant all change it,
while a refresh that changes nothing does not.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import set_tenant_id
from app.models.product import Product, ProductSource, ProductStatus, ProductVariant
from app.tasks.products import price_stock_snapshot
from tests.integration.test_ebay_c1_api import register

pytestmark = pytest.mark.integration


async def test_snapshot_changes_only_when_price_or_stock_does(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    owner = await register(client)
    tenant_id = uuid.UUID(str(owner["identity"]["tenant"]["id"]))
    set_tenant_id(tenant_id)
    product = Product(
        tenant_id=tenant_id,
        source=ProductSource.ALIEXPRESS,
        external_id="1005001",
        title="Case",
        status=ProductStatus.DRAFT,
        sell_price=Decimal("10"),
        stock_quantity=5,
    )
    db_session.add(product)
    await db_session.flush()
    a = ProductVariant(
        tenant_id=tenant_id, product_id=product.id, external_variant_id="a", stock_quantity=3
    )
    b = ProductVariant(
        tenant_id=tenant_id, product_id=product.id, external_variant_id="b", stock_quantity=4
    )
    db_session.add_all([a, b])
    await db_session.flush()

    base = await price_stock_snapshot(db_session, product.id)
    assert base == await price_stock_snapshot(db_session, product.id)  # stable when untouched

    a.stock_quantity = 0
    await db_session.flush()
    stock_moved = await price_stock_snapshot(db_session, product.id)
    assert stock_moved != base

    b.sell_price = Decimal("12.50")
    await db_session.flush()
    price_moved = await price_stock_snapshot(db_session, product.id)
    assert price_moved != stock_moved

    b.is_enabled = False
    await db_session.flush()
    disabled = await price_stock_snapshot(db_session, product.id)
    assert disabled != price_moved

    a.deleted_at = datetime.now(UTC)
    await db_session.flush()
    removed = await price_stock_snapshot(db_session, product.id)
    assert removed != disabled
    assert len(removed) == 2  # product row plus the one live variant
