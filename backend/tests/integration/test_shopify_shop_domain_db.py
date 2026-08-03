"""Integration coverage for Shopify shop-domain uniqueness (audit A-01)."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import encrypt
from app.models.integration import IntegrationStatus
from app.models.shopify import ShopifyConnection
from app.models.store import Store, StorePlatform, StoreStatus
from app.repositories.shopify import ShopifyMaintenanceRepository
from tests.integration.conftest import registration_payload

pytestmark = pytest.mark.integration


async def _register(client: AsyncClient, *, email: str, company: str) -> dict[str, object]:
    response = await client.post(
        "/api/v1/auth/register",
        json=registration_payload(email=email, companyName=company),
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _insert_connection(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    shop_domain: str,
    status: IntegrationStatus = IntegrationStatus.CONNECTED,
) -> ShopifyConnection:
    store = Store(
        tenant_id=tenant_id,
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
        tenant_id=tenant_id,
        store_id=store.id,
        shop_domain=shop_domain,
        encrypted_access_token=encrypt("shpat_test_token_not_real"),
        scopes="read_products",
        status=status,
    )
    session.add(connection)
    await session.flush()
    return connection


class TestShopifyShopDomainUniqueness:
    async def test_database_rejects_the_same_shop_on_a_second_tenant(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        first = await _register(client, email="a01-a@example.com", company="Tenant A")
        second = await _register(client, email="a01-b@example.com", company="Tenant B")
        tenant_a = uuid.UUID(str(first["identity"]["tenant"]["id"]))
        tenant_b = uuid.UUID(str(second["identity"]["tenant"]["id"]))
        domain = f"unique-{uuid.uuid4().hex[:10]}.myshopify.com"

        await _insert_connection(db_session, tenant_id=tenant_a, shop_domain=domain)

        with pytest.raises(IntegrityError):
            await _insert_connection(db_session, tenant_id=tenant_b, shop_domain=domain)
            await db_session.flush()

    async def test_webhook_lookup_returns_the_owning_tenant_only(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        first = await _register(client, email="a01-c@example.com", company="Tenant C")
        tenant_id = uuid.UUID(str(first["identity"]["tenant"]["id"]))
        domain = f"lookup-{uuid.uuid4().hex[:10]}.myshopify.com"

        created = await _insert_connection(db_session, tenant_id=tenant_id, shop_domain=domain)
        found = await ShopifyMaintenanceRepository(db_session).get_connected_by_shop_domain(domain)

        assert found is not None
        assert found.id == created.id
        assert found.tenant_id == tenant_id

        missing = await ShopifyMaintenanceRepository(db_session).get_connected_by_shop_domain(
            "no-such-shop.myshopify.com"
        )
        assert missing is None
