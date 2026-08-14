"""Integration tests for `PATCH /products/{id}` (Product Editor stage 2).

Reuses `test_products.py`'s AliExpress-mocking apparatus to get a genuine
imported product, the same pattern the stage 1 description-import tests and
`test_product_optimization.py` already establish.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fakeredis import aioredis as fake_aioredis
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tokens import create_access_token
from app.integrations.aliexpress import service as service_module
from app.models.product import Product, ProductSource, ProductStatus
from tests.integration.test_products import (
    REAL_PRODUCT_ID,
    auth_header,
    connected_tenant,
    register,
)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> fake_aioredis.FakeRedis:
    """In-process Redis for the OAuth state store — replicated per-module,
    same reasoning `test_product_optimization.py` already documents."""
    redis = fake_aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(service_module, "get_redis", lambda _purpose: redis)
    return redis


@pytest.fixture(autouse=True)
def _allow_outbound(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.integrations.rate_limiter import RateLimitDecision

    async def _allow(self: Any, tenant_id: str) -> RateLimitDecision:
        return RateLimitDecision(allowed=True, remaining=99, retry_after_seconds=0)

    monkeypatch.setattr("app.integrations.rate_limiter.OutboundRateLimiter.acquire", _allow)


async def import_a_product(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict[str, str], dict[str, Any]]:
    headers = await connected_tenant(client, monkeypatch)
    response = await client.post(
        "/api/v1/products/import", json={"externalId": REAL_PRODUCT_ID}, headers=headers
    )
    assert response.status_code == 201, response.text
    return headers, response.json()


class TestPartialUpdate:
    async def test_updates_only_the_fields_sent(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)

        response = await client.patch(
            f"/api/v1/products/{product['id']}",
            json={"title": "Better Case Title"},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["title"] == "Better Case Title"
        # Nothing else moved. `costPriceMin` is compared as a Decimal, not a
        # string: a freshly-imported row and one reloaded from Postgres can
        # render the same `Numeric(16,4)` value with different trailing
        # zeros ("3.14" vs "3.1400") -- a serialization detail, not a sign
        # anything actually changed.
        assert body["brand"] == product["brand"]
        assert body["supplierName"] == product["supplierName"]
        assert Decimal(body["costPriceMin"]) == Decimal(product["costPriceMin"])

    async def test_updates_several_fields_at_once(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)

        response = await client.patch(
            f"/api/v1/products/{product['id']}",
            json={
                "title": "New Title",
                "brand": "Acme",
                "vendor": "Acme Supply Co",
                "tags": ["phone-case", "clear"],
                "seoTitle": "Clear Phone Case | Acme",
                "slug": "clear-phone-case-acme",
                "status": "active",
            },
            headers=headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["title"] == "New Title"
        assert body["brand"] == "Acme"
        assert body["vendor"] == "Acme Supply Co"
        assert body["tags"] == ["phone-case", "clear"]
        assert body["seoTitle"] == "Clear Phone Case | Acme"
        assert body["slug"] == "clear-phone-case-acme"
        assert body["status"] == "active"

    async def test_an_empty_body_changes_nothing(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)

        response = await client.patch(f"/api/v1/products/{product['id']}", json={}, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["title"] == product["title"]

    async def test_description_is_sanitized_on_write(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)

        response = await client.patch(
            f"/api/v1/products/{product['id']}",
            json={"description": "<p>Great case</p><script>steal(document.cookie)</script>"},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()

        assert "Great case" in body["description"]
        assert "script" not in body["description"].lower()
        assert "steal" not in body["description"]

    async def test_description_can_be_cleared(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)
        assert product["description"], "the fixture should have imported a real description"

        response = await client.patch(
            f"/api/v1/products/{product['id']}", json={"description": ""}, headers=headers
        )
        assert response.status_code == 200, response.text
        assert response.json()["description"] is None


class TestValidation:
    async def test_title_cannot_be_blanked(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)

        response = await client.patch(
            f"/api/v1/products/{product['id']}", json={"title": ""}, headers=headers
        )
        assert response.status_code == 422, response.text

    async def test_a_whitespace_only_title_is_rejected(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """M2A: `str_strip_whitespace` (`AppBaseModel`) trims `"   "` to `""`
        before `_reject_blank_when_provided` ever sees it, so this is already
        covered by the same validator as an explicit empty string -- proven
        here directly rather than assumed from reading the config."""
        headers, product = await import_a_product(client, monkeypatch)

        response = await client.patch(
            f"/api/v1/products/{product['id']}", json={"title": "   "}, headers=headers
        )
        assert response.status_code == 422, response.text

    @pytest.mark.parametrize("field", ["brand", "categoryName", "vendor", "seoTitle", "slug"])
    async def test_plain_text_fields_reject_an_explicit_empty_string(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch, field: str
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)

        response = await client.patch(
            f"/api/v1/products/{product['id']}", json={field: ""}, headers=headers
        )
        assert response.status_code == 422, response.text

    async def test_an_unknown_status_value_is_rejected(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)

        response = await client.patch(
            f"/api/v1/products/{product['id']}", json={"status": "published"}, headers=headers
        )
        assert response.status_code == 422, response.text

    async def test_an_unrecognised_field_is_rejected(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`extra=forbid` on the shared schema base -- a typo like
        `quantitiy` should be a 422, not a silently dropped field."""
        headers, product = await import_a_product(client, monkeypatch)

        response = await client.patch(
            f"/api/v1/products/{product['id']}", json={"stockQuantity": 999}, headers=headers
        )
        assert response.status_code == 422, response.text


