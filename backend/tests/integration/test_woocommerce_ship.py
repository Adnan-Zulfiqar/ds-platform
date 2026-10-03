"""Track E7 W5 — marking a WooCommerce order shipped. Real Postgres; the
store is the W4 fake, extended with order updates and notes."""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.woocommerce import client as woo
from app.models.order import FulfillmentStatus, Order, Shipment
from tests.integration.test_ebay_c1_api import auth_header, register, token_with_roles
from tests.integration.test_woocommerce_orders import (
    OrderingFakeWoo,
    connect,
    import_url,
    woo_order,
)

pytestmark = pytest.mark.integration


class ShippingFakeWoo(OrderingFakeWoo):
    def __init__(self) -> None:
        super().__init__()
        self.updates: list[tuple[str, dict[str, Any]]] = []
        self.notes: list[tuple[str, dict[str, Any]]] = []
        self.fail_notes = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/wp-json/wc/v3")
        if path.startswith("/orders/") and path.endswith("/notes") and request.method == "POST":
            if self.fail_notes:
                return httpx.Response(503, text="busy")
            self.notes.append((path.split("/")[2], json.loads(request.content)))
            return httpx.Response(201, json={"id": len(self.notes)})
        if path.startswith("/orders/") and request.method == "PUT":
            self.updates.append((path.split("/")[2], json.loads(request.content)))
            return httpx.Response(200, json={"id": int(path.split("/")[2]), "status": "completed"})
        return super().handle(request)


@pytest.fixture
def shop(monkeypatch: pytest.MonkeyPatch) -> ShippingFakeWoo:
    fake = ShippingFakeWoo()
    monkeypatch.setattr(woo, "_resolver", lambda _host: ["93.184.216.34"])
    monkeypatch.setattr(woo, "_transport", httpx.MockTransport(fake.handle))
    return fake


SHIP = {
    "company": "Royal Mail",
    "trackingNumber": "RM123456789GB",
    "trackingUrl": "https://track.example/RM123456789GB",
    "notifyCustomer": True,
}


async def imported_order(
    client: AsyncClient, db_session: AsyncSession, shop: ShippingFakeWoo, **order: Any
) -> tuple[dict[str, Any], uuid.UUID]:
    owner = await register(client)
    store_id = await connect(client, owner)
    shop.orders = [woo_order(7, **order)]
    await client.post(import_url(store_id), headers=auth_header(owner))
    order_id = await db_session.scalar(
        sa.select(Order.id).where(Order.external_id == f"{store_id}:7")
    )
    assert order_id is not None
    return owner, order_id


def ship_url(order_id: uuid.UUID) -> str:
    return f"/api/v1/integrations/woocommerce/orders/{order_id}/shipments"


async def test_marking_shipped_completes_the_order_and_adds_a_customer_note(
    client: AsyncClient, db_session: AsyncSession, shop: ShippingFakeWoo
) -> None:
    owner, order_id = await imported_order(client, db_session, shop)
    response = await client.post(ship_url(order_id), json=SHIP, headers=auth_header(owner))
    assert response.status_code == 201, response.text
    assert response.json()["trackingNumber"] == "RM123456789GB"
    assert shop.updates == [("7", {"status": "completed"})]
    [(order_ref, note)] = shop.notes
    assert order_ref == "7" and note["customer_note"] is True
    assert "Royal Mail" in note["note"] and "RM123456789GB" in note["note"]
    assert "https://track.example/RM123456789GB" in note["note"]

    order = await db_session.scalar(
        sa.select(Order).where(Order.id == order_id).execution_options(populate_existing=True)
    )
    assert order is not None and order.fulfillment_status is FulfillmentStatus.SHIPPED


async def test_the_same_tracking_number_twice_tells_the_store_once(
    client: AsyncClient, db_session: AsyncSession, shop: ShippingFakeWoo
) -> None:
    owner, order_id = await imported_order(client, db_session, shop)
    first = await client.post(ship_url(order_id), json=SHIP, headers=auth_header(owner))
    again = await client.post(ship_url(order_id), json=SHIP, headers=auth_header(owner))
    assert again.json()["id"] == first.json()["id"]
    assert len(shop.notes) == 1 and len(shop.updates) == 1
    count = await db_session.scalar(
        sa.select(sa.func.count()).select_from(Shipment).where(Shipment.order_id == order_id)
    )
    assert count == 1


async def test_a_cancelled_order_is_refused_before_any_call(
    client: AsyncClient, db_session: AsyncSession, shop: ShippingFakeWoo
) -> None:
    owner, order_id = await imported_order(client, db_session, shop, status="cancelled")
    response = await client.post(ship_url(order_id), json=SHIP, headers=auth_header(owner))
    assert response.status_code == 422
    assert shop.updates == [] and shop.notes == []


async def test_a_store_outage_records_no_shipment(
    client: AsyncClient, db_session: AsyncSession, shop: ShippingFakeWoo
) -> None:
    owner, order_id = await imported_order(client, db_session, shop)
    shop.fail_notes = True
    response = await client.post(ship_url(order_id), json=SHIP, headers=auth_header(owner))
    assert response.status_code == 502
    count = await db_session.scalar(
        sa.select(sa.func.count()).select_from(Shipment).where(Shipment.order_id == order_id)
    )
    assert count == 0


async def test_only_admins_and_only_woocommerce_orders(
    client: AsyncClient, db_session: AsyncSession, shop: ShippingFakeWoo
) -> None:
    owner, order_id = await imported_order(client, db_session, shop)
    member = token_with_roles(owner, "member")
    assert (await client.post(ship_url(order_id), json=SHIP, headers=member)).status_code == 403
    other = await register(client)
    assert (
        await client.post(ship_url(order_id), json=SHIP, headers=auth_header(other))
    ).status_code == 404
