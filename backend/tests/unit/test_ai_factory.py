"""Tests for `get_ai_provider`.

The property that matters: selecting an unimplemented or half-configured
provider is a loud configuration error, not a silent fallback to the stub. A
deployment that set `AI_PROVIDER=anthropic`, or `openai` without a key, must
find out immediately.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from app.ai.exceptions import AIProviderNotConfiguredError
from app.ai.factory import get_ai_provider
from app.ai.openai_provider import OpenAIProvider
from app.ai.stub_provider import StubProvider
from app.core.config import AIProviderName, AISettings, Settings

pytestmark = pytest.mark.unit


def _settings_with_provider(provider: AIProviderName) -> Settings:
    settings = Settings()
    settings.ai = AISettings(provider=provider)
    return settings


class TestStubSelection:
    def test_stub_provider_is_configured(self) -> None:
        provider = get_ai_provider(_settings_with_provider(AIProviderName.STUB))
        assert isinstance(provider, StubProvider)
        assert provider.name == "stub"


class TestOpenAISelection:
    def test_openai_with_key_and_model_is_configured(self) -> None:
        settings = Settings()
        settings.ai = AISettings(
            provider=AIProviderName.OPENAI,
            openai_api_key=SecretStr("sk-test-not-real"),
            openai_model="test-model",
        )
        provider = get_ai_provider(settings)
        assert isinstance(provider, OpenAIProvider)
        assert provider.name == "openai"

    @pytest.mark.parametrize(
        ("key", "model", "missing"),
        [
            (None, "test-model", "AI_OPENAI_API_KEY"),
            ("sk-test-not-real", "", "AI_OPENAI_MODEL"),
            ("  ", "test-model", "AI_OPENAI_API_KEY"),
        ],
    )
    def test_a_missing_setting_is_named_not_silently_stubbed(
        self, key: str | None, model: str, missing: str
    ) -> None:
        settings = Settings()
        settings.ai = AISettings(
            provider=AIProviderName.OPENAI,
            openai_api_key=SecretStr(key) if key is not None else None,
            openai_model=model,
        )
        with pytest.raises(AIProviderNotConfiguredError, match=missing) as raised:
            get_ai_provider(settings)
        assert "sk-test-not-real" not in str(raised.value)


class TestUnimplementedProviders:
    @pytest.mark.parametrize(
        "name",
        [
            AIProviderName.ANTHROPIC,
            AIProviderName.GEMINI,
            AIProviderName.LOCAL,
        ],
    )
    def test_raises_a_configuration_error_rather_than_falling_back(
        self, name: AIProviderName
    ) -> None:
        with pytest.raises(AIProviderNotConfiguredError, match=name.value):
            get_ai_provider(_settings_with_provider(name))
