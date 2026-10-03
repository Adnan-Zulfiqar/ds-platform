"""Track E1 — marking a Shopify order shipped, Shopify GraphQL faked.

The real service, repositories, row lock and request path run; only
``ShopifyClient.graphql`` is replaced, and every call is recorded so the
tests assert exactly what Shopify would receive.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import set_tenant_id
from app.core.encryption import encrypt
from app.integrations.shopify.client import ShopifyClient
from app.integrations.shopify.exceptions import ShopifyResponseError
from app.models.integration import IntegrationStatus
from app.models.order import FulfillmentStatus, Order, OrderSource
from app.models.shopify import ShopifyConnection
from app.models.store import Store, StorePlatform, StoreStatus
from tests.integration.test_ebay_c1_api import auth_header, register, token_with_roles

pytestmark = pytest.mark.integration

BODY = {"company": "USPS", "trackingNumber": "9400111899223344556677"}


class FakeShopify:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any] | None]] = []
        self.fulfillment_orders: list[dict[str, Any]] = [
            {"id": "gid://shopify/FulfillmentOrder/1", "status": "OPEN"},
            {"id": "gid://shopify/FulfillmentOrder/2", "status": "CLOSED"},
        ]
        self.user_errors: list[dict[str, Any]] = []
        self.access_denied = False

    async def graphql(
        self, query: str, *, variables: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.calls.append((query, variables))
        if self.access_denied:
            raise ShopifyResponseError(
                "Shopify GraphQL returned errors.",
                details={
                    "errors": [
                        {"message": "Access denied", "extensions": {"code": "ACCESS_DENIED"}}
                    ]
                },
            )
        if "fulfillmentOrders" in query:
            return {"data": {"order": {"fulfillmentOrders": {"nodes": self.fulfillment_orders}}}}
        if "fulfillmentCreate" in query:
            return {
                "data": {
                    "fulfillmentCreate": {
                        "fulfillment": None
                        if self.user_errors
                        else {"id": "gid://shopify/Fulfillment/9", "status": "SUCCESS"},
                        "userErrors": self.user_errors,
                    }
                }
            }
        raise AssertionError(f"unexpected Shopify query: {query[:60]}")


@pytest.fixture
def shopify(monkeypatch: pytest.MonkeyPatch) -> FakeShopify:
    fake = FakeShopify()

    async def graphql(
        self: ShopifyClient, query: str, *, variables: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return await fake.graphql(query, variables=variables)

    monkeypatch.setattr(ShopifyClient, "graphql", graphql)
    return fake


async def shopify_order(
    db_session: AsyncSession, body: dict[str, Any], **overrides: Any
) -> uuid.UUID:
    tenant_id = uuid.UUID(str(body["identity"]["tenant"]["id"]))
    set_tenant_id(tenant_id)
    suffix = uuid.uuid4().hex[:8]
    store = Store(
        tenant_id=tenant_id,
        name=f"shop-{suffix}",
        slug=f"shop-{suffix}",
        platform=StorePlatform.SHOPIFY,
        status=StoreStatus.CONNECTED,
    )
    db_session.add(store)
    await db_session.flush()
    db_session.add(
        ShopifyConnection(
            tenant_id=tenant_id,
            store_id=store.id,
            shop_domain=f"e1-{suffix}.myshopify.com",
            encrypted_access_token=encrypt("shpat_not_real"),
            scopes="read_orders,write_merchant_managed_fulfillment_orders",
            status=IntegrationStatus.CONNECTED,
        )
    )
    order = Order(
        tenant_id=tenant_id,
        source=OrderSource.SHOPIFY,
        external_id="5001",
        store_id=store.id,
        fulfillment_status=FulfillmentStatus.PAID,
        **overrides,
    )
    db_session.add(order)
    await db_session.flush()
    return order.id


def url(order_id: uuid.UUID) -> str:
    return f"/api/v1/integrations/shopify/orders/{order_id}/fulfilments"


async def test_marks_shipped_on_open_fulfillment_orders_only(
    client: AsyncClient, db_session: AsyncSession, shopify: FakeShopify
) -> None:
    body = await register(client)
    order_id = await shopify_order(db_session, body)

    response = await client.post(url(order_id), json=BODY, headers=auth_header(body))

    assert response.status_code == 201, response.text
    (_, lookup), (_, create) = shopify.calls
    assert lookup == {"id": "gid://shopify/Order/5001"}
    assert create is not None
    fulfillment = create["fulfillment"]
    assert fulfillment["lineItemsByFulfillmentOrder"] == [
        {"fulfillmentOrderId": "gid://shopify/FulfillmentOrder/1"}
    ]
    assert fulfillment["trackingInfo"] == {"company": "USPS", "number": "9400111899223344556677"}
    assert fulfillment["notifyCustomer"] is True
    order = await db_session.get(Order, order_id, populate_existing=True)
    assert order is not None and order.fulfillment_status is FulfillmentStatus.SHIPPED


async def test_the_same_tracking_number_is_sent_once(
    client: AsyncClient, db_session: AsyncSession, shopify: FakeShopify
) -> None:
    body = await register(client)
    order_id = await shopify_order(db_session, body)
    first = await client.post(url(order_id), json=BODY, headers=auth_header(body))
    second = await client.post(url(order_id), json=BODY, headers=auth_header(body))
    assert second.json()["id"] == first.json()["id"]
    assert sum("fulfillmentCreate" in q for q, _ in shopify.calls) == 1


async def test_shopifys_refusal_reaches_the_merchant(
    client: AsyncClient, db_session: AsyncSession, shopify: FakeShopify
) -> None:
    shopify.user_errors = [{"field": ["fulfillment"], "message": "Fulfillment order is closed."}]
    body = await register(client)
    order_id = await shopify_order(db_session, body)
    response = await client.post(url(order_id), json=BODY, headers=auth_header(body))
    assert response.status_code == 422
    assert response.json()["code"] == "shopify_fulfilment_rejected"
    assert "closed" in response.json()["message"]


async def test_a_store_without_the_new_scope_is_told_to_reconnect(
    client: AsyncClient, db_session: AsyncSession, shopify: FakeShopify
) -> None:
    shopify.access_denied = True
    body = await register(client)
    order_id = await shopify_order(db_session, body)
    response = await client.post(url(order_id), json=BODY, headers=auth_header(body))
    assert response.status_code == 422
    assert response.json()["code"] == "shopify_fulfilment_scope_missing"


async def test_nothing_left_to_fulfil_is_a_clear_refusal(
    client: AsyncClient, db_session: AsyncSession, shopify: FakeShopify
) -> None:
    shopify.fulfillment_orders = [{"id": "gid://shopify/FulfillmentOrder/1", "status": "CLOSED"}]
    body = await register(client)
    order_id = await shopify_order(db_session, body)
    response = await client.post(url(order_id), json=BODY, headers=auth_header(body))
    assert response.status_code == 422
    assert not any("fulfillmentCreate" in q for q, _ in shopify.calls)


async def test_roles_and_tenants(
    client: AsyncClient, db_session: AsyncSession, shopify: FakeShopify
) -> None:
    body = await register(client)
    order_id = await shopify_order(db_session, body)
    member = await client.post(url(order_id), json=BODY, headers=token_with_roles(body, "member"))
    assert member.status_code == 403
    other = auth_header(await register(client))
    assert (await client.post(url(order_id), json=BODY, headers=other)).status_code == 404
    assert shopify.calls == []
