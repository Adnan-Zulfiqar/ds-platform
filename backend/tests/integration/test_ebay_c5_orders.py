"""EBAY-C5 — eBay orders in, shipments out, buyers erasable.

Builds on the C3 harness (owner connected, US listing setup saved so the
eBay store exists). eBay's Fulfillment API is faked; nothing reaches eBay.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.ebay.deletion import DeletionSubject, EbayOrderBuyersOwner
from app.models.order import FulfillmentStatus, Order, OrderItem, OrderSource, PaymentStatus
from app.models.store import Store
from tests.integration.ebay_c1_live import install
from tests.integration.test_ebay_c3_publish import (
    PublishFakeEbay,
    prepared,
)
from tests.integration.test_ebay_c3_publish import (
    fresh_caches as fresh_caches,  # autouse fixture, re-exported so it runs here
)
from tests.integration.test_ebay_c3_publish import (
    fresh_redis as fresh_redis,  # autouse: a Redis client per test event loop
)

pytestmark = pytest.mark.integration

IMPORT_URL = "/api/v1/integrations/ebay/orders/import"


def ebay_order(
    order_id: str, *, sku: str, cancelled: bool = False, username: str = "buyer_jane"
) -> dict[str, Any]:
    return {
        "orderId": order_id,
        "creationDate": "2026-10-02T10:00:00.000Z",
        "orderFulfillmentStatus": "NOT_STARTED",
        "orderPaymentStatus": "PAID",
        "cancelStatus": {"cancelState": "CANCELED" if cancelled else "NONE_REQUESTED"},
        "buyer": {"username": username},
        "pricingSummary": {
            "total": {"value": "17.49", "currency": "USD"},
            "deliveryCost": {"value": "4.99", "currency": "USD"},
        },
        "fulfillmentStartInstructions": [
            {
                "shippingStep": {
                    "shipTo": {
                        "fullName": "Jane Buyer",
                        "primaryPhone": {"phoneNumber": "5550100"},
                        "contactAddress": {
                            "addressLine1": "1 Main St",
                            "city": "Austin",
                            "stateOrProvince": "TX",
                            "postalCode": "78701",
                            "countryCode": "US",
                        },
                    }
                }
            }
        ],
        "lineItems": [
            {
                "lineItemId": f"{order_id}-L1",
                "legacyItemId": "110000000001",
                "sku": sku,
                "title": "Red ceramic mug",
                "quantity": 1,
                "lineItemCost": {"value": "12.50", "currency": "USD"},
                "listingMarketplaceId": "EBAY_US",
                "lineItemFulfillmentStatus": "NOT_STARTED",
            }
        ],
    }


class OrdersFakeEbay(PublishFakeEbay):
    def __init__(self) -> None:
        super().__init__()
        self.orders: list[dict[str, Any]] = []
        self.fulfilments: list[tuple[str, dict[str, Any]]] = []

    async def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/sell/fulfillment/v1/order" and request.method == "GET":
            assert request.url.params["filter"].startswith("lastmodifieddate:[")
            offset = int(request.url.params["offset"])
            page = self.orders[offset : offset + 50]
            return httpx.Response(200, json={"orders": page, "total": len(self.orders)})
        if path.startswith("/sell/fulfillment/v1/order/") and path.endswith(
            "/shipping_fulfillment"
        ):
            order_id = path.split("/")[-2]
            self.fulfilments.append((order_id, json.loads(request.content)))
            return httpx.Response(201, headers={"Location": f"{request.url}/F1"})
        return await super().handler(request)


@pytest.fixture
def ebay(monkeypatch: pytest.MonkeyPatch) -> OrdersFakeEbay:
    fake = OrdersFakeEbay()
    install(monkeypatch, fake)
    return fake


async def imported(
    client: AsyncClient, db_session: AsyncSession, ebay: OrdersFakeEbay
) -> tuple[dict[str, str], uuid.UUID]:
    headers, product_id, _ = await prepared(client, db_session)
    ebay.orders = [ebay_order("12-34567-89012", sku=f"dp-us-{product_id}")]
    response = await client.post(IMPORT_URL, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json() == {"fetched": 1, "created": 1, "updated": 0}
    return headers, product_id


async def the_order(db_session: AsyncSession) -> Order:
    order = await db_session.scalar(
        sa.select(Order)
        .where(Order.source == OrderSource.EBAY)
        .execution_options(populate_existing=True)
    )
    assert order is not None
    return order


async def test_import_maps_the_order_its_store_and_its_product(
    client: AsyncClient, db_session: AsyncSession, ebay: OrdersFakeEbay
) -> None:
    _, product_id = await imported(client, db_session, ebay)

    order = await the_order(db_session)
    assert (order.fulfillment_status, order.payment_status) == (
        FulfillmentStatus.PAID,
        PaymentStatus.PAID,
    )
    assert (order.recipient_name, order.city, order.country_code) == ("Jane Buyer", "Austin", "US")
    assert order.marketplace_buyer_username == "buyer_jane"
    assert order.total_amount == Decimal("17.49")
    store = await db_session.get(Store, order.store_id)
    assert store is not None and store.slug == "ebay-marketplace-us"
    item = await db_session.scalar(sa.select(OrderItem).where(OrderItem.order_id == order.id))
    assert item is not None
    assert (item.product_id, item.external_item_id, item.quantity) == (
        product_id,
        "12-34567-89012-L1",
        1,
    )


async def test_reimport_updates_and_reflects_an_eBay_cancellation(
    client: AsyncClient, db_session: AsyncSession, ebay: OrdersFakeEbay
) -> None:
    headers, product_id = await imported(client, db_session, ebay)
    ebay.orders = [ebay_order("12-34567-89012", sku=f"dp-us-{product_id}", cancelled=True)]

    again = await client.post(IMPORT_URL, headers=headers)

    assert again.json() == {"fetched": 1, "created": 0, "updated": 1}
    assert (await the_order(db_session)).fulfillment_status is FulfillmentStatus.CANCELLED
    items = (await db_session.scalars(sa.select(OrderItem))).all()
    assert len(items) == 1


async def test_shipping_tells_eBay_once_per_tracking_number(
    client: AsyncClient, db_session: AsyncSession, ebay: OrdersFakeEbay
) -> None:
    headers, _ = await imported(client, db_session, ebay)
    order = await the_order(db_session)
    url = f"/api/v1/integrations/ebay/orders/{order.id}/shipments"
    body = {"carrierCode": "USPS", "trackingNumber": "9400111899223344556677"}

    first = await client.post(url, json=body, headers=headers)
    second = await client.post(url, json=body, headers=headers)

    assert first.status_code == 201, first.text
    assert second.json()["id"] == first.json()["id"]
    ((order_id, payload),) = ebay.fulfilments
    assert order_id == "12-34567-89012"
    assert payload["lineItems"] == [{"lineItemId": "12-34567-89012-L1", "quantity": 1}]
    assert (payload["shippingCarrierCode"], payload["trackingNumber"]) == (
        "USPS",
        "9400111899223344556677",
    )
    assert (await the_order(db_session)).fulfillment_status is FulfillmentStatus.SHIPPED


async def test_a_cancelled_order_cannot_be_shipped(
    client: AsyncClient, db_session: AsyncSession, ebay: OrdersFakeEbay
) -> None:
    headers, product_id, _ = await prepared(client, db_session)
    ebay.orders = [ebay_order("99-1", sku=f"dp-us-{product_id}", cancelled=True)]
    assert (await client.post(IMPORT_URL, headers=headers)).status_code == 200
    order = await the_order(db_session)

    response = await client.post(
        f"/api/v1/integrations/ebay/orders/{order.id}/shipments",
        json={"carrierCode": "USPS", "trackingNumber": "1234567890"},
        headers=headers,
    )

    assert response.status_code == 422
    assert ebay.fulfilments == []


async def test_the_deletion_owner_anonymises_the_buyer_and_keeps_the_sale(
    client: AsyncClient, db_session: AsyncSession, ebay: OrdersFakeEbay
) -> None:
    await imported(client, db_session, ebay)
    owner = EbayOrderBuyersOwner()

    other = DeletionSubject(user_id=None, username="someone_else", eias_token=None)
    buyer = DeletionSubject(user_id=None, username="buyer_jane", eias_token=None)
    assert await owner.erase(db_session, other) == 0
    assert await owner.erase(db_session, buyer) == 1
    assert await owner.erase(db_session, buyer) == 0

    order = await the_order(db_session)
    assert (
        order.marketplace_buyer_username,
        order.recipient_name,
        order.address_line1,
        order.recipient_phone,
    ) == (
        None,
        None,
        None,
        None,
    )
    assert order.total_amount == Decimal("17.49")


async def test_importing_without_a_connection_is_a_clear_409(
    client: AsyncClient, db_session: AsyncSession, ebay: OrdersFakeEbay
) -> None:
    from tests.integration.test_ebay_c1_api import auth_header, register

    headers = auth_header(await register(client))
    response = await client.post(IMPORT_URL, headers=headers)
    assert response.status_code == 409
    assert response.json()["code"] == "ebay_not_connected"
