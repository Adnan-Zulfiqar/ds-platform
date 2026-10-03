"""Track E7 W3 — keeping a published WooCommerce product's price and stock
current. Real Postgres; the store is the W2 in-memory fake."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import set_tenant_id
from app.integrations.woocommerce.client import WooCommerceUnreachableError
from app.integrations.woocommerce.price_quantity import (
    RECONNECT_MESSAGE,
    WooCommercePriceQuantitySync,
)
from app.models.product import Product
from app.models.shopify import ListingSyncStatus, StoreListing
from app.models.store import Store
from tests.integration.test_ebay_c1_api import auth_header
from tests.integration.test_woocommerce_publish import (
    PUBLISH,
    FakeWoo,
    connected,
    draft,
    shop,  # noqa: F401 — the fixture, re-exported for this module
)

pytestmark = pytest.mark.integration


async def published(
    client: AsyncClient, db_session: AsyncSession
) -> tuple[dict[str, Any], uuid.UUID, str]:
    owner, store_id = await connected(client)
    product_id = await draft(db_session, owner)
    response = await client.post(
        PUBLISH,
        json={"productId": str(product_id), "storeId": store_id},
        headers=auth_header(owner),
    )
    assert response.status_code == 200, response.text
    set_tenant_id(uuid.UUID(str(owner["identity"]["tenant"]["id"])))
    return owner, product_id, store_id


async def listing_for(db_session: AsyncSession, product_id: uuid.UUID) -> StoreListing:
    listing = await db_session.scalar(
        sa.select(StoreListing)
        .where(StoreListing.product_id == product_id)
        .execution_options(populate_existing=True)
    )
    assert listing is not None
    return listing


async def test_a_price_and_stock_change_is_sent_as_absolute_values(
    client: AsyncClient,
    db_session: AsyncSession,
    shop: FakeWoo,  # noqa: F811
) -> None:
    _, product_id, _ = await published(client, db_session)
    await db_session.execute(
        sa.update(Product)
        .where(Product.id == product_id)
        .values(sell_price=Decimal("14.00"), stock_quantity=3)
    )
    shop.calls.clear()
    outcome = await WooCommercePriceQuantitySync(db_session).push(product_id)
    assert (outcome.synced, outcome.failed) == (1, 0)
    [remote] = shop.products.values()
    assert remote["regular_price"] == "14.00" and remote["stock_quantity"] == 3
    assert [c for c in shop.calls if c[0] == "PUT"] == [("PUT", f"/products/{remote['id']}")]

    again = await WooCommercePriceQuantitySync(db_session).push(product_id)
    assert again.synced == 1  # idempotent: the same numbers again
    assert remote["regular_price"] == "14.00"


async def test_a_wrong_currency_is_recorded_and_nothing_is_sent(
    client: AsyncClient,
    db_session: AsyncSession,
    shop: FakeWoo,  # noqa: F811
) -> None:
    _, product_id, _ = await published(client, db_session)
    await db_session.execute(
        sa.update(Product).where(Product.id == product_id).values(currency="USD")
    )
    shop.calls.clear()
    outcome = await WooCommercePriceQuantitySync(db_session).push(product_id)
    assert outcome.failed == 1
    assert [c for c in shop.calls if c[0] == "PUT"] == []
    listing = await listing_for(db_session, product_id)
    assert listing.status is ListingSyncStatus.ERROR
    assert "USD" in (listing.last_error or "")


async def test_a_disconnected_store_asks_for_a_reconnect(
    client: AsyncClient,
    db_session: AsyncSession,
    shop: FakeWoo,  # noqa: F811
) -> None:
    owner, product_id, store_id = await published(client, db_session)
    await client.post(
        f"/api/v1/integrations/woocommerce/stores/{store_id}/disconnect", headers=auth_header(owner)
    )
    set_tenant_id(uuid.UUID(str(owner["identity"]["tenant"]["id"])))
    outcome = await WooCommercePriceQuantitySync(db_session).push(product_id)
    assert outcome.failed == 1
    assert (await listing_for(db_session, product_id)).last_error == RECONNECT_MESSAGE


async def test_the_stores_refusal_is_recorded_on_the_listing(
    client: AsyncClient,
    db_session: AsyncSession,
    shop: FakeWoo,  # noqa: F811
) -> None:
    _, product_id, _ = await published(client, db_session)
    shop.reject_writes = "Stock management is disabled."
    outcome = await WooCommercePriceQuantitySync(db_session).push(product_id)
    assert outcome.failed == 1
    assert "Stock management is disabled." in (
        (await listing_for(db_session, product_id)).last_error or ""
    )


async def test_an_unreachable_store_raises_so_the_task_retries(
    client: AsyncClient,
    db_session: AsyncSession,
    shop: FakeWoo,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, product_id, _ = await published(client, db_session)
    from app.integrations.woocommerce import client as woo

    def down(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(woo, "_transport", httpx.MockTransport(down))
    with pytest.raises(WooCommerceUnreachableError):
        await WooCommercePriceQuantitySync(db_session).push(product_id)
    assert (await listing_for(db_session, product_id)).status is ListingSyncStatus.SYNCED


async def test_a_product_without_a_woocommerce_listing_makes_no_call(
    client: AsyncClient,
    db_session: AsyncSession,
    shop: FakeWoo,  # noqa: F811
) -> None:
    owner, _ = await connected(client)
    product_id = await draft(db_session, owner)
    shop.calls.clear()
    outcome = await WooCommercePriceQuantitySync(db_session).push(product_id)
    assert outcome.listings == 0 and shop.calls == []
    assert await db_session.scalar(sa.select(sa.func.count()).select_from(Store)) >= 1
