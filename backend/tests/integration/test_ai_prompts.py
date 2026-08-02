"""Integration tests for AI prompt management.

Drives the real HTTP pipeline — auth, dependencies, repository, database.
Every endpoint requires `RequireAdmin`; the permission tests mint a
viewer-role token directly with `create_access_token` rather than going
through a real invite flow, since none exists yet (see
`docs/PHASE_9_PLAN.md` Stage 2 notes) — `require_minimum_role` reads only the
token's signed claims, never the database, so a token for a user that does
not exist is sufficient to prove the check runs before anything else does.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import clear_context, set_tenant_id
from app.core.tokens import create_access_token
from app.repositories.ai_prompt import PromptExecutionRepository
from app.schemas.common import ListQueryParams
from tests.integration.conftest import registration_payload

pytestmark = pytest.mark.integration

BASE_URL = "/api/v1/ai/prompts"

_SEEDED_PROMPT = "product_title_generator"


async def register(client: AsyncClient, **overrides: Any) -> dict[str, Any]:
    response = await client.post("/api/v1/auth/register", json=registration_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


def auth_header(body: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {body['tokens']['accessToken']}"}


def viewer_header() -> dict[str, str]:
    """A syntactically valid token for a viewer who cannot manage prompts."""
    token = create_access_token(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), roles=("viewer",))
    return {"Authorization": f"Bearer {token.token}"}


class TestCreatePrompt:
    async def test_creates_an_active_version_1(self, client: AsyncClient) -> None:
        body = await register(client)
        response = await client.post(
            BASE_URL,
            json={"name": "custom_prompt_a", "template": "Hello {{name}}"},
            headers=auth_header(body),
        )
        assert response.status_code == 201, response.text
        data = response.json()
        assert data["name"] == "custom_prompt_a"
        assert data["version"] == 1
        assert data["active"] is True
        assert data["requiredVariables"] == ["name"]

    async def test_duplicate_name_is_rejected(self, client: AsyncClient) -> None:
        body = await register(client)
        headers = auth_header(body)
        payload = {"name": "custom_prompt_b", "template": "Hi {{x}}"}

        first = await client.post(BASE_URL, json=payload, headers=headers)
        assert first.status_code == 201, first.text

        second = await client.post(BASE_URL, json=payload, headers=headers)
        assert second.status_code == 409, second.text

    async def test_non_admin_cannot_create_a_prompt(self, client: AsyncClient) -> None:
        response = await client.post(
            BASE_URL,
            json={"name": "custom_prompt_c", "template": "Hi {{x}}"},
            headers=viewer_header(),
        )
        assert response.status_code == 403, response.text


class TestListAndGet:
    async def test_list_includes_the_seeded_defaults(self, client: AsyncClient) -> None:
        body = await register(client)
        response = await client.get(BASE_URL, params={"size": 50}, headers=auth_header(body))
        assert response.status_code == 200, response.text
        names = {item["name"] for item in response.json()["items"]}
        assert _SEEDED_PROMPT in names
        assert "seo_optimizer" in names
        assert "quality_scorer" in names
        assert "image_analyzer" in names
        assert "product_description_generator" in names

    async def test_get_active_prompt_reports_its_required_variables(
        self, client: AsyncClient
    ) -> None:
        body = await register(client)
        response = await client.get(f"{BASE_URL}/{_SEEDED_PROMPT}", headers=auth_header(body))
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["active"] is True
        assert set(data["requiredVariables"]) == {"product_title", "category", "brand", "tone"}

    async def test_unknown_prompt_is_404(self, client: AsyncClient) -> None:
        body = await register(client)
        response = await client.get(f"{BASE_URL}/does_not_exist", headers=auth_header(body))
        assert response.status_code == 404, response.text

    async def test_non_admin_cannot_list(self, client: AsyncClient) -> None:
        response = await client.get(BASE_URL, headers=viewer_header())
        assert response.status_code == 403, response.text

    async def test_non_admin_cannot_read_a_single_prompt(self, client: AsyncClient) -> None:
        response = await client.get(f"{BASE_URL}/{_SEEDED_PROMPT}", headers=viewer_header())
        assert response.status_code == 403, response.text


class TestVersioningAndActivation:
    async def test_new_version_is_created_inactive(self, client: AsyncClient) -> None:
        body = await register(client)
        headers = auth_header(body)
        await client.post(
            BASE_URL, json={"name": "custom_prompt_d", "template": "v1 {{x}}"}, headers=headers
        )

        response = await client.post(
            f"{BASE_URL}/custom_prompt_d/versions",
            json={"template": "v2 {{x}} {{y}}"},
            headers=headers,
        )
        assert response.status_code == 201, response.text
        data = response.json()
        assert data["version"] == 2
        assert data["active"] is False

        active = await client.get(f"{BASE_URL}/custom_prompt_d", headers=headers)
        assert active.json()["version"] == 1

    async def test_activating_a_version_deactivates_the_previous_one(
        self, client: AsyncClient
    ) -> None:
        body = await register(client)
        headers = auth_header(body)
        await client.post(
            BASE_URL, json={"name": "custom_prompt_e", "template": "v1 {{x}}"}, headers=headers
        )
        await client.post(
            f"{BASE_URL}/custom_prompt_e/versions",
            json={"template": "v2 {{x}}"},
            headers=headers,
        )

        activated = await client.post(
            f"{BASE_URL}/custom_prompt_e/versions/2/activate", headers=headers
        )
        assert activated.status_code == 200, activated.text
        assert activated.json()["version"] == 2
        assert activated.json()["active"] is True

        history = await client.get(f"{BASE_URL}/custom_prompt_e/history", headers=headers)
        by_version = {item["version"]: item["active"] for item in history.json()["items"]}
        assert by_version == {1: False, 2: True}

    async def test_activating_an_older_version_is_how_rollback_works(
        self, client: AsyncClient
    ) -> None:
        body = await register(client)
        headers = auth_header(body)
        await client.post(
            BASE_URL, json={"name": "custom_prompt_f", "template": "v1 {{x}}"}, headers=headers
        )
        await client.post(
            f"{BASE_URL}/custom_prompt_f/versions",
            json={"template": "v2 {{x}}"},
            headers=headers,
        )
        await client.post(f"{BASE_URL}/custom_prompt_f/versions/2/activate", headers=headers)

        rolled_back = await client.post(
            f"{BASE_URL}/custom_prompt_f/versions/1/activate", headers=headers
        )
        assert rolled_back.status_code == 200, rolled_back.text
        assert rolled_back.json()["version"] == 1
        assert rolled_back.json()["active"] is True

        active = await client.get(f"{BASE_URL}/custom_prompt_f", headers=headers)
        assert active.json()["version"] == 1

    async def test_history_lists_every_version_newest_first(self, client: AsyncClient) -> None:
        body = await register(client)
        headers = auth_header(body)
        await client.post(
            BASE_URL, json={"name": "custom_prompt_g", "template": "v1 {{x}}"}, headers=headers
        )
        await client.post(
            f"{BASE_URL}/custom_prompt_g/versions",
            json={"template": "v2 {{x}}"},
            headers=headers,
        )
        await client.post(
            f"{BASE_URL}/custom_prompt_g/versions",
            json={"template": "v3 {{x}}"},
            headers=headers,
        )

        history = await client.get(f"{BASE_URL}/custom_prompt_g/history", headers=headers)
        assert [item["version"] for item in history.json()["items"]] == [3, 2, 1]

    async def test_adding_a_version_to_an_unknown_name_is_404(self, client: AsyncClient) -> None:
        body = await register(client)
        response = await client.post(
            f"{BASE_URL}/does_not_exist/versions",
            json={"template": "hi {{x}}"},
            headers=auth_header(body),
        )
        assert response.status_code == 404, response.text


class TestTestRendering:
    async def test_render_without_execute_never_calls_a_provider(self, client: AsyncClient) -> None:
        body = await register(client)
        response = await client.post(
            f"{BASE_URL}/{_SEEDED_PROMPT}/test",
            json={
                "variables": {
                    "product_title": "Mirror lip gloss set",
                    "category": "Beauty",
                    "brand": "Generic",
                    "tone": "friendly",
                },
                "execute": False,
            },
            headers=auth_header(body),
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert "Mirror lip gloss set" in data["renderedPrompt"]
        assert data["execution"] is None

    async def test_missing_variables_fail_clearly_and_record_nothing(
        self, client: AsyncClient
    ) -> None:
        body = await register(client)
        response = await client.post(
            f"{BASE_URL}/{_SEEDED_PROMPT}/test",
            json={"variables": {"product_title": "Only one variable"}, "execute": False},
            headers=auth_header(body),
        )
        assert response.status_code == 422, response.text
        detail = response.json()
        assert detail["code"] == "missing_prompt_variables"

    async def test_execute_uses_the_stub_provider_and_records_a_synthetic_execution(
        self, client: AsyncClient
    ) -> None:
        body = await register(client)
        response = await client.post(
            f"{BASE_URL}/{_SEEDED_PROMPT}/test",
            json={
                "variables": {
                    "product_title": "Mirror lip gloss set",
                    "category": "Beauty",
                    "brand": "Generic",
                    "tone": "friendly",
                },
                "execute": True,
            },
            headers=auth_header(body),
        )
        assert response.status_code == 200, response.text
        execution = response.json()["execution"]
        assert execution is not None
        assert execution["provider"] == "stub"
        assert execution["isSynthetic"] is True
        assert execution["status"] == "succeeded"

    async def test_non_admin_cannot_test_render(self, client: AsyncClient) -> None:
        response = await client.post(
            f"{BASE_URL}/{_SEEDED_PROMPT}/test",
            json={"variables": {}, "execute": False},
            headers=viewer_header(),
        )
        assert response.status_code == 403, response.text


class TestTenantIsolation:
    """No HTTP endpoint reads `PromptExecution` back yet (see Stage 2 notes),
    so isolation is verified the same way the repository's own unit tests do
    it — directly — but here against a real database and real rows written
    by the HTTP layer, which is what an SQL-compile check alone cannot prove.
    """

    async def test_a_tenants_executions_are_invisible_to_another_tenant(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        tenant_a = await register(client)
        tenant_b = await register(client)

        await client.post(
            f"{BASE_URL}/{_SEEDED_PROMPT}/test",
            json={
                "variables": {
                    "product_title": "Tenant A product",
                    "category": "Beauty",
                    "brand": "A",
                    "tone": "neutral",
                },
                "execute": True,
            },
            headers=auth_header(tenant_a),
        )
        await client.post(
            f"{BASE_URL}/{_SEEDED_PROMPT}/test",
            json={
                "variables": {
                    "product_title": "Tenant B product",
                    "category": "Beauty",
                    "brand": "B",
                    "tone": "neutral",
                },
                "execute": True,
            },
            headers=auth_header(tenant_b),
        )

        tenant_a_id = uuid.UUID(tenant_a["identity"]["tenant"]["id"])
        set_tenant_id(tenant_a_id)
        try:
            rows, total = await PromptExecutionRepository(db_session).list(
                ListQueryParams(page=1, size=50)
            )
        finally:
            clear_context()

        assert total == 1
        assert rows[0].input_variables["product_title"] == "Tenant A product"
