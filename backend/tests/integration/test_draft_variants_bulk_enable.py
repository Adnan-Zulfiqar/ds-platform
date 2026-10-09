"""Enable or disable many draft variants in one request ("select all")."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import set_tenant_id
from app.models.product import Product, ProductSource, ProductStatus, ProductVariant
from tests.integration.test_products import auth_header, register

pytestmark = pytest.mark.integration

DRAFTS_URL = "/api/v1/drafts"


async def _draft_with_variants(
    client: AsyncClient, db_session: AsyncSession, count: int = 4
) -> tuple[dict[str, str], Product, list[ProductVariant]]:
    body = await register(client)
    headers = auth_header(body)
    tenant_id = uuid.UUID(str(body["identity"]["tenant"]["id"]))
    set_tenant_id(tenant_id)
    product = Product(
        tenant_id=tenant_id,
        source=ProductSource.MANUAL,
        external_id=f"manual-{uuid.uuid4().hex[:12]}",
        title="Many variants",
        status=ProductStatus.DRAFT,
    )
    db_session.add(product)
    await db_session.flush()
    variants = [
        ProductVariant(
            tenant_id=tenant_id,
            product_id=product.id,
            external_variant_id=f"sku-{i}",
            label=f"Size: {i}",
            cost_price=Decimal("4.00"),
            currency="USD",
            stock_quantity=5,
            is_enabled=i % 2 == 0,
        )
        for i in range(count)
    ]
    db_session.add_all(variants)
    await db_session.flush()
    return headers, product, variants


def _enabled(body: dict[str, object]) -> list[bool]:
    rows = body["variants"]
    assert isinstance(rows, list)
    return [bool(v["isEnabled"]) for v in rows]


async def test_all_variants_are_enabled_then_disabled_in_one_request(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers, product, _ = await _draft_with_variants(client, db_session)
    on = await client.patch(
        f"{DRAFTS_URL}/{product.id}/variants", json={"enabled": True}, headers=headers
    )
    assert on.status_code == 200, on.text
    assert _enabled(on.json()) == [True] * 4

    off = await client.patch(
        f"{DRAFTS_URL}/{product.id}/variants", json={"enabled": False}, headers=headers
    )
    assert _enabled(off.json()) == [False] * 4


async def test_only_the_listed_variants_change(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers, product, variants = await _draft_with_variants(client, db_session)
    target = [str(variants[0].id), str(variants[2].id)]  # both start enabled
    response = await client.patch(
        f"{DRAFTS_URL}/{product.id}/variants",
        json={"enabled": False, "variantIds": target},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    by_id = {v["id"]: v["isEnabled"] for v in response.json()["variants"]}
    assert by_id[str(variants[0].id)] is False and by_id[str(variants[2].id)] is False
    assert by_id[str(variants[1].id)] is False and by_id[str(variants[3].id)] is False


async def test_an_unknown_variant_changes_nothing(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers, product, variants = await _draft_with_variants(client, db_session)
    response = await client.patch(
        f"{DRAFTS_URL}/{product.id}/variants",
        json={"enabled": True, "variantIds": [str(variants[1].id), str(uuid.uuid4())]},
        headers=headers,
    )
    assert response.status_code == 404
    detail = await client.get(f"{DRAFTS_URL}/{product.id}", headers=headers)
    by_id = {v["id"]: v["isEnabled"] for v in detail.json()["variants"]}
    assert by_id == {str(v.id): i % 2 == 0 for i, v in enumerate(variants)}


async def test_another_workspaces_draft_is_not_found(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, product, _ = await _draft_with_variants(client, db_session)
    stranger = auth_header(await register(client))
    response = await client.patch(
        f"{DRAFTS_URL}/{product.id}/variants", json={"enabled": False}, headers=stranger
    )
    assert response.status_code == 404
