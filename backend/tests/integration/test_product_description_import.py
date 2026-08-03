"""Integration tests for AliExpress description import and sanitization
(Product Editor stage 1).

Reuses `test_products.py`'s AliExpress-mocking apparatus (a captured real
payload behind a mocked transport) to get a genuine imported product, the
same pattern `test_product_optimization.py` already establishes.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fakeredis import aioredis as fake_aioredis
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.aliexpress import service as service_module
from app.models.product import Product
from tests.integration.test_products import REAL_PRODUCT_ID, connected_tenant

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> fake_aioredis.FakeRedis:
    """In-process Redis for the OAuth state store — same fixture
    `test_products.py` defines, replicated here rather than imported: an
    `autouse` fixture must live in the module pytest is collecting."""
    redis = fake_aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(service_module, "get_redis", lambda _purpose: redis)
    return redis


@pytest.fixture(autouse=True)
def _allow_outbound(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bypass the outbound rate limiter, which has its own tests."""
    from app.integrations.rate_limiter import RateLimitDecision

    async def _allow(self: Any, tenant_id: str) -> RateLimitDecision:
        return RateLimitDecision(allowed=True, remaining=99, retry_after_seconds=0)

    monkeypatch.setattr("app.integrations.rate_limiter.OutboundRateLimiter.acquire", _allow)


async def import_a_product(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict[str, str], dict[str, object]]:
    headers = await connected_tenant(client, monkeypatch)
    response = await client.post(
        "/api/v1/products/import", json={"externalId": REAL_PRODUCT_ID}, headers=headers
    )
    assert response.status_code == 201, response.text
    return headers, response.json()


class TestFirstImport:
    async def test_populates_both_editable_and_supplier_description(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, product = await import_a_product(client, monkeypatch)

        assert product["description"], "the real fixture's `detail` HTML should be imported"
        assert product["supplierDescription"] == product["description"]

    async def test_description_is_sanitized_not_raw_supplier_html(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The real fixture's `detail` wraps everything in
        `<div class="detailmodule_html">...` -- `div` is not in the
        sanitizer's allowlist."""
        _, product = await import_a_product(client, monkeypatch)

        assert "<div" not in product["description"]
        assert "<img" in product["description"]

    async def test_list_endpoint_does_not_carry_the_description_body(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`description` is detail-only, the same reasoning `variants`/
        `images` already follow -- a list page for dozens of products should
        not carry a full description body per row."""
        headers, _ = await import_a_product(client, monkeypatch)

        response = await client.get("/api/v1/products", headers=headers)
        assert response.status_code == 200, response.text
        assert "description" not in response.json()["items"][0]


class TestResyncOverwriteProtection:
    async def test_a_resync_with_no_merchant_edit_keeps_refreshing_normally(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)

        response = await client.post(f"/api/v1/products/{product['id']}/sync", headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["description"] == product["description"]
        assert response.json()["supplierDescription"] == product["supplierDescription"]

    async def test_a_resync_does_not_overwrite_a_diverged_description(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
        db_session: AsyncSession,
    ) -> None:
        """No write API exists yet (stage 1 deliberately stops before it), so
        a merchant edit is simulated by writing `description` directly and
        leaving `supplier_description` at its imported value -- exactly the
        divergence a future `PATCH` would create. `db_session` is the same
        session the `client` fixture wires into the app's dependency
        override, so a flush here is visible to the next request without a
        commit.
        """
        headers, product = await import_a_product(client, monkeypatch)
        product_id = uuid.UUID(product["id"])

        row = (
            await db_session.execute(select(Product).where(Product.id == product_id))
        ).scalar_one()
        merchant_edit = "<p>Rewritten by the merchant, not the supplier.</p>"
        row.description = merchant_edit
        await db_session.flush()

        response = await client.post(f"/api/v1/products/{product_id}/sync", headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["description"] == merchant_edit
        # The supplier snapshot still refreshes to what the supplier
        # currently says, independent of the merchant's edit.
        assert body["supplierDescription"] == product["supplierDescription"]
        assert body["supplierDescription"] != merchant_edit

    async def test_a_resync_after_reverting_to_the_supplier_snapshot_refreshes_again(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
        db_session: AsyncSession,
    ) -> None:
        """Once `description` is made to match `supplier_description` again
        (a merchant explicitly reverting their edit, once that UI exists),
        sync resumes refreshing it -- divergence is judged by equality, not
        by a one-way flag."""
        headers, product = await import_a_product(client, monkeypatch)
        product_id = uuid.UUID(product["id"])

        row = (
            await db_session.execute(select(Product).where(Product.id == product_id))
        ).scalar_one()
        row.description = "<p>Temporary edit.</p>"
        await db_session.flush()

        # Revert it back to match the supplier snapshot exactly.
        row.description = row.supplier_description
        await db_session.flush()

        response = await client.post(f"/api/v1/products/{product_id}/sync", headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["description"] == response.json()["supplierDescription"]


class TestTenantIsolation:
    async def test_a_products_description_is_not_visible_to_another_tenant(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No new repository was added for this stage -- `description`/
        `supplier_description` are plain columns on the already tenant-scoped
        `products` table, read through the same `ProductRepository` every
        other field already goes through. This exercises that existing
        boundary rather than asserting a new one."""
        from tests.integration.test_products import auth_header, register

        _, product = await import_a_product(client, monkeypatch)
        other_tenant = await register(client)

        response = await client.get(
            f"/api/v1/products/{product['id']}", headers=auth_header(other_tenant)
        )
        assert response.status_code == 404, response.text
