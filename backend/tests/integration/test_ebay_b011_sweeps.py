"""B-011 — the scheduled eBay jobs find their work correctly and narrowly.

The cross-tenant sweep returns tenant ids only; the per-tenant lookup stays
inside the tenant. Built on the C3 harness (a published eBay listing).
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import set_tenant_id
from app.models.store import StorePlatform
from app.repositories.ebay import EbayConnectedTenantsSweep
from app.repositories.shopify import StoreListingRepository
from tests.integration.ebay_c1_live import install
from tests.integration.test_ebay_c3_publish import (
    PUBLISH_URL,
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


@pytest.fixture
def ebay(monkeypatch: pytest.MonkeyPatch) -> PublishFakeEbay:
    fake = PublishFakeEbay()
    install(monkeypatch, fake)
    return fake


async def test_the_sweep_finds_connected_tenants_and_the_lookup_stays_inside_one(
    client: AsyncClient, db_session: AsyncSession, ebay: PublishFakeEbay
) -> None:
    headers, product_id, store_id = await prepared(client, db_session)
    payload = {"productId": str(product_id), "storeId": str(store_id)}
    assert (await client.post(PUBLISH_URL, json=payload, headers=headers)).status_code == 200
    me = await client.get("/api/v1/auth/me", headers=headers)
    tenant_id = uuid.UUID(me.json()["tenant"]["id"])

    tenants = await EbayConnectedTenantsSweep(db_session).connected_tenant_ids()
    assert tenant_id in tenants

    set_tenant_id(tenant_id)
    ids = await StoreListingRepository(db_session).product_ids_on_platform(StorePlatform.EBAY)
    assert ids == [product_id]

    set_tenant_id(uuid.uuid4())  # another workspace sees nothing
    assert (
        await StoreListingRepository(db_session).product_ids_on_platform(StorePlatform.EBAY) == []
    )
