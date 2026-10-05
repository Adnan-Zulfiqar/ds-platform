"""Shopify's mandatory privacy webhooks, end to end through the receiver.

Real Postgres, signed requests. The properties that matter:

- ``customers/redact`` blanks exactly the listed orders, in the one tenant
  and store the shop belongs to. A different tenant's order with the same
  marketplace id is untouched (the cross-tenant case).
- ``shop/redact`` still works after uninstall has deleted the connection.
- ``customers/data_request`` is referred to the merchant as a notification
  that carries ids, never the customer's email or phone.
- A bad signature changes nothing.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from fakeredis import aioredis as fake_aioredis
from httpx import AsyncClient
from pydantic import SecretStr

from app.core.encryption import encrypt
from app.database.session import transaction
from app.models.integration import IntegrationStatus
from app.models.notification import Notification
from app.models.order import FulfillmentStatus, Order, OrderSource, PaymentStatus
from app.models.shopify import ShopifyConnection
from app.models.store import Store, StorePlatform, StoreStatus
from app.models.tenant import Tenant, TenantStatus

pytestmark = pytest.mark.integration

SECRET = "shopify-gdpr-test-secret"
WEBHOOK_URL = "/api/v1/integrations/shopify/webhook"


def _sign(body: bytes) -> str:
    return base64.b64encode(hmac.new(SECRET.encode(), body, hashlib.sha256).digest()).decode()


@pytest.fixture(autouse=True)
def _webhook_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.integrations.shopify.webhook.settings.shopify.api_secret", SecretStr(SECRET)
    )
    monkeypatch.setattr(
        "app.integrations.shopify.webhook.get_redis",
        lambda _purpose: fake_aioredis.FakeRedis(decode_responses=True),
    )


async def _shop(*, domain: str, connected: bool = True) -> tuple[uuid.UUID, uuid.UUID]:
    """A committed tenant + Shopify store (+ connection), visible to the
    webhook's own transaction. Returns ``(tenant_id, store_id)``."""
    async with transaction() as session:
        tenant = Tenant(
            name=domain, slug=f"gdpr-{uuid.uuid4().hex[:10]}", status=TenantStatus.ACTIVE
        )
        session.add(tenant)
        await session.flush()
        store = Store(
            tenant_id=tenant.id,
            name=domain,
            slug=f"s-{uuid.uuid4().hex[:8]}",
            platform=StorePlatform.SHOPIFY,
            status=StoreStatus.CONNECTED,
            external_store_id=domain,
        )
        session.add(store)
        await session.flush()
        if connected:
            session.add(
                ShopifyConnection(
                    tenant_id=tenant.id,
                    store_id=store.id,
                    shop_domain=domain,
                    encrypted_access_token=encrypt("shpat_test"),
                    scopes="read_orders",
                    status=IntegrationStatus.CONNECTED,
                )
            )
            await session.flush()
        return tenant.id, store.id


async def _order(tenant_id: uuid.UUID, store_id: uuid.UUID, external_id: str) -> uuid.UUID:
    async with transaction() as session:
        row = Order(
            tenant_id=tenant_id,
            store_id=store_id,
            source=OrderSource.SHOPIFY,
            external_id=external_id,
            fulfillment_status=FulfillmentStatus.PENDING,
            payment_status=PaymentStatus.PAID,
            buyer_name="Jane Buyer",
            recipient_name="Jane Buyer",
            recipient_phone="+44 7700 900000",
            address_line1="1 High Street",
            city="London",
            postal_code="N1 1AA",
            country_code="GB",
        )
        session.add(row)
        await session.flush()
        return row.id


async def _buyer(order_id: uuid.UUID) -> tuple[Any, ...]:
    async with transaction() as session:
        row = (
            await session.execute(
                sa.select(
                    Order.buyer_name,
                    Order.recipient_name,
                    Order.recipient_phone,
                    Order.address_line1,
                    Order.city,
                    Order.postal_code,
                    Order.country_code,
                ).where(Order.id == order_id)
            )
        ).one()
        return tuple(row)


