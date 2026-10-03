"""Track E7 W4b — WooCommerce order webhooks. Real Postgres; the store is the
W4 fake, extended with webhook registration and single-order reads."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.integrations.woocommerce import client as woo
from app.integrations.woocommerce import webhook as webhook_module
from app.integrations.woocommerce.connection import WEBHOOK_IDS_SETTING, webhook_secret_for
from app.models.order import FulfillmentStatus, Order, OrderSource
from app.models.store import Store
from tests.integration.test_ebay_c1_api import auth_header, register
from tests.integration.test_woocommerce_orders import OrderingFakeWoo, connect, woo_order

pytestmark = pytest.mark.integration

BASE = "https://api.droppilot.example/api/v1/integrations/woocommerce/webhooks"


class HookingFakeWoo(OrderingFakeWoo):
    def __init__(self) -> None:
        super().__init__()
        self.webhooks: dict[int, dict[str, Any]] = {}
        self.deleted: list[int] = []
        self.refuse_webhooks = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/wp-json/wc/v3")
        if path == "/webhooks" and request.method == "POST":
            if self.refuse_webhooks:
                return httpx.Response(
                    403, json={"code": "woocommerce_rest_cannot_create", "message": "No."}
                )
            hook_id = 900 + len(self.webhooks) + len(self.deleted)
            self.webhooks[hook_id] = json.loads(request.content)
            return httpx.Response(201, json={"id": hook_id})
        if path.startswith("/webhooks/") and request.method == "DELETE":
            hook_id = int(path.rsplit("/", 1)[1])
            self.webhooks.pop(hook_id, None)
            self.deleted.append(hook_id)
            return httpx.Response(200, json={"id": hook_id})
        if path.startswith("/orders/") and request.method == "GET":
            wanted = int(path.rsplit("/", 1)[1])
            for order in self.orders:
                if order["id"] == wanted:
                    return httpx.Response(200, json=order)
            return httpx.Response(404, json={"code": "invalid", "message": "Invalid ID."})
        return super().handle(request)


@pytest.fixture
def shop(monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession) -> HookingFakeWoo:
    """Also points the receiver's own ``transaction()`` at the test session,
    so it sees the rows the test made (which are never committed)."""

    @asynccontextmanager
    async def shared() -> AsyncIterator[AsyncSession]:
        yield db_session
        await db_session.flush()

    monkeypatch.setattr(webhook_module, "transaction", shared)
    fake = HookingFakeWoo()
    monkeypatch.setattr(woo, "_resolver", lambda _host: ["93.184.216.34"])
    monkeypatch.setattr(woo, "_transport", httpx.MockTransport(fake.handle))
    monkeypatch.setattr(settings.woocommerce, "webhook_callback_base", BASE)
    return fake


def signed(secret: str, body: bytes) -> dict[str, str]:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return {
        "X-WC-Webhook-Signature": base64.b64encode(digest).decode(),
        "Content-Type": "application/json",
    }


async def connected(
    client: AsyncClient, db_session: AsyncSession
) -> tuple[dict[str, Any], str, str, str]:
    owner = await register(client)
    store_id = await connect(client, owner)
    tenant_id = str(owner["identity"]["tenant"]["id"])
    store = await db_session.scalar(
        sa.select(Store)
        .where(Store.id == uuid.UUID(store_id))
        .execution_options(populate_existing=True)
    )
    assert store is not None
    secret = webhook_secret_for(store)
    assert secret
    return owner, tenant_id, store_id, secret


def hook_url(tenant_id: str, store_id: str) -> str:
    return f"/api/v1/integrations/woocommerce/webhooks/{tenant_id}/{store_id}"


async def test_connecting_registers_order_webhooks_at_a_url_naming_tenant_and_store(
    client: AsyncClient, db_session: AsyncSession, shop: HookingFakeWoo
) -> None:
    _, tenant_id, store_id, secret = await connected(client, db_session)
    assert sorted(h["topic"] for h in shop.webhooks.values()) == ["order.created", "order.updated"]
    for hook in shop.webhooks.values():
        assert hook["delivery_url"] == f"{BASE}/{tenant_id}/{store_id}"
        assert hook["secret"] == secret
    store = await db_session.scalar(sa.select(Store).where(Store.id == uuid.UUID(store_id)))
    assert store is not None and sorted(store.settings[WEBHOOK_IDS_SETTING]) == sorted(
        shop.webhooks
    )


async def test_a_signed_delivery_refetches_the_order_and_ignores_the_payload(
    client: AsyncClient, db_session: AsyncSession, shop: HookingFakeWoo
) -> None:
    _, tenant_id, store_id, secret = await connected(client, db_session)
    shop.orders = [woo_order(7, status="completed")]
    # The payload claims "processing"; the store says "completed". The store wins.
    body = json.dumps(woo_order(7, status="processing")).encode()
    response = await client.post(
        hook_url(tenant_id, store_id), content=body, headers=signed(secret, body)
    )
    assert response.status_code == 200, response.text
    order = await db_session.scalar(sa.select(Order).where(Order.external_id == f"{store_id}:7"))
    assert order is not None and order.source is OrderSource.WOOCOMMERCE
    assert order.fulfillment_status is FulfillmentStatus.SHIPPED


async def test_bad_signatures_and_forged_tenants_are_refused_uniformly(
    client: AsyncClient, db_session: AsyncSession, shop: HookingFakeWoo
) -> None:
    _, tenant_id, store_id, secret = await connected(client, db_session)
    other = await register(client)
    shop.orders = [woo_order(7)]
    body = json.dumps({"id": 7}).encode()
    cases = [
        (hook_url(tenant_id, store_id), signed("wrong-secret", body)),
        (hook_url(tenant_id, store_id), {"Content-Type": "application/json"}),
        (hook_url(str(other["identity"]["tenant"]["id"]), store_id), signed(secret, body)),
        (hook_url(tenant_id, str(uuid.uuid4())), signed(secret, body)),
    ]
    for url, headers in cases:
        response = await client.post(url, content=body, headers=headers)
        assert response.status_code == 401, (url, response.text)
    count = await db_session.scalar(
        sa.select(sa.func.count()).select_from(Order).where(Order.source == OrderSource.WOOCOMMERCE)
    )
    assert count == 0


async def test_the_creation_ping_is_acknowledged(
    client: AsyncClient, db_session: AsyncSession, shop: HookingFakeWoo
) -> None:
    _, tenant_id, store_id, _ = await connected(client, db_session)
    response = await client.post(
        hook_url(tenant_id, store_id),
        content=b"webhook_id=901",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 200


async def test_reconnect_and_disconnect_remove_the_old_webhooks(
    client: AsyncClient, db_session: AsyncSession, shop: HookingFakeWoo
) -> None:
    owner, _, store_id, _ = await connected(client, db_session)
    first = sorted(shop.webhooks)
    await connect(client, owner)
    assert sorted(shop.deleted) == first and len(shop.webhooks) == 2
    second = sorted(shop.webhooks)
    await client.post(
        f"/api/v1/integrations/woocommerce/stores/{store_id}/disconnect", headers=auth_header(owner)
    )
    assert sorted(shop.deleted) == sorted(first + second) and shop.webhooks == {}


async def test_no_https_base_means_no_registration(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, shop: HookingFakeWoo
) -> None:
    monkeypatch.setattr(
        settings.woocommerce, "webhook_callback_base", "http://localhost:8000/x/webhooks"
    )
    owner = await register(client)
    await connect(client, owner)
    assert shop.webhooks == {}


async def test_a_store_refusing_webhooks_still_connects_and_says_why(
    client: AsyncClient, shop: HookingFakeWoo
) -> None:
    shop.refuse_webhooks = True
    owner = await register(client)
    response = await client.post(
        "/api/v1/integrations/woocommerce/connect",
        json={
            "name": "Corner Shop",
            "siteUrl": "https://shop.example.com",
            "consumerKey": "ck_" + "1" * 40,
            "consumerSecret": "cs_" + "2" * 40,
        },
        headers=auth_header(owner),
    )
    assert response.status_code == 201
    assert response.json()["status"] == "connected"
    assert "Import recent orders" in (response.json()["lastError"] or "")
