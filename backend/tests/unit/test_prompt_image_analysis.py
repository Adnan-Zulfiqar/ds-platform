"""PromptService.execute_image_analysis uses analyse_image, not complete."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ai.exceptions import AIProviderNotConfiguredError
from app.ai.provider import CompletionResult, ImageAnalysisResult
from app.models.ai_prompt import PromptExecutionStatus
from app.services.prompt import PromptService

pytestmark = pytest.mark.unit

IMAGE_URL = "https://cdn.example/a.jpg"
VARIABLES = {"image_url": IMAGE_URL, "product_title": "Mug"}


def _prompt() -> MagicMock:
    prompt = MagicMock()
    prompt.id = uuid.uuid4()
    prompt.name = "image_analyzer"
    prompt.version = 1
    prompt.template = "Describe {{image_url}} titled {{product_title}}"
    return prompt


def _service(prompt: MagicMock) -> tuple[PromptService, MagicMock]:
    service = PromptService(MagicMock())
    service.get_active = AsyncMock(return_value=prompt)  # type: ignore[method-assign]
    service.executions.create = AsyncMock(side_effect=lambda **kwargs: MagicMock(**kwargs))
    return service, service.executions.create


class TestExecuteImageAnalysis:
    async def test_calls_analyse_image_not_complete(self, monkeypatch: pytest.MonkeyPatch) -> None:
        prompt = _prompt()
        service, create = _service(prompt)
        provider = MagicMock()
        provider.complete = AsyncMock(
            return_value=CompletionResult(text="nope", provider="stub", model="stub-1")
        )
        provider.analyse_image = AsyncMock(
            return_value=ImageAnalysisResult(
                caption="[STUB-AI] synthetic caption (image deadbeef).",
                alt_text="[STUB-AI] synthetic alt text (image deadbeef).",
                provider="stub",
                model="stub-1",
                is_synthetic=True,
            )
        )
        monkeypatch.setattr("app.services.prompt.get_ai_provider", lambda _settings: provider)

        rendered, result, execution = await service.execute_image_analysis(
            name="image_analyzer",
            variables=VARIABLES,
            executed_by_user_id=None,
        )

        provider.analyse_image.assert_awaited_once()
        provider.complete.assert_not_called()
        assert result is not None
        assert result.is_synthetic is True
        assert execution.prompt_name == "image_analyzer"
        assert execution.prompt_version == 1
        assert execution.response_text == json.dumps(
            {
                "altText": "[STUB-AI] synthetic alt text (image deadbeef).",
                "caption": "[STUB-AI] synthetic caption (image deadbeef).",
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        assert execution.response_text.startswith('{"altText":')
        assert execution.input_tokens is None
        assert execution.output_tokens is None
        assert "Describe https://cdn.example/a.jpg titled Mug" == rendered
        create.assert_awaited_once()

    async def test_ai_error_records_failed_execution(self, monkeypatch: pytest.MonkeyPatch) -> None:
        prompt = _prompt()
        service, create = _service(prompt)
        provider = MagicMock()
        provider.analyse_image = AsyncMock(side_effect=AIProviderNotConfiguredError())
        provider.complete = AsyncMock()
        monkeypatch.setattr("app.services.prompt.get_ai_provider", lambda _settings: provider)

        rendered, result, execution = await service.execute_image_analysis(
            name="image_analyzer",
            variables=VARIABLES,
            executed_by_user_id=None,
        )

        assert result is None
        assert rendered
        assert execution.status == PromptExecutionStatus.FAILED
        assert execution.response_text is None
        assert execution.error_code == "AIProviderNotConfiguredError"
        provider.complete.assert_not_called()
        create.assert_awaited_once()

    async def test_unexpected_error_does_not_write_execution(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prompt = _prompt()
        service, create = _service(prompt)
        provider = MagicMock()
        provider.analyse_image = AsyncMock(side_effect=TypeError("bug"))
        monkeypatch.setattr("app.services.prompt.get_ai_provider", lambda _settings: provider)

        with pytest.raises(TypeError):
            await service.execute_image_analysis(
                name="image_analyzer",
                variables=VARIABLES,
                executed_by_user_id=None,
            )
        create.assert_not_called()

    async def test_test_render_still_calls_complete(self, monkeypatch: pytest.MonkeyPatch) -> None:
        prompt = _prompt()
        service, _create = _service(prompt)
        provider = MagicMock()
        provider.complete = AsyncMock(
            return_value=CompletionResult(
                text="[STUB-AI] synthetic completion (prompt abcdef12).",
                provider="stub",
                model="stub-1",
                is_synthetic=True,
            )
        )
        provider.analyse_image = AsyncMock()
        monkeypatch.setattr("app.services.prompt.get_ai_provider", lambda _settings: provider)

        await service.test_render(
            name="image_analyzer",
            variables=VARIABLES,
            execute=True,
            executed_by_user_id=None,
        )

        provider.complete.assert_awaited_once()
        provider.analyse_image.assert_not_called()