async def _post(
    client: AsyncClient, topic: str, domain: str, payload: dict[str, Any], *, sign: bool = True
):
    body = json.dumps(payload).encode()
    return await client.post(
        WEBHOOK_URL,
        content=body,
        headers={
            "content-type": "application/json",
            "x-shopify-topic": topic,
            "x-shopify-hmac-sha256": _sign(body) if sign else "bad",
            "x-shopify-shop-domain": domain,
            "x-shopify-webhook-id": uuid.uuid4().hex,
        },
    )


REDACTED = (None, None, None, None, None, None, "GB")
INTACT = ("Jane Buyer", "Jane Buyer", "+44 7700 900000", "1 High Street", "London", "N1 1AA", "GB")


async def test_customer_redact_blanks_only_the_listed_orders_of_that_shop(
    client: AsyncClient,
) -> None:
    domain = f"redact-{uuid.uuid4().hex[:8]}.myshopify.com"
    tenant_id, store_id = await _shop(domain=domain)
    listed = await _order(tenant_id, store_id, "5001")
    other = await _order(tenant_id, store_id, "5002")
    # Another workspace's order carrying the same Shopify order id.
    foreign_tenant, foreign_store = await _shop(
        domain=f"other-{uuid.uuid4().hex[:8]}.myshopify.com"
    )
    foreign = await _order(foreign_tenant, foreign_store, "5001")

    response = await _post(
        client,
        "customers/redact",
        domain,
        {
            "shop_domain": domain,
            "customer": {"id": 42, "email": "jane@example.com", "phone": "+44"},
            "orders_to_redact": [5001],
        },
    )
    assert response.status_code == 200, response.text
    assert await _buyer(listed) == REDACTED
    assert await _buyer(other) == INTACT
    assert await _buyer(foreign) == INTACT


async def test_shop_redact_works_after_uninstall_removed_the_connection(
    client: AsyncClient,
) -> None:
    domain = f"gone-{uuid.uuid4().hex[:8]}.myshopify.com"
    tenant_id, store_id = await _shop(domain=domain, connected=False)
    a = await _order(tenant_id, store_id, "7001")
    b = await _order(tenant_id, store_id, "7002")

    response = await _post(client, "shop/redact", domain, {"shop_domain": domain, "shop_id": 1})
    assert response.status_code == 200, response.text
    assert await _buyer(a) == REDACTED
    assert await _buyer(b) == REDACTED


async def test_data_request_is_referred_to_the_merchant_without_contact_details(
    client: AsyncClient,
) -> None:
    domain = f"ask-{uuid.uuid4().hex[:8]}.myshopify.com"
    tenant_id, store_id = await _shop(domain=domain)
    order_id = await _order(tenant_id, store_id, "9001")

    response = await _post(
        client,
        "customers/data_request",
        domain,
        {
            "shop_domain": domain,
            "customer": {"id": 77, "email": "jane@example.com", "phone": "+44 7700"},
            "orders_requested": [9001],
            "data_request": {"id": 123},
        },
    )
    assert response.status_code == 200, response.text
    async with transaction() as session:
        note = (
            await session.execute(
                sa.select(Notification).where(Notification.tenant_id == tenant_id)
            )
        ).scalar_one()
    assert note.payload["customer_id"] == "77"
    assert note.payload["orders_requested"] == ["9001"]
    assert note.payload["data_request_id"] == "123"
    assert "jane@example.com" not in json.dumps(note.payload) + note.body + note.title
    assert "7700" not in json.dumps(note.payload) + note.body
    assert await _buyer(order_id) == INTACT  # a request is not an erasure


async def test_a_bad_signature_changes_nothing(client: AsyncClient) -> None:
    domain = f"forged-{uuid.uuid4().hex[:8]}.myshopify.com"
    tenant_id, store_id = await _shop(domain=domain)
    order_id = await _order(tenant_id, store_id, "1")

    response = await _post(
        client, "shop/redact", domain, {"shop_domain": domain, "shop_id": 1}, sign=False
    )
    assert response.status_code == 401
    assert await _buyer(order_id) == INTACT
