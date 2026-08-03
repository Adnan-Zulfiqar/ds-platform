"""HTTP coverage for AI prompt mutation lock (audit A-03)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.core.config import settings
from tests.integration.conftest import registration_payload

pytestmark = pytest.mark.integration

BASE_URL = "/api/v1/ai/prompts"


async def _admin_headers(client: AsyncClient) -> dict[str, str]:
    response = await client.post("/api/v1/auth/register", json=registration_payload())
    assert response.status_code == 201, response.text
    token = response.json()["tokens"]["accessToken"]
    return {"Authorization": f"Bearer {token}"}


class TestPromptMutationDisabledOverHttp:
    async def test_create_returns_403_when_mutation_is_disabled(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings.ai, "allow_prompt_mutation", False)
        headers = await _admin_headers(client)

        response = await client.post(
            BASE_URL,
            headers=headers,
            json={
                "name": "should_not_create_a03",
                "template": "Hello {{name}}",
                "description": None,
                "targetModel": None,
            },
        )
        assert response.status_code == 403, response.text
        assert response.json()["code"] == "permission_denied"