async def _second_product_for_same_tenant(
    db_session: AsyncSession, existing_product_id: str
) -> uuid.UUID:
    """A second, minimal product owned by the same tenant as an already-
    imported one.

    Not a second real import: the mocked AliExpress transport always answers
    with the same fixture regardless of the requested id, and `map_product`
    keys the stored `external_id` off the *parsed response* (`detail.product_id`),
    not the request parameter -- so a second `POST /import` with a different
    `externalId` would just upsert the same row again, not create a second
    one. A direct insert sidesteps that entirely for what this test actually
    needs: two distinct rows in one tenant.
    """
    existing = (
        await db_session.execute(
            select(Product).where(Product.id == uuid.UUID(existing_product_id))
        )
    ).scalar_one()

    second = Product(
        tenant_id=existing.tenant_id,
        source=ProductSource.MANUAL,
        external_id=f"manual-{uuid.uuid4().hex[:12]}",
        title="A second product",
        status=ProductStatus.DRAFT,
    )
    db_session.add(second)
    await db_session.flush()
    return second.id


class TestOptimisticConcurrency:
    """Full coverage of the compare-and-swap mechanism lives in
    `test_draft_editor_concurrency.py` against `/drafts/{id}` -- the actual
    M2A editor surface. This proves the same shared `ProductService.update_product`
    path stays available (and backward compatible) from `/products/{id}` too."""

    async def test_a_stale_expected_updated_at_is_rejected(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
        db_session: AsyncSession,
    ) -> None:
        """A concurrent write is simulated as a direct row update rather
        than a second PATCH -- this test's fixtures share one transaction,
        and Postgres's `now()` is frozen for its duration, so a second HTTP
        call could not actually move `updatedAt` here regardless of whether
        the compare-and-swap works. See the equivalent, more detailed
        docstring in `test_draft_editor_concurrency.py`."""
        headers, product = await import_a_product(client, monkeypatch)

        concurrent_write_at = datetime.fromisoformat(product["updatedAt"]) + timedelta(seconds=5)
        await db_session.execute(
            update(Product)
            .where(Product.id == uuid.UUID(product["id"]))
            .values(updated_at=concurrent_write_at)
        )
        await db_session.flush()

        stale = await client.patch(
            f"/api/v1/products/{product['id']}",
            json={"title": "Stale Edit", "expectedUpdatedAt": product["updatedAt"]},
            headers=headers,
        )
        assert stale.status_code == 409, stale.text


