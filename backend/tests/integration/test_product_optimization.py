"""Integration tests for product optimisation (Phase 9 stages 3 and 4).

Drives the real HTTP pipeline — auth, dependencies, repositories, database.
Reuses `test_products.py`'s AliExpress-mocking apparatus (a captured real
payload behind a mocked transport, not the live gateway) to get a genuine
imported product into the catalogue, rather than depending on live network
access the way the Playwright suite does.

Stage 4 added the `seo_optimizer` generation; its coverage lives in
`TestSeoGeneration` and `TestSeoFailure` below, extending this file rather
than duplicating its fixtures elsewhere.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fakeredis import aioredis as fake_aioredis
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.exceptions import AIError
from app.ai.provider import CompletionRequest, CompletionResult
from app.ai.stub_provider import StubProvider
from app.core.config import AIProviderName, settings
from app.core.tokens import create_access_token
from app.integrations.aliexpress import service as service_module
from app.models.ai_prompt import PromptExecution
from tests.integration.test_products import (
    REAL_PRODUCT_ID,
    auth_header,
    connected_tenant,
    register,
)

pytestmark = pytest.mark.integration

#: Distinctive text from the seeded `seo_optimizer` template (migration 0010),
#: used to recognise its rendered prompt at the provider boundary. Neither
#: title nor description template contains it.
_SEO_PROMPT_MARKER = "SEO-optimised page title"


class _SeoFailsProvider:
    """A provider that answers the title and description prompts exactly as
    `StubProvider` would, and fails only the `seo_optimizer` prompt.

    Installed at `app.services.prompt.get_ai_provider` — the seam a real
    provider occupies — so the test drives the production failure path
    (`PromptExecution` recorded as FAILED, `_first_failure` sees it) rather
    than a shortcut inside the service.
    """

    name = "stub"

    def __init__(self) -> None:
        self._inner = StubProvider()

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        if _SEO_PROMPT_MARKER in request.prompt:
            raise AIError("synthetic SEO generation failure")
        return await self._inner.complete(request)


def _fail_seo_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.prompt.get_ai_provider", lambda _settings: _SeoFailsProvider()
    )


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
        original_description = product["description"]

        response = await client.post(
            f"/api/v1/products/{product['id']}/optimize", json={}, headers=headers
        )
        optimized_product = response.json()["product"]

        assert optimized_product["title"] == original_title
        assert optimized_product["description"] == original_description
        # The always-current supplier copy is equally untouched (Stage 4
        # requirement: generated SEO never rides into supplier fields).
        assert optimized_product["supplierTitle"] == product["supplierTitle"]
        assert optimized_product["supplierDescription"] == product["supplierDescription"]

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


class TestSeoGeneration:
    """Phase 9 stage 4: the third generation call and where its output goes."""

    async def test_optimize_executes_the_seo_optimizer_prompt(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Read the execution log directly — no endpoint exposes it (Stage 2
        limitation 2) — within this test's own transaction, which holds
        only the rows this request wrote."""
        headers, product = await import_a_product(client, monkeypatch)

        response = await client.post(
            f"/api/v1/products/{product['id']}/optimize", json={}, headers=headers
        )
        assert response.status_code == 201, response.text

        names = (await db_session.execute(select(PromptExecution.prompt_name))).scalars().all()
        assert sorted(names) == [
            "product_description_generator",
            "product_title_generator",
            "seo_optimizer",
        ]

    async def test_seo_execution_receives_the_keywords_variable(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The template declares `{{keywords}}`; if the builder ever dropped
        it, rendering would raise `MissingPromptVariablesError` (422) and no
        execution would exist — so the row existing with the variable
        recorded is the proof the variable reached the prompt."""
        headers, product = await import_a_product(client, monkeypatch)

        await client.post(f"/api/v1/products/{product['id']}/optimize", json={}, headers=headers)

        execution = (
            await db_session.execute(
                select(PromptExecution).where(PromptExecution.prompt_name == "seo_optimizer")
            )
        ).scalar_one()
        assert "keywords" in execution.input_variables
        assert isinstance(execution.input_variables["keywords"], str)
        assert execution.is_synthetic is True
        assert execution.provider == "stub"

    async def test_generated_version_content_carries_the_seo_keys(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)

        response = await client.post(
            f"/api/v1/products/{product['id']}/optimize", json={}, headers=headers
        )
        version = response.json()["version"]

        for key in ("title", "description", "seoTitle", "seoDescription", "keywords"):
            assert version[key] is not None, key

    async def test_generated_seo_is_the_stub_providers_synthetic_output_verbatim(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Nothing is parsed, trimmed to a character limit, or scored. The
        three SEO values are the one `seo_optimizer` completion as the
        provider returned it — identical under `StubProvider`, and visibly
        synthetic. See docs/PHASE_9_STAGE_4_PLAN.md §3."""
        headers, product = await import_a_product(client, monkeypatch)

        response = await client.post(
            f"/api/v1/products/{product['id']}/optimize", json={}, headers=headers
        )
        version = response.json()["version"]

        assert version["seoTitle"].startswith("[STUB-AI]")
        assert version["seoTitle"] == version["seoDescription"] == version["keywords"]
        # And it is a *different* completion from the title's — three
        # prompts, three executions, not one response copied everywhere.
        assert version["seoTitle"] != version["title"]

    async def test_versions_endpoint_exposes_the_optional_seo_fields(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)
        await client.post(f"/api/v1/products/{product['id']}/optimize", json={}, headers=headers)

        history = await client.get(f"/api/v1/products/{product['id']}/versions", headers=headers)
        by_source = {v["source"]: v for v in history.json()["items"]}

        original = by_source["original"]
        assert original["seoTitle"] is None
        assert original["seoDescription"] is None
        assert original["keywords"] is None

        generated = by_source["ai_generated"]
        assert generated["seoTitle"] is not None
        assert generated["seoDescription"] is not None
        assert generated["keywords"] is not None

    async def test_merchant_seo_fields_and_tags_are_not_written(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Seed the merchant's own SEO fields through the existing PATCH
        endpoint first, so the assertion is "a known value survived", not
        "null is still null". The generated proposal lives only in the
        version; promoting it is the merchant's decision, not this stage's."""
        headers, product = await import_a_product(client, monkeypatch)
        seeded = await client.patch(
            f"/api/v1/products/{product['id']}",
            json={
                "seoTitle": "Merchant SEO title",
                "seoDescription": "Merchant SEO description",
                "metaKeywords": "merchant, keywords",
                "tags": ["merchant-tag"],
                "searchTopics": ["merchant topic"],
            },
            headers=headers,
        )
        assert seeded.status_code == 200, seeded.text

        response = await client.post(
            f"/api/v1/products/{product['id']}/optimize", json={}, headers=headers
        )
        assert response.status_code == 201, response.text
        body = response.json()

        # The proposal exists...
        assert body["version"]["seoTitle"].startswith("[STUB-AI]")
        # ...and the merchant's fields are exactly as they were seeded.
        after = body["product"]
        assert after["seoTitle"] == "Merchant SEO title"
        assert after["seoDescription"] == "Merchant SEO description"
        assert after["metaKeywords"] == "merchant, keywords"
        assert after["tags"] == ["merchant-tag"]
        assert after["searchTopics"] == ["merchant topic"]

        # Re-read rather than trust the response envelope alone.
        detail = await client.get(f"/api/v1/products/{product['id']}", headers=headers)
        assert detail.json()["seoTitle"] == "Merchant SEO title"
        assert detail.json()["seoDescription"] == "Merchant SEO description"
        assert detail.json()["tags"] == ["merchant-tag"]

    async def test_second_optimize_still_produces_version_three(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Three executions per call must not disturb version numbering."""
        headers, product = await import_a_product(client, monkeypatch)

        await client.post(f"/api/v1/products/{product['id']}/optimize", json={}, headers=headers)
        second = await client.post(
            f"/api/v1/products/{product['id']}/optimize", json={}, headers=headers
        )
        assert second.json()["version"]["versionNumber"] == 3
        assert second.json()["version"]["seoTitle"] is not None
        assert second.json()["product"]["aiVersion"] == 3

    async def test_rollback_does_not_touch_merchant_seo_fields(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Activation syncs only the optimized title/description cache, as it
        did in Stage 3 — never the merchant's SEO fields, in either direction."""
        headers, product = await import_a_product(client, monkeypatch)
        await client.patch(
            f"/api/v1/products/{product['id']}",
            json={"seoTitle": "Merchant SEO title", "tags": ["merchant-tag"]},
            headers=headers,
        )
        first = await client.post(
            f"/api/v1/products/{product['id']}/optimize", json={}, headers=headers
        )
        await client.post(f"/api/v1/products/{product['id']}/optimize", json={}, headers=headers)

        version_2_id = first.json()["version"]["id"]
        rolled_back = await client.post(
            f"/api/v1/products/{product['id']}/versions/{version_2_id}/activate",
            headers=headers,
        )
        assert rolled_back.status_code == 200, rolled_back.text
        body = rolled_back.json()
        assert body["aiVersion"] == 2
        assert body["optimizedTitle"] == first.json()["version"]["title"]
        assert body["seoTitle"] == "Merchant SEO title"
        assert body["tags"] == ["merchant-tag"]


class TestSeoFailure:
    """All-or-nothing: an SEO failure is a whole-optimisation failure."""

    async def test_seo_failure_creates_no_ai_version_and_marks_failed(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)
        _fail_seo_only(monkeypatch)

        response = await client.post(
            f"/api/v1/products/{product['id']}/optimize", json={}, headers=headers
        )
        assert response.status_code == 503, response.text

        # Title and description succeeded, but nothing of theirs was kept:
        # only the lazily-created original snapshot exists.
        history = await client.get(f"/api/v1/products/{product['id']}/versions", headers=headers)
        assert [v["versionNumber"] for v in history.json()["items"]] == [1]

        detail = await client.get(f"/api/v1/products/{product['id']}", headers=headers)
        assert detail.json()["aiStatus"] == "failed"
        assert detail.json()["optimizedTitle"] is None
        assert detail.json()["optimizedDescription"] is None

    async def test_seo_failure_is_recorded_on_the_execution_not_hidden(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)
        _fail_seo_only(monkeypatch)

        await client.post(f"/api/v1/products/{product['id']}/optimize", json={}, headers=headers)

        rows = (await db_session.execute(select(PromptExecution))).scalars().all()
        by_name = {row.prompt_name: row for row in rows}
        assert by_name["product_title_generator"].status.value == "succeeded"
        assert by_name["product_description_generator"].status.value == "succeeded"
        assert by_name["seo_optimizer"].status.value == "failed"
        assert by_name["seo_optimizer"].error_code == "AIError"

    async def test_last_good_optimized_cache_survives_a_failed_regeneration(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)
        first = await client.post(
            f"/api/v1/products/{product['id']}/optimize", json={}, headers=headers
        )
        assert first.status_code == 201, first.text
        good_title = first.json()["product"]["optimizedTitle"]
        good_description = first.json()["product"]["optimizedDescription"]

        _fail_seo_only(monkeypatch)
        retry = await client.post(
            f"/api/v1/products/{product['id']}/optimize", json={}, headers=headers
        )
        assert retry.status_code == 503, retry.text

        detail = await client.get(f"/api/v1/products/{product['id']}", headers=headers)
        body = detail.json()
        assert body["aiStatus"] == "failed"
        assert body["optimizedTitle"] == good_title
        assert body["optimizedDescription"] == good_description
        assert body["aiVersion"] == 2

        history = await client.get(f"/api/v1/products/{product['id']}/versions", headers=headers)
        assert sorted(v["versionNumber"] for v in history.json()["items"]) == [1, 2]
        assert next(v for v in history.json()["items"] if v["versionNumber"] == 2)["active"]

    async def test_seo_failure_does_not_touch_merchant_seo_fields_either(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, product = await import_a_product(client, monkeypatch)
        await client.patch(
            f"/api/v1/products/{product['id']}",
            json={"seoTitle": "Merchant SEO title", "seoDescription": "Merchant SEO description"},
            headers=headers,
        )
        _fail_seo_only(monkeypatch)

        await client.post(f"/api/v1/products/{product['id']}/optimize", json={}, headers=headers)

        detail = await client.get(f"/api/v1/products/{product['id']}", headers=headers)
        assert detail.json()["seoTitle"] == "Merchant SEO title"
        assert detail.json()["seoDescription"] == "Merchant SEO description"
        assert detail.json()["title"] == product["title"]


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
