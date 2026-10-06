"""Groundwork for supplier auto-ordering (2026-10-05).

Real Postgres. Shopify orders now keep their lines, each linked to the exact
catalogue variant through the listing's variant map, and the AliExpress
status refresh only ever looks at AliExpress orders.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import set_tenant_id
from app.integrations.shopify.sync import ShopifySyncService
from app.models.order import FulfillmentStatus, Order, OrderItem, OrderSource, PaymentStatus
from app.models.product import Product, ProductSource, ProductStatus, ProductVariant
from app.models.shopify import ListingSyncStatus, StoreListing
from app.models.store import Store, StorePlatform, StoreStatus
from app.repositories.order import OrderRepository
from tests.integration.test_ebay_c1_api import register

pytestmark = pytest.mark.integration


async def workspace(client: AsyncClient) -> uuid.UUID:
    owner = await register(client)
    tenant_id = uuid.UUID(str(owner["identity"]["tenant"]["id"]))
    set_tenant_id(tenant_id)
    return tenant_id


async def published_product(
    db_session: AsyncSession, tenant_id: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    """A Shopify store and a two-variant product published to it as
    Shopify product 111 with variants 9001 and 9002."""
    store = Store(
        tenant_id=tenant_id,
        name="Shop",
        slug=f"shop-{uuid.uuid4().hex[:6]}",
        platform=StorePlatform.SHOPIFY,
        status=StoreStatus.CONNECTED,
        currency="USD",
    )
    db_session.add(store)
    product = Product(
        tenant_id=tenant_id,
        source=ProductSource.ALIEXPRESS,
        external_id="1005001",
        title="Case",
        status=ProductStatus.ACTIVE,
        sell_price=Decimal("10"),
    )
    db_session.add(product)
    await db_session.flush()
    red = ProductVariant(tenant_id=tenant_id, product_id=product.id, external_variant_id="r")
    blue = ProductVariant(tenant_id=tenant_id, product_id=product.id, external_variant_id="b")
    db_session.add_all([red, blue])
    await db_session.flush()
    db_session.add(
        StoreListing(
            tenant_id=tenant_id,
            store_id=store.id,
            product_id=product.id,
            external_product_id="111",
            external_variant_map={
                str(red.id): "9001",
                str(blue.id): "9002",
                f"price:{red.id}": "10.00",
            },
            status=ListingSyncStatus.SYNCED,
        )
    )
    await db_session.flush()
    return store.id, product.id, red.id, blue.id


def shopify_order(lines: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": 555,
        "financial_status": "paid",
        "currency": "USD",
        "total_price": "30.00",
        "shipping_address": {"name": "Jane", "country_code": "GB", "city": "London"},
        "line_items": lines,
    }


async def items(db_session: AsyncSession) -> list[tuple[Any, ...]]:
    rows = await db_session.execute(
        sa.select(
            OrderItem.external_item_id,
            OrderItem.product_id,
            OrderItem.variant_id,
            OrderItem.quantity,
            OrderItem.unit_price,
        ).order_by(OrderItem.external_item_id)
    )
    return [tuple(row) for row in rows.all()]


async def test_shopify_lines_are_stored_with_the_exact_variant(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    tenant_id = await workspace(client)
    store_id, product_id, red, blue = await published_product(db_session, tenant_id)
    sync = ShopifySyncService(db_session)

    result = await sync.upsert_order_from_shopify(
        store_id=store_id,
        raw=shopify_order(
            [
                {"id": 1, "product_id": 111, "variant_id": 9002, "quantity": 2, "price": "10.00"},
                {"id": 2, "product_id": 999, "variant_id": 1, "quantity": 1, "price": "5.00"},
                {"id": 3, "product_id": 111, "variant_id": 4242, "quantity": 1, "price": "10.00"},
            ]
        ),
    )

    assert result == "created"
    assert await items(db_session) == [
        ("1", product_id, blue, 2, Decimal("10.00")),
        ("2", None, None, 1, Decimal("5.00")),  # not a DropPilot listing
        ("3", product_id, None, 1, Decimal("10.00")),  # unknown variant: product only
    ]

    # An update replaces the lines rather than adding to them.
    await sync.upsert_order_from_shopify(
        store_id=store_id,
        raw=shopify_order(
            [{"id": 1, "product_id": 111, "variant_id": 9001, "quantity": 1, "price": "10.00"}]
        ),
    )
    assert await items(db_session) == [("1", product_id, red, 1, Decimal("10.00"))]


async def test_the_aliexpress_refresh_only_sees_aliexpress_orders(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    tenant_id = await workspace(client)
    for source, external_id in (
        (OrderSource.ALIEXPRESS, "ae-1"),
        (OrderSource.SHOPIFY, "sh-1"),
        (OrderSource.EBAY, "eb-1"),
        (OrderSource.WOOCOMMERCE, "wc-1"),
    ):
        db_session.add(
            Order(
                tenant_id=tenant_id,
                source=source,
                external_id=external_id,
                fulfillment_status=FulfillmentStatus.PAID,
                payment_status=PaymentStatus.PAID,
            )
        )
    await db_session.flush()

    active = await OrderRepository(db_session).list_active_between(source=OrderSource.ALIEXPRESS)
    assert [order.external_id for order in active] == ["ae-1"]
