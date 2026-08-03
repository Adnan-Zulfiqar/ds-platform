"""Integration coverage for Shopify webhook processing fixes.

- A-09: replay-check failure fails **closed** for mutating topics, open for
  non-mutating ones.
- Phase 8.1: ``app/uninstalled`` releases the global shop claim (deletes the
  connection) rather than leaving an ERROR row that still blocks other tenants.

Reuses the direct-DB connection-seeding pattern from
`test_shopify_shop_domain_db.py` rather than a live OAuth round trip — this
is about webhook processing, not the OAuth flow itself.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import uuid

import pytest
from fakeredis import aioredis as fake_aioredis
from httpx import AsyncClient
from pydantic import SecretStr

from app.core.context import clear_context, set_tenant_id
from app.core.encryption import encrypt
from app.database.session import transaction
from app.models.integration import IntegrationStatus
from app.models.shopify import ShopifyConnection
from app.models.store import Store, StorePlatform, StoreStatus
from app.models.tenant import Tenant, TenantStatus
from app.repositories.shopify import ShopifyConnectionRepository

pytestmark = pytest.mark.integration

WEBHOOK_SECRET = "shopify-webhook-processing-test-secret"


def _sign(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


async def _insert_connected_shop(*, shop_domain: str) -> tuple[uuid.UUID, uuid.UUID]:
    """Create a tenant, store, and Shopify connection in a **genuinely
    committed** transaction.

    The webhook handler reads through its own separate `transaction()` call
    (correctly — a webhook is not part of an authenticated HTTP request's
    session), which means it can only see rows that were actually committed,
    not rows sitting in another test's still-open, roll-back-only `db_session`
    transaction. This is why setup here does not use the `client`/`db_session`
    fixtures: this test is specifically about cross-session visibility, so
    the setup has to be cross-session-visible too.

    Returns `(tenant_id, connection_id)`.
    """
    async with transaction() as session:
        tenant = Tenant(
            name=f"Webhook Test {shop_domain}",
            slug=f"webhook-test-{uuid.uuid4().hex[:10]}",
            status=TenantStatus.ACTIVE,
        )
        session.add(tenant)
        await session.flush()

        store = Store(
            tenant_id=tenant.id,
            name=shop_domain,
            slug=f"slug-{uuid.uuid4().hex[:8]}",
            platform=StorePlatform.SHOPIFY,
            status=StoreStatus.CONNECTED,
            storefront_url=f"https://{shop_domain}",
            external_store_id=shop_domain,
        )
        session.add(store)
        await session.flush()

        connection = ShopifyConnection(
            tenant_id=tenant.id,
            store_id=store.id,
            shop_domain=shop_domain,
            encrypted_access_token=encrypt("shpat_test_token_not_real"),
            scopes="read_products",
            status=IntegrationStatus.CONNECTED,
        )
        session.add(connection)
        await session.flush()
        return tenant.id, connection.id


class _FailingRedis:
    """Stands in for a Redis client that cannot reach the server."""

    async def set(self, *args: object, **kwargs: object) -> None:
        from redis.exceptions import RedisError

        raise RedisError("simulated outage")


class TestReplayFailClosed:
    async def test_a_redis_outage_fails_closed_for_a_mutating_topic(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.integrations.shopify.webhook.settings.shopify.api_secret",
            SecretStr(WEBHOOK_SECRET),
        )
        monkeypatch.setattr(
            "app.integrations.shopify.webhook.get_redis", lambda _purpose: _FailingRedis()
        )

        body = b'{"id": 1}'
        response = await client.post(
            "/api/v1/integrations/shopify/webhooks/orders-create",
            content=body,
            headers={
                "content-type": "application/json",
                "x-shopify-hmac-sha256": _sign(body),
                "x-shopify-shop-domain": "does-not-matter.myshopify.com",
            },
        )

        # A mutating topic must not be silently acknowledged when the
        # dedup store cannot be trusted — Shopify retries a 5xx on its own
        # schedule, which is the correct outcome here.
        assert response.status_code == 503, response.text

    async def test_a_redis_outage_does_not_block_a_non_mutating_topic(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.integrations.shopify.webhook.settings.shopify.api_secret",
            SecretStr(WEBHOOK_SECRET),
        )
        monkeypatch.setattr(
            "app.integrations.shopify.webhook.get_redis", lambda _purpose: _FailingRedis()
        )

        body = b'{"id": 1}'
        response = await client.post(
            "/api/v1/integrations/shopify/webhooks/products-update",
            content=body,
            headers={
                "content-type": "application/json",
                "x-shopify-hmac-sha256": _sign(body),
                "x-shopify-shop-domain": "does-not-matter.myshopify.com",
            },
        )

        assert response.status_code == 200, response.text
        assert response.json() == {"status": "received"}


class TestAppUninstalled:
    async def test_releases_the_shop_claim(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.integrations.shopify.webhook.settings.shopify.api_secret",
            SecretStr(WEBHOOK_SECRET),
        )
        monkeypatch.setattr(
            "app.integrations.shopify.webhook.get_redis",
            lambda _purpose: fake_aioredis.FakeRedis(decode_responses=True),
        )

        domain = f"uninstall-{uuid.uuid4().hex[:10]}.myshopify.com"
        tenant_id, connection_id = await _insert_connected_shop(shop_domain=domain)

        body = f'{{"id": 999, "domain": "{domain}"}}'.encode()
        response = await client.post(
            "/api/v1/integrations/shopify/webhooks/app-uninstalled",
            content=body,
            headers={
                "content-type": "application/json",
                "x-shopify-hmac-sha256": _sign(body),
                "x-shopify-shop-domain": domain,
            },
        )
        assert response.status_code == 200, response.text

        async with transaction() as session:
            set_tenant_id(tenant_id)
            try:
                refreshed = await ShopifyConnectionRepository(session).get_by_shop_domain(domain)
            finally:
                clear_context()

            assert refreshed is None

        # Global claim released — maintenance lookup must also be empty.
        async with transaction() as session:
            from app.repositories.shopify import ShopifyMaintenanceRepository

            owner = await ShopifyMaintenanceRepository(session).get_by_shop_domain(domain)
            assert owner is None
            assert connection_id  # was created; now gone
