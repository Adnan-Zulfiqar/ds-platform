"""Track E7 W4a — importing WooCommerce orders. Real Postgres; the store is
the W2 fake, extended with an ``/orders`` listing."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.woocommerce import client as woo
from app.models.order import FulfillmentStatus, Order, OrderItem, OrderSource, PaymentStatus
from tests.integration.test_ebay_c1_api import auth_header, register, token_with_roles
from tests.integration.test_woocommerce_publish import CONNECT, FakeWoo, draft

pytestmark = pytest.mark.integration


class OrderingFakeWoo(FakeWoo):
    def __init__(self) -> None:
        super().__init__()
        self.orders: list[dict[str, Any]] = []
        self.order_queries: list[dict[str, str]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/wp-json/wc/v3")
        if path == "/orders":
            params = dict(request.url.params)
            self.order_queries.append(params)
            page, size = int(params.get("page", "1")), int(params.get("per_page", "10"))
            return httpx.Response(200, json=self.orders[(page - 1) * size : page * size])
        return super().handle(request)


def woo_order(
    order_id: int, *, status: str = "processing", sku: str = "", **extra: Any
) -> dict[str, Any]:
    return {
        "id": order_id,
        "status": status,
        "currency": "GBP",
        "total": "17.49",
        "shipping_total": "4.99",
        "date_created_gmt": "2026-10-01T10:00:00",
        "date_paid_gmt": "2026-10-01T10:05:00",
        "billing": {"first_name": "Jane", "last_name": "Buyer", "phone": "+44 20 7946 0000"},
        "shipping": {
            "first_name": "Jane",
            "last_name": "Buyer",
            "address_1": "1 High St",
            "city": "London",
            "postcode": "N1 1AA",
            "country": "GB",
        },
        "line_items": [
            {
                "id": order_id * 10,
                "product_id": 101,
                "sku": sku,
                "name": "Red mug",
                "quantity": 2,
                "total": "12.50",
            }
        ],
        **extra,
    }


@pytest.fixture
def shop(monkeypatch: pytest.MonkeyPatch) -> OrderingFakeWoo:
    fake = OrderingFakeWoo()
    monkeypatch.setattr(woo, "_resolver", lambda _host: ["93.184.216.34"])
    monkeypatch.setattr(woo, "_transport", httpx.MockTransport(fake.handle))
    return fake


async def connect(
    client: AsyncClient, owner: dict[str, Any], site: str = "https://shop.example.com"
) -> str:
    response = await client.post(
        CONNECT,
        json={
            "name": "Corner Shop",
            "siteUrl": site,
            "consumerKey": "ck_" + "1" * 40,
            "consumerSecret": "cs_" + "2" * 40,
        },
        headers=auth_header(owner),
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def import_url(store_id: str) -> str:
    return f"/api/v1/integrations/woocommerce/stores/{store_id}/orders/import"


async def test_orders_are_imported_with_buyer_address_and_mapped_status(
    client: AsyncClient, db_session: AsyncSession, shop: OrderingFakeWoo
) -> None:
    owner = await register(client)
    store_id = await connect(client, owner)
    product_id = await draft(db_session, owner)
    shop.orders = [woo_order(1, sku=f"dp-{product_id}"), woo_order(2, status="completed")]

    response = await client.post(import_url(store_id), headers=auth_header(owner))
    assert response.status_code == 200, response.text
    assert response.json() == {"fetched": 2, "created": 2, "updated": 0}
    assert (
        shop.order_queries[0]["modified_after"] and shop.order_queries[0]["dates_are_gmt"] == "true"
    )

    order = await db_session.scalar(sa.select(Order).where(Order.external_id == f"{store_id}:1"))
    assert order is not None
    assert order.source is OrderSource.WOOCOMMERCE
    assert str(order.store_id) == store_id
    assert (order.fulfillment_status, order.payment_status) == (
        FulfillmentStatus.PAID,
        PaymentStatus.PAID,
    )
    assert (order.recipient_name, order.city, order.country_code) == ("Jane Buyer", "London", "GB")
    assert order.total_amount == Decimal("17.49") and order.currency == "GBP"
    item = await db_session.scalar(sa.select(OrderItem).where(OrderItem.order_id == order.id))
    assert item is not None and item.product_id == product_id
    assert item.quantity == 2 and item.unit_price == Decimal("6.25")

    shipped = await db_session.scalar(sa.select(Order).where(Order.external_id == f"{store_id}:2"))
    assert shipped is not None and shipped.fulfillment_status is FulfillmentStatus.SHIPPED


async def test_a_reimport_updates_and_drafts_are_skipped(
    client: AsyncClient, db_session: AsyncSession, shop: OrderingFakeWoo
) -> None:
    owner = await register(client)
    store_id = await connect(client, owner)
    shop.orders = [woo_order(1), woo_order(3, status="checkout-draft")]
    await client.post(import_url(store_id), headers=auth_header(owner))
    shop.orders = [woo_order(1, status="refunded")]
    again = await client.post(import_url(store_id), headers=auth_header(owner))
    assert again.json() == {"fetched": 1, "created": 0, "updated": 1}
    rows = (
        await db_session.scalars(sa.select(Order).where(Order.source == OrderSource.WOOCOMMERCE))
    ).all()
    assert [r.fulfillment_status for r in rows] == [FulfillmentStatus.REFUNDED]


async def test_two_stores_with_the_same_order_number_do_not_collide(
    client: AsyncClient, db_session: AsyncSession, shop: OrderingFakeWoo
) -> None:
    owner = await register(client)
    first = await connect(client, owner, "https://one.example.com")
    second = await connect(client, owner, "https://two.example.com")
    shop.orders = [woo_order(1)]
    await client.post(import_url(first), headers=auth_header(owner))
    await client.post(import_url(second), headers=auth_header(owner))
    ids = set(
        (
            await db_session.scalars(
                sa.select(Order.external_id).where(Order.source == OrderSource.WOOCOMMERCE)
            )
        ).all()
    )
    assert ids == {f"{first}:1", f"{second}:1"}


async def test_paging_stops_on_a_short_page(client: AsyncClient, shop: OrderingFakeWoo) -> None:
    owner = await register(client)
    store_id = await connect(client, owner)
    shop.orders = [woo_order(i) for i in range(1, 53)]
    response = await client.post(import_url(store_id), headers=auth_header(owner))
    assert response.json()["created"] == 52
    assert [q["page"] for q in shop.order_queries] == ["1", "2"]


async def test_only_admins_import_and_only_their_own_stores(
    client: AsyncClient, shop: OrderingFakeWoo
) -> None:
    owner = await register(client)
    other = await register(client)
    store_id = await connect(client, owner)
    member = token_with_roles(owner, "member")
    assert (await client.post(import_url(store_id), headers=member)).status_code == 403
    assert (await client.post(import_url(store_id), headers=auth_header(other))).status_code == 404
    assert (
        await client.post(import_url(str(uuid.uuid4())), headers=auth_header(owner))
    ).status_code == 404
