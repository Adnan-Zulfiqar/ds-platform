"""AI prompt mutation lock (audit A-03)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.core.config import settings
from app.core.exceptions import PermissionDeniedError
from app.services.prompt import PromptService

pytestmark = pytest.mark.unit


class TestPromptMutationGate:
    @pytest.mark.asyncio
    async def test_create_is_refused_when_mutation_is_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings.ai, "allow_prompt_mutation", False)
        service = PromptService(MagicMock())

        with pytest.raises(PermissionDeniedError) as exc:
            await service.create_prompt(
                name="x",
                description=None,
                template="hi",
                target_model=None,
                created_by_user_id=None,
            )
        assert "AI_ALLOW_PROMPT_MUTATION" in str(exc.value.message)

    @pytest.mark.asyncio
    async def test_activate_is_refused_when_mutation_is_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings.ai, "allow_prompt_mutation", False)
        service = PromptService(MagicMock())

        with pytest.raises(PermissionDeniedError):
            await service.activate_version(name="product_title_generator", version=1)
