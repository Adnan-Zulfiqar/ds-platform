"""Drafts vs Products publication projections (Product Workspace V2 Stage 0).

Seeds catalogue rows directly rather than through AliExpress import so the
suite does not require platform ``ALIEXPRESS_APP_*`` credentials. Publication
membership is entirely about ``StoreListing`` state.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import set_tenant_id
from app.models.product import Product, ProductSource, ProductStatus
from app.models.shopify import ListingSyncStatus, StoreListing
from app.models.store import Store, StorePlatform, StoreStatus
from tests.integration.test_products import PRODUCTS_URL, auth_header, register

pytestmark = pytest.mark.integration

DRAFTS_URL = "/api/v1/drafts"
COUNTS_URL = "/api/v1/products/workspace-counts"


async def _register_tenant(client: AsyncClient) -> tuple[dict[str, str], uuid.UUID]:
    body = await register(client)
    tenant_id = uuid.UUID(str(body["identity"]["tenant"]["id"]))
    return auth_header(body), tenant_id


async def _seed_draft_product(db_session: AsyncSession, *, tenant_id: uuid.UUID) -> Product:
    set_tenant_id(tenant_id)
    product = Product(
        tenant_id=tenant_id,
        source=ProductSource.MANUAL,
        external_id=f"manual-{uuid.uuid4().hex[:12]}",
        title="Workspace draft product",
        status=ProductStatus.DRAFT,
    )
    db_session.add(product)
    await db_session.flush()
    return product


async def _attach_listing(
    db_session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    product_id: uuid.UUID,
    status: ListingSyncStatus,
) -> None:
    set_tenant_id(tenant_id)
    store = Store(
        tenant_id=tenant_id,
        name="Workspace test store",
        slug=f"ws-{uuid.uuid4().hex[:12]}",
        platform=StorePlatform.SHOPIFY,
        status=StoreStatus.CONNECTED,
    )
    db_session.add(store)
    await db_session.flush()

    db_session.add(
        StoreListing(
            tenant_id=tenant_id,
            store_id=store.id,
            product_id=product_id,
            external_product_id=f"gid://shopify/Product/{uuid.uuid4().hex[:8]}",
            external_variant_map={},
            inventory_item_map={},
            status=status,
        )
    )
    # Flush only — the integration fixture owns a connection-level transaction
    # that rolls back after the test; commit would detach from that envelope.
    await db_session.flush()


class TestDraftVersusPublishedLists:
    async def test_imported_product_appears_in_drafts_only(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await _register_tenant(client)
        product = await _seed_draft_product(db_session, tenant_id=tenant_id)

        drafts = await client.get(DRAFTS_URL, headers=headers)
        products = await client.get(PRODUCTS_URL, headers=headers)
        counts = await client.get(COUNTS_URL, headers=headers)

        assert drafts.status_code == 200
        assert products.status_code == 200
        assert counts.status_code == 200

        draft_ids = {item["id"] for item in drafts.json()["items"]}
        product_ids = {item["id"] for item in products.json()["items"]}

        assert str(product.id) in draft_ids
        assert str(product.id) not in product_ids
        assert counts.json() == {"drafts": 1, "products": 0}

    async def test_synced_listing_moves_product_to_products_list(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await _register_tenant(client)
        product = await _seed_draft_product(db_session, tenant_id=tenant_id)
        await _attach_listing(
            db_session,
            tenant_id=tenant_id,
            product_id=product.id,
            status=ListingSyncStatus.SYNCED,
        )

        drafts = await client.get(DRAFTS_URL, headers=headers)
        products = await client.get(PRODUCTS_URL, headers=headers)
        counts = await client.get(COUNTS_URL, headers=headers)

        draft_ids = {item["id"] for item in drafts.json()["items"]}
        product_ids = {item["id"] for item in products.json()["items"]}

        assert str(product.id) not in draft_ids
        assert str(product.id) in product_ids
        assert counts.json() == {"drafts": 0, "products": 1}

    async def test_failed_listing_keeps_product_in_drafts(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await _register_tenant(client)
        product = await _seed_draft_product(db_session, tenant_id=tenant_id)
        await _attach_listing(
            db_session,
            tenant_id=tenant_id,
            product_id=product.id,
            status=ListingSyncStatus.ERROR,
        )

        drafts = await client.get(DRAFTS_URL, headers=headers)
        products = await client.get(PRODUCTS_URL, headers=headers)

        draft_ids = {item["id"] for item in drafts.json()["items"]}
        product_ids = {item["id"] for item in products.json()["items"]}

        assert str(product.id) in draft_ids
        assert str(product.id) not in product_ids

    async def test_drafts_list_requires_authentication(self, client: AsyncClient) -> None:
        assert (await client.get(DRAFTS_URL)).status_code == 401
