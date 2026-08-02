"""Tests for `get_ai_provider`.

The property that matters: selecting an unimplemented provider is a loud
configuration error, not a silent fallback to the stub. A deployment that set
`AI_PROVIDER=openai` believing it works must find out immediately.
"""

from __future__ import annotations

import pytest

from app.ai.exceptions import AIProviderNotConfiguredError
from app.ai.factory import get_ai_provider
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


class TestUnimplementedProviders:
    @pytest.mark.parametrize(
        "name",
        [
            AIProviderName.OPENAI,
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
