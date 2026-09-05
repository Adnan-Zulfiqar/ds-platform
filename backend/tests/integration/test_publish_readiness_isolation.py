"""UX-L2B-R1 — HTTP cross-tenant isolation for publish readiness and publish.

Foreign draft/store identifiers must be indistinguishable from missing ones:
exact 404, ``not_found`` code, no foreign metadata, and zero Shopify client
calls. Provider wire is mocked; no live Shopify traffic.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import set_tenant_id
from app.core.encryption import encrypt
from app.integrations.shopify import sync as shopify_sync_module
from app.models.integration import IntegrationStatus
from app.models.product import Product, ProductSource, ProductStatus
from app.models.shopify import ShopifyConnection
from app.models.store import Store, StorePlatform, StoreStatus
from tests.integration.test_products import auth_header, register

pytestmark = pytest.mark.integration

READINESS_URL = "/api/v1/integrations/shopify/publish-readiness"
PUBLISH_URL = "/api/v1/integrations/shopify/publish"

SECRET_MARKERS = (
    "shpat_",
    "access_token",
    "refresh_token",
    "encrypted_access_token",
    "Authorization",
    "Bearer ",
)


def _assert_non_disclosing_404(response: Any, *, foreign_hints: list[str]) -> dict[str, Any]:
    assert response.status_code == 404, response.text
    body = response.json()
    assert body["code"] == "not_found"
    assert set(body) >= {"code", "message", "details", "requestId"}
    assert body["requestId"]
    assert isinstance(body["message"], str) and body["message"]
    # Never confirm existence via 403.
    assert response.status_code != 403
    text = response.text.lower()
    for hint in foreign_hints:
        assert hint.lower() not in text
    for marker in SECRET_MARKERS:
        assert marker.lower() not in text
    return body


async def _seed_tenant_with_draft_and_store(
    client: AsyncClient,
    db_session: AsyncSession,
    *,
    company: str,
    email: str,
) -> tuple[dict[str, str], Product, Store]:
    body = await register(client, companyName=company, email=email)
    headers = auth_header(body)
    tenant_id = uuid.UUID(str(body["identity"]["tenant"]["id"]))
    set_tenant_id(tenant_id)

    product = Product(
        tenant_id=tenant_id,
        source=ProductSource.MANUAL,
        external_id=f"manual-{uuid.uuid4().hex[:12]}",
        title="Tenant draft for publish isolation",
        status=ProductStatus.DRAFT,
        description="<p>Body</p>",
        supplier_description="<p>Body</p>",
        supplier_title="Tenant draft for publish isolation",
    )
    store = Store(
        tenant_id=tenant_id,
        name=f"{company} Shopify",
        slug=f"store-{uuid.uuid4().hex[:8]}",
        platform=StorePlatform.SHOPIFY,
        status=StoreStatus.CONNECTED,
        currency="GBP",
        settings={"countryCode": "GB"},
    )
    db_session.add(product)
    db_session.add(store)
    await db_session.flush()

    connection = ShopifyConnection(
        tenant_id=tenant_id,
        store_id=store.id,
        shop_domain=f"{uuid.uuid4().hex[:10]}.myshopify.com",
        encrypted_access_token=encrypt("shpat_test_token_not_real"),
        status=IntegrationStatus.CONNECTED,
        scopes="write_products",
    )
    db_session.add(connection)
    await db_session.flush()
    return headers, product, store


@pytest.fixture
def counting_shopify_client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace Shopify REST client construction; count provider contact attempts."""
    calls = MagicMock()
    calls.count = 0

    async def _boom(*_args: Any, **_kwargs: Any) -> Any:
        calls.count += 1
        raise AssertionError("Shopify provider must not be contacted for isolation cases")

    fake_service = MagicMock()
    fake_service.client_for_store = AsyncMock(side_effect=_boom)

    original_init = shopify_sync_module.ShopifySyncService.__init__

    def _init(self: Any, session: AsyncSession) -> None:
        original_init(self, session)
        self.shopify = fake_service

    monkeypatch.setattr(shopify_sync_module.ShopifySyncService, "__init__", _init)
    return calls


class TestPublishReadinessAuthAndIsolation:
    async def test_readiness_requires_authentication(self, client: AsyncClient) -> None:
        response = await client.post(
            READINESS_URL,
            json={"productId": str(uuid.uuid4()), "storeId": str(uuid.uuid4())},
        )
        assert response.status_code == 401
        assert response.json()["code"] in {
            "authentication_required",
            "invalid_credentials",
            "token_expired",
        }

    async def test_tenant_a_cannot_read_tenant_b_draft(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        counting_shopify_client: MagicMock,
    ) -> None:
        headers_a, _, store_a = await _seed_tenant_with_draft_and_store(
            client, db_session, company="AcmeA", email="uxl2b-a1@example.com"
        )
        _, product_b, _ = await _seed_tenant_with_draft_and_store(
            client, db_session, company="GlobexB", email="uxl2b-b1@example.com"
        )
        foreign_hints = [product_b.title, "GlobexB Shopify"]

        response = await client.post(
            READINESS_URL,
            headers=headers_a,
            json={"productId": str(product_b.id), "storeId": str(store_a.id)},
        )
        _assert_non_disclosing_404(response, foreign_hints=foreign_hints)
        assert counting_shopify_client.count == 0

    async def test_tenant_a_cannot_use_tenant_b_store(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        counting_shopify_client: MagicMock,
    ) -> None:
        headers_a, product_a, _ = await _seed_tenant_with_draft_and_store(
            client, db_session, company="AcmeA2", email="uxl2b-a2@example.com"
        )
        _, _, store_b = await _seed_tenant_with_draft_and_store(
            client, db_session, company="GlobexB2", email="uxl2b-b2@example.com"
        )

        response = await client.post(
            READINESS_URL,
            headers=headers_a,
            json={"productId": str(product_a.id), "storeId": str(store_b.id)},
        )
        _assert_non_disclosing_404(response, foreign_hints=["GlobexB2 Shopify"])
        assert counting_shopify_client.count == 0

    async def test_random_and_foreign_draft_ids_are_indistinguishable(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        counting_shopify_client: MagicMock,
    ) -> None:
        headers_a, _, store_a = await _seed_tenant_with_draft_and_store(
            client, db_session, company="AcmeA3", email="uxl2b-a3@example.com"
        )
        _, product_b, _ = await _seed_tenant_with_draft_and_store(
            client, db_session, company="GlobexB3", email="uxl2b-b3@example.com"
        )
        random_id = uuid.uuid4()

        foreign = await client.post(
            READINESS_URL,
            headers=headers_a,
            json={"productId": str(product_b.id), "storeId": str(store_a.id)},
        )
        missing = await client.post(
            READINESS_URL,
            headers=headers_a,
            json={"productId": str(random_id), "storeId": str(store_a.id)},
        )
        foreign_body = _assert_non_disclosing_404(foreign, foreign_hints=[product_b.title])
        missing_body = _assert_non_disclosing_404(missing, foreign_hints=[])
        assert foreign.status_code == missing.status_code == 404
        assert foreign_body["code"] == missing_body["code"] == "not_found"
        assert set(foreign_body.keys()) == set(missing_body.keys())
        assert counting_shopify_client.count == 0