class TestSlugConflict:
    async def test_a_duplicate_slug_within_the_same_tenant_is_rejected(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession
    ) -> None:
        headers, first = await import_a_product(client, monkeypatch)
        await client.patch(
            f"/api/v1/products/{first['id']}", json={"slug": "taken-slug"}, headers=headers
        )

        second_id = await _second_product_for_same_tenant(db_session, first["id"])

        response = await client.patch(
            f"/api/v1/products/{second_id}", json={"slug": "taken-slug"}, headers=headers
        )
        assert response.status_code == 409, response.text

    async def test_the_same_product_keeping_its_own_slug_is_not_a_conflict(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)
        await client.patch(
            f"/api/v1/products/{product['id']}", json={"slug": "my-slug"}, headers=headers
        )

        response = await client.patch(
            f"/api/v1/products/{product['id']}",
            json={"slug": "my-slug", "vendor": "Someone"},
            headers=headers,
        )
        assert response.status_code == 200, response.text

    async def test_a_duplicate_slug_across_tenants_is_allowed(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Uniqueness is per-tenant (`uq_products_tenant_slug`); two
        different workspaces choosing the same human-readable slug is not a
        conflict."""
        headers_a, product_a = await import_a_product(client, monkeypatch)
        await client.patch(
            f"/api/v1/products/{product_a['id']}", json={"slug": "shared-slug"}, headers=headers_a
        )

        headers_b = await connected_tenant(client, monkeypatch)
        product_b = (
            await client.post(
                "/api/v1/products/import", json={"externalId": REAL_PRODUCT_ID}, headers=headers_b
            )
        ).json()

        response = await client.patch(
            f"/api/v1/products/{product_b['id']}", json={"slug": "shared-slug"}, headers=headers_b
        )
        assert response.status_code == 200, response.text


class TestResyncDoesNotOverwriteEditedFields:
    async def test_editing_title_and_brand_survives_a_resync(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)

        edited = await client.patch(
            f"/api/v1/products/{product['id']}",
            json={"title": "Merchant Rewritten Title", "brand": "Merchant Brand"},
            headers=headers,
        )
        assert edited.status_code == 200, edited.text

        resynced = await client.post(f"/api/v1/products/{product['id']}/sync", headers=headers)
        assert resynced.status_code == 200, resynced.text
        body = resynced.json()

        assert body["title"] == "Merchant Rewritten Title"
        assert body["brand"] == "Merchant Brand"

    async def test_a_field_never_edited_keeps_refreshing_normally(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
        db_session: AsyncSession,
    ) -> None:
        """Only the edited field freezes -- an unedited field on the same
        product keeps tracking the supplier, proving this is per-field
        divergence, not an all-or-nothing lock on the whole row."""
        headers, product = await import_a_product(client, monkeypatch)

        await client.patch(
            f"/api/v1/products/{product['id']}", json={"title": "Edited Title"}, headers=headers
        )

        resynced = await client.post(f"/api/v1/products/{product['id']}/sync", headers=headers)
        assert resynced.status_code == 200, resynced.text
        body = resynced.json()

        assert body["title"] == "Edited Title"
        assert body["brand"] == product["brand"]

        row = (
            await db_session.execute(select(Product).where(Product.id == uuid.UUID(product["id"])))
        ).scalar_one()
        assert row.supplier_title != "Edited Title"
        assert row.brand == row.supplier_brand


class TestAuthorizationAndIsolation:
    async def test_requires_authentication(self, client: AsyncClient) -> None:
        response = await client.patch(f"/api/v1/products/{uuid.uuid4()}", json={"title": "x"})
        assert response.status_code == 401

    async def test_non_admin_cannot_update(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, product = await import_a_product(client, monkeypatch)
        token = create_access_token(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), roles=("viewer",))
        viewer_headers = {"Authorization": f"Bearer {token.token}"}

        response = await client.patch(
            f"/api/v1/products/{product['id']}", json={"title": "x"}, headers=viewer_headers
        )
        assert response.status_code == 403, response.text

    async def test_unknown_product_is_404(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers = await connected_tenant(client, monkeypatch)
        response = await client.patch(
            f"/api/v1/products/{uuid.uuid4()}", json={"title": "x"}, headers=headers
        )
        assert response.status_code == 404, response.text

    async def test_cannot_update_another_tenants_product(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, product = await import_a_product(client, monkeypatch)
        other_tenant = await register(client)

        response = await client.patch(
            f"/api/v1/products/{product['id']}",
            json={"title": "Hijacked"},
            headers=auth_header(other_tenant),
        )
        assert response.status_code == 404, response.text
