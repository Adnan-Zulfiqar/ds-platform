"""Stage 4 — draft media reorder and variant merchant fields."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import set_tenant_id
from app.models.product import Product, ProductImage, ProductSource, ProductStatus, ProductVariant
from tests.integration.test_products import auth_header, register

pytestmark = pytest.mark.integration

DRAFTS_URL = "/api/v1/drafts"


async def _seed(
    client: AsyncClient, db_session: AsyncSession
) -> tuple[dict[str, str], Product, list[ProductImage], ProductVariant]:
    body = await register(client)
    headers = auth_header(body)
    tenant_id = uuid.UUID(str(body["identity"]["tenant"]["id"]))
    set_tenant_id(tenant_id)
    product = Product(
        tenant_id=tenant_id,
        source=ProductSource.MANUAL,
        external_id=f"manual-{uuid.uuid4().hex[:12]}",
        title="Media variant draft",
        status=ProductStatus.DRAFT,
    )
    db_session.add(product)
    await db_session.flush()

    images = [
        ProductImage(
            tenant_id=tenant_id,
            product_id=product.id,
            url=f"https://cdn.example.com/{i}.jpg",
            position=i,
            is_supplier=True,
        )
        for i in range(3)
    ]
    for image in images:
        db_session.add(image)

    variant = ProductVariant(
        tenant_id=tenant_id,
        product_id=product.id,
        external_variant_id="sku-1",
        label="Color: Black / Size: M",
        cost_price=Decimal("4.22"),
        list_price=Decimal("5.85"),
        currency="CNY",
        stock_quantity=12,
        is_enabled=True,
    )
    db_session.add(variant)
    await db_session.flush()
    return headers, product, images, variant


class TestDraftMediaAndVariants:
    async def test_reorder_images_sets_featured(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, product, images, _variant = await _seed(client, db_session)
        order = [str(images[2].id), str(images[0].id), str(images[1].id)]

        response = await client.patch(
            f"{DRAFTS_URL}/{product.id}/images/reorder",
            headers=headers,
            json={"imageIds": order},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        positions = {item["id"]: item["position"] for item in body["images"]}
        assert positions[str(images[2].id)] == 0
        assert positions[str(images[0].id)] == 1

    async def test_add_merchant_image_and_patch_variant(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, product, _images, variant = await _seed(client, db_session)

        added = await client.post(
            f"{DRAFTS_URL}/{product.id}/images",
            headers=headers,
            json={
                "url": "https://cdn.example.com/merchant.png",
                "altText": "Hero",
            },
        )
        assert added.status_code == 200, added.text
        merchant = next(item for item in added.json()["images"] if not item["isSupplier"])
        assert merchant["altText"] == "Hero"

        patched = await client.patch(
            f"{DRAFTS_URL}/{product.id}/variants/{variant.id}",
            headers=headers,
            json={
                "merchantSku": "DP-BLACK-M",
                "sellPrice": "19.99",
                "compareAtPrice": "29.99",
                "isEnabled": True,
            },
        )
        assert patched.status_code == 200, patched.text
        row = next(item for item in patched.json()["variants"] if item["id"] == str(variant.id))
        assert row["merchantSku"] == "DP-BLACK-M"
        assert row["sellPrice"] == "19.9900" or row["sellPrice"].startswith("19.99")
        assert row["isEnabled"] is True

    async def test_reject_negative_sell_price(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, product, _images, variant = await _seed(client, db_session)
        response = await client.patch(
            f"{DRAFTS_URL}/{product.id}/variants/{variant.id}",
            headers=headers,
            json={"sellPrice": "-1"},
        )
        assert response.status_code == 422