class TestReadinessOpenApiAndEnvelope:
    async def test_readiness_is_documented_in_openapi(self, client: AsyncClient) -> None:
        spec = (await client.get("/openapi.json")).json()
        readiness = spec["paths"].get("/api/v1/integrations/shopify/publish-readiness")
        assert readiness is not None
        post = readiness["post"]
        assert "requestBody" in post
        assert "ShopifyPublishReadinessResponse" in str(post.get("responses", {})) or any(
            "PublishReadiness" in str(v) for v in post.get("responses", {}).values()
        )

    async def test_error_envelope_includes_request_id_header_and_body(
        self, client: AsyncClient
    ) -> None:
        response = await client.post(
            READINESS_URL,
            json={"productId": str(uuid.uuid4()), "storeId": str(uuid.uuid4())},
        )
        assert response.status_code == 401
        assert response.headers.get("x-request-id") or response.json().get("requestId")
        body = response.json()
        assert "requestId" in body
        assert "code" in body
        assert "message" in body


class TestPublishAuthAndIsolation:
    async def test_publish_requires_authentication(self, client: AsyncClient) -> None:
        response = await client.post(
            PUBLISH_URL,
            json={"productId": str(uuid.uuid4()), "storeId": str(uuid.uuid4())},
        )
        assert response.status_code == 401

    async def test_tenant_a_cannot_publish_tenant_b_draft(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        counting_shopify_client: MagicMock,
    ) -> None:
        headers_a, _, store_a = await _seed_tenant_with_draft_and_store(
            client, db_session, company="AcmeA4", email="uxl2b-a4@example.com"
        )
        _, product_b, _ = await _seed_tenant_with_draft_and_store(
            client, db_session, company="GlobexB4", email="uxl2b-b4@example.com"
        )

        response = await client.post(
            PUBLISH_URL,
            headers=headers_a,
            json={"productId": str(product_b.id), "storeId": str(store_a.id)},
        )
        _assert_non_disclosing_404(response, foreign_hints=[product_b.title])
        assert counting_shopify_client.count == 0

    async def test_tenant_a_cannot_publish_through_tenant_b_store(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        counting_shopify_client: MagicMock,
    ) -> None:
        headers_a, product_a, _ = await _seed_tenant_with_draft_and_store(
            client, db_session, company="AcmeA5", email="uxl2b-a5@example.com"
        )
        _, _, store_b = await _seed_tenant_with_draft_and_store(
            client, db_session, company="GlobexB5", email="uxl2b-b5@example.com"
        )

        response = await client.post(
            PUBLISH_URL,
            headers=headers_a,
            json={"productId": str(product_a.id), "storeId": str(store_b.id)},
        )
        _assert_non_disclosing_404(response, foreign_hints=["GlobexB5 Shopify"])
        assert counting_shopify_client.count == 0

    async def test_random_and_foreign_publish_targets_are_indistinguishable(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        counting_shopify_client: MagicMock,
    ) -> None:
        headers_a, product_a, store_a = await _seed_tenant_with_draft_and_store(
            client, db_session, company="AcmeA6", email="uxl2b-a6@example.com"
        )
        _, product_b, store_b = await _seed_tenant_with_draft_and_store(
            client, db_session, company="GlobexB6", email="uxl2b-b6@example.com"
        )

        foreign_draft = await client.post(
            PUBLISH_URL,
            headers=headers_a,
            json={"productId": str(product_b.id), "storeId": str(store_a.id)},
        )
        missing_draft = await client.post(
            PUBLISH_URL,
            headers=headers_a,
            json={"productId": str(uuid.uuid4()), "storeId": str(store_a.id)},
        )
        foreign_store = await client.post(
            PUBLISH_URL,
            headers=headers_a,
            json={"productId": str(product_a.id), "storeId": str(store_b.id)},
        )
        missing_store = await client.post(
            PUBLISH_URL,
            headers=headers_a,
            json={"productId": str(product_a.id), "storeId": str(uuid.uuid4())},
        )

        for response in (foreign_draft, missing_draft, foreign_store, missing_store):
            body = _assert_non_disclosing_404(response, foreign_hints=[product_b.title, "GlobexB6"])
            assert body["code"] == "not_found"
        assert counting_shopify_client.count == 0
