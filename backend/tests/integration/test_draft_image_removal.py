"""A removed draft image is gone from every read the editor and channels use.

Removal is a soft delete (a supplier image can be restored). The draft detail
read loads ``Product.images``, which is not filtered by ``deleted_at``, while
publish readiness counts only live images. These tests pin the merchant-visible
contract: after removal, the image is absent from the removal response and
from ``GET /drafts/{id}``, and the remaining image leads at position 0.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import set_tenant_id
from app.models.product import Product, ProductImage, ProductSource, ProductStatus
from tests.integration.test_products import auth_header, register

pytestmark = pytest.mark.integration

DRAFTS_URL = "/api/v1/drafts"


async def _draft_with_two_images(
    client: AsyncClient, db_session: AsyncSession
) -> tuple[dict[str, str], Product, ProductImage, ProductImage]:
    body = await register(client)
    headers = auth_header(body)
    tenant_id = uuid.UUID(str(body["identity"]["tenant"]["id"]))
    set_tenant_id(tenant_id)
    product = Product(
        tenant_id=tenant_id,
        source=ProductSource.MANUAL,
        external_id=f"manual-{uuid.uuid4().hex[:12]}",
        title="Draft with images",
        status=ProductStatus.DRAFT,
    )
    db_session.add(product)
    await db_session.flush()
    first = ProductImage(
        tenant_id=tenant_id, product_id=product.id, url="https://cdn.example.com/a.jpg", position=0
    )
    second = ProductImage(
        tenant_id=tenant_id, product_id=product.id, url="https://cdn.example.com/b.jpg", position=1
    )
    db_session.add_all([first, second])
    await db_session.flush()
    return headers, product, first, second


async def test_a_removed_image_is_absent_from_the_removal_response_and_the_draft(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers, product, first, second = await _draft_with_two_images(client, db_session)

    removed = await client.delete(f"{DRAFTS_URL}/{product.id}/images/{first.id}", headers=headers)
    assert removed.status_code == 200, removed.text
    assert [image["id"] for image in removed.json()["images"]] == [str(second.id)], (
        f"first={first.id} second={second.id} body={removed.json()['images']}"
    )

    detail = await client.get(f"{DRAFTS_URL}/{product.id}", headers=headers)
    assert detail.status_code == 200, detail.text
    images = detail.json()["images"]
    assert [image["id"] for image in images] == [str(second.id)]
    assert images[0]["position"] == 0


async def test_a_restored_image_comes_back(client: AsyncClient, db_session: AsyncSession) -> None:
    headers, product, first, second = await _draft_with_two_images(client, db_session)
    await client.delete(f"{DRAFTS_URL}/{product.id}/images/{first.id}", headers=headers)

    restored = await client.post(
        f"{DRAFTS_URL}/{product.id}/images/{first.id}/restore", headers=headers
    )
    assert restored.status_code == 200, restored.text
    assert {image["id"] for image in restored.json()["images"]} == {str(first.id), str(second.id)}
