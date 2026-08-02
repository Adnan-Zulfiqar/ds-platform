"""Integration tests for product optimisation (Phase 9 stage 3).

Drives the real HTTP pipeline — auth, dependencies, repositories, database.
Reuses `test_products.py`'s AliExpress-mocking apparatus (a captured real
payload behind a mocked transport, not the live gateway) to get a genuine
imported product into the catalogue, rather than depending on live network
access the way the Playwright suite does.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fakeredis import aioredis as fake_aioredis
from httpx import AsyncClient

from app.core.config import AIProviderName, settings
from app.core.tokens import create_access_token
from app.integrations.aliexpress import service as service_module
from tests.integration.test_products import (
    REAL_PRODUCT_ID,
    auth_header,
    connected_tenant,
    register,
)

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
) -> tuple[dict[str, str], dict[str, Any]]:
    """A connected tenant with one real, mocked-payload product imported."""
    headers = await connected_tenant(client, monkeypatch)
    response = await client.post(
        "/api/v1/products/import", json={"externalId": REAL_PRODUCT_ID}, headers=headers
    )
    assert response.status_code == 201, response.text
    return headers, response.json()


class TestVersionsEndpoint:
    async def test_a_never_optimized_product_has_no_versions(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)

        response = await client.get(f"/api/v1/products/{product['id']}/versions", headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["items"] == []

    async def test_requires_authentication(self, client: AsyncClient) -> None:
        response = await client.get(f"/api/v1/products/{uuid.uuid4()}/versions")
        assert response.status_code == 401

    async def test_cannot_see_another_tenants_product_versions(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, product = await import_a_product(client, monkeypatch)
        other_tenant = await register(client)

        response = await client.get(
            f"/api/v1/products/{product['id']}/versions", headers=auth_header(other_tenant)
        )
        assert response.status_code == 404, response.text


class TestOptimize:
    async def test_creates_an_original_snapshot_and_an_ai_generated_version(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)

        response = await client.post(
            f"/api/v1/products/{product['id']}/optimize", json={}, headers=headers
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["version"]["versionNumber"] == 2
        assert body["version"]["source"] == "ai_generated"
        assert body["version"]["active"] is True

        history = await client.get(f"/api/v1/products/{product['id']}/versions", headers=headers)
        by_number = {v["versionNumber"]: v["source"] for v in history.json()["items"]}
        assert by_number == {1: "original", 2: "ai_generated"}

    async def test_updates_the_products_ai_fields_via_the_stub_provider(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)

        response = await client.post(
            f"/api/v1/products/{product['id']}/optimize", json={}, headers=headers
        )
        optimized_product = response.json()["product"]

        assert optimized_product["aiStatus"] == "optimized"
        assert optimized_product["aiProvider"] == "stub"
        assert optimized_product["aiVersion"] == 2
        assert optimized_product["optimizedTitle"] is not None
        assert "[STUB-AI]" in optimized_product["optimizedTitle"]
        assert optimized_product["optimizedDescription"] is not None

    async def test_never_changes_the_suppliers_title_or_description(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)
        original_title = product["title"]

        response = await client.post(
            f"/api/v1/products/{product['id']}/optimize", json={}, headers=headers
        )
        optimized_product = response.json()["product"]

        assert optimized_product["title"] == original_title

    async def test_a_second_optimize_call_adds_a_third_version(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)

        await client.post(f"/api/v1/products/{product['id']}/optimize", json={}, headers=headers)
        second = await client.post(
            f"/api/v1/products/{product['id']}/optimize", json={}, headers=headers
        )
        assert second.json()["version"]["versionNumber"] == 3

        history = await client.get(f"/api/v1/products/{product['id']}/versions", headers=headers)
        assert len(history.json()["items"]) == 3

    async def test_accepts_a_custom_tone(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)

        response = await client.post(
            f"/api/v1/products/{product['id']}/optimize",
            json={"tone": "playful"},
            headers=headers,
        )
        assert response.status_code == 201, response.text

    async def test_non_admin_cannot_optimize(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A viewer-role token, minted directly rather than through a real
        invite flow — none exists yet, the same gap Stage 2's permission
        tests already note. `require_minimum_role` reads only the token's
        signed claims, so this proves the check runs regardless of whether
        the product or tenant in the token are real."""
        _, product = await import_a_product(client, monkeypatch)
        token = create_access_token(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), roles=("viewer",))
        viewer_headers = {"Authorization": f"Bearer {token.token}"}

        response = await client.post(
            f"/api/v1/products/{product['id']}/optimize", json={}, headers=viewer_headers
        )
        assert response.status_code == 403, response.text

    async def test_unknown_product_is_404(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers = await connected_tenant(client, monkeypatch)
        response = await client.post(
            f"/api/v1/products/{uuid.uuid4()}/optimize", json={}, headers=headers
        )
        assert response.status_code == 404, response.text

    async def test_cannot_optimize_another_tenants_product(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, product = await import_a_product(client, monkeypatch)
        other_tenant = await register(client)

        response = await client.post(
            f"/api/v1/products/{product['id']}/optimize",
            json={},
            headers=auth_header(other_tenant),
        )
        assert response.status_code == 404, response.text

    async def test_provider_failure_marks_the_product_failed_without_a_new_version(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)
        monkeypatch.setattr(settings.ai, "provider", AIProviderName.OPENAI)

        response = await client.post(
            f"/api/v1/products/{product['id']}/optimize", json={}, headers=headers
        )
        assert response.status_code == 503, response.text

        # The original snapshot (version 1) is still created — it happens
        # before any provider call — but no AI-generated version exists.
        history = await client.get(f"/api/v1/products/{product['id']}/versions", headers=headers)
        assert [v["versionNumber"] for v in history.json()["items"]] == [1]

        detail = await client.get(f"/api/v1/products/{product['id']}", headers=headers)
        assert detail.json()["aiStatus"] == "failed"
        assert detail.json()["optimizedTitle"] is None


class TestActivateVersion:
    async def test_activating_an_older_version_rolls_back(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)
        first = await client.post(
            f"/api/v1/products/{product['id']}/optimize", json={}, headers=headers
        )
        first_title = first.json()["version"]["title"]

        await client.post(f"/api/v1/products/{product['id']}/optimize", json={}, headers=headers)

        version_2_id = first.json()["version"]["id"]
        rolled_back = await client.post(
            f"/api/v1/products/{product['id']}/versions/{version_2_id}/activate",
            headers=headers,
        )
        assert rolled_back.status_code == 200, rolled_back.text
        assert rolled_back.json()["aiVersion"] == 2
        assert rolled_back.json()["optimizedTitle"] == first_title

    async def test_activating_the_original_clears_optimized_fields(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)
        await client.post(f"/api/v1/products/{product['id']}/optimize", json={}, headers=headers)

        history = await client.get(f"/api/v1/products/{product['id']}/versions", headers=headers)
        original_id = next(v["id"] for v in history.json()["items"] if v["source"] == "original")

        rolled_back = await client.post(
            f"/api/v1/products/{product['id']}/versions/{original_id}/activate",
            headers=headers,
        )
        assert rolled_back.status_code == 200, rolled_back.text
        body = rolled_back.json()
        assert body["aiStatus"] == "not_optimized"
        assert body["optimizedTitle"] is None
        assert body["optimizedDescription"] is None
        assert body["aiProvider"] is None
        # The supplier's own title is completely unaffected by any of this.
        assert body["title"] == product["title"]

    async def test_requires_authentication(self, client: AsyncClient) -> None:
        response = await client.post(
            f"/api/v1/products/{uuid.uuid4()}/versions/{uuid.uuid4()}/activate"
        )
        assert response.status_code == 401

    async def test_non_admin_cannot_activate(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)
        await client.post(f"/api/v1/products/{product['id']}/optimize", json={}, headers=headers)
        history = await client.get(f"/api/v1/products/{product['id']}/versions", headers=headers)
        version_id = history.json()["items"][0]["id"]

        token = create_access_token(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), roles=("viewer",))
        viewer_headers = {"Authorization": f"Bearer {token.token}"}
        response = await client.post(
            f"/api/v1/products/{product['id']}/versions/{version_id}/activate",
            headers=viewer_headers,
        )
        assert response.status_code == 403, response.text

    async def test_unknown_version_is_404(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)
        response = await client.post(
            f"/api/v1/products/{product['id']}/versions/{uuid.uuid4()}/activate",
            headers=headers,
        )
        assert response.status_code == 404, response.text

    async def test_cannot_activate_a_version_belonging_to_a_different_product(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`activate` looks a version up by `(product_id, version_id)`
        together — a real version id paired with a product id it does not
        belong to must find nothing, the same way a genuinely different
        product would. A random, never-imported product id proves this
        without needing a second real product: if the lookup only checked
        `version_id`, this would succeed and leak the version across the
        product boundary."""
        headers, product_a = await import_a_product(client, monkeypatch)
        await client.post(f"/api/v1/products/{product_a['id']}/optimize", json={}, headers=headers)
        history_a = await client.get(
            f"/api/v1/products/{product_a['id']}/versions", headers=headers
        )
        version_a_id = history_a.json()["items"][0]["id"]

        response = await client.post(
            f"/api/v1/products/{uuid.uuid4()}/versions/{version_a_id}/activate",
            headers=headers,
        )
        assert response.status_code == 404, response.text
