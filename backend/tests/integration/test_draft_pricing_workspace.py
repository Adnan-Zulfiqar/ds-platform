"""Draft pricing workspace — Decimal-safe apply/preview."""

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


async def _seed(client: AsyncClient, db_session: AsyncSession) -> tuple[dict[str, str], Product]:
    body = await register(client)
    headers = auth_header(body)
    tenant_id = uuid.UUID(str(body["identity"]["tenant"]["id"]))
    set_tenant_id(tenant_id)
    product = Product(
        tenant_id=tenant_id,
        source=ProductSource.MANUAL,
        external_id=f"manual-{uuid.uuid4().hex[:12]}",
        title="Pricing workspace fixture",
        status=ProductStatus.DRAFT,
        currency="USD",
        cost_price_min=Decimal("10.0000"),
        cost_price_max=Decimal("12.0000"),
        stock_quantity=5,
        shipping_cost=None,
    )
    db_session.add(product)
    await db_session.flush()
    db_session.add(
        ProductVariant(
            tenant_id=tenant_id,
            product_id=product.id,
            external_variant_id="sku-1",
            label="Color: Black",
            cost_price=Decimal("10.0000"),
            list_price=Decimal("14.0000"),
            currency="USD",
            stock_quantity=5,
            is_enabled=True,
        )
    )
    await db_session.flush()
    return headers, product


class TestDraftPricingWorkspace:
    async def test_pricing_workspace_warns_when_shipping_unknown(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, product = await _seed(client, db_session)
        response = await client.get(f"{DRAFTS_URL}/{product.id}/pricing", headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["shippingCostAvailable"] is False
        assert body["shippingWarning"]
        assert body["variants"][0]["supplierCost"] == "10.0000"
        assert body["variants"][0]["manualOverride"] is False

    async def test_apply_percentage_markup_sets_sell_price(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, product = await _seed(client, db_session)
        response = await client.post(
            f"{DRAFTS_URL}/{product.id}/pricing/apply",
            headers=headers,
            json={
                "mode": "percentage_markup",
                "markupPercent": "50",
                "roundToCents": True,
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        row = body["variants"][0]
        assert row["sellPrice"] == "15.00"
        assert row["profit"] == "5.0000"
        assert row["marginPercent"] == "33.33"
