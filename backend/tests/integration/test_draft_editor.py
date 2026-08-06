"""Draft editor write path — thin wrappers over ProductService."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import set_tenant_id
from app.models.product import Product, ProductSource, ProductStatus
from tests.integration.test_products import auth_header, register

pytestmark = pytest.mark.integration

DRAFTS_URL = "/api/v1/drafts"


async def _seed(client: AsyncClient, db_session: AsyncSession) -> tuple[dict[str, str], Product]:
    body = await register(client)
    headers = auth_header(body)
    tenant_id = uuid.UUID(str(body["identity"]["tenant"]["id"]))
    set_tenant_id(tenant_id)
    product = Product(
        tenant_id=tenant_id,
        source=ProductSource.MANUAL,
        external_id=f"manual-{uuid.uuid4().hex[:12]}",
        title="Editable draft title",
        status=ProductStatus.DRAFT,
        description="<p>Original</p>",
        supplier_description="<p>Original</p>",
        supplier_title="Editable draft title",
    )
    db_session.add(product)
    await db_session.flush()
    return headers, product


class TestDraftEditorApi:
    async def test_get_draft_returns_supplier_twins(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, product = await _seed(client, db_session)

        response = await client.get(f"{DRAFTS_URL}/{product.id}", headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["title"] == "Editable draft title"
        assert body["supplierTitle"] == "Editable draft title"
        assert body["description"] == "<p>Original</p>"

    async def test_patch_draft_persists_title_and_description(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, product = await _seed(client, db_session)

        response = await client.patch(
            f"{DRAFTS_URL}/{product.id}",
            headers=headers,
            json={
                "title": "Merchant edited title",
                "description": "<p>Merchant <strong>HTML</strong></p><script>alert(1)</script>",
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["title"] == "Merchant edited title"
        assert "Merchant" in (body["description"] or "")
        assert "<script" not in (body["description"] or "").lower()

        listed = await client.get(DRAFTS_URL, headers=headers)
        ids = {item["id"] for item in listed.json()["items"]}
        assert str(product.id) in ids

    async def test_draft_patch_requires_authentication(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        _, product = await _seed(client, db_session)
        response = await client.patch(
            f"{DRAFTS_URL}/{product.id}",
            json={"title": "Nope"},
        )
        assert response.status_code == 401
