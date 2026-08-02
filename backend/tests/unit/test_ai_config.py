"""Tests for `AISettings`.

Mirrors `test_aliexpress_config.py`: credentials must come from configuration
and default to unset rather than a placeholder, and the Google/Gemini alias
must accept whichever environment-variable name an operator already used.
"""

from __future__ import annotations

import pytest

from app.core.config import AIProviderName, AISettings, Settings

pytestmark = pytest.mark.unit

OPENAI_KEY = "sk-test-openai-key"
ANTHROPIC_KEY = "sk-test-anthropic-key"
GOOGLE_KEY = "test-google-key"


class TestProviderSelection:
    def test_defaults_to_stub(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AI_PROVIDER", raising=False)
        assert AISettings().provider is AIProviderName.STUB

    def test_reads_the_configured_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_PROVIDER", "openai")
        assert AISettings().provider is AIProviderName.OPENAI

    def test_rejects_an_unknown_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_PROVIDER", "not-a-real-provider")
        with pytest.raises(Exception, match="provider"):
            AISettings()


class TestCredentials:
    def test_keys_default_to_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AI_OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("AI_ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("AI_GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("AI_GEMINI_API_KEY", raising=False)

        config = AISettings()

        assert config.openai_api_key is None
        assert config.anthropic_api_key is None
        assert config.google_api_key is None

    def test_keys_load_from_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_OPENAI_API_KEY", OPENAI_KEY)
        monkeypatch.setenv("AI_ANTHROPIC_API_KEY", ANTHROPIC_KEY)

        config = AISettings()

        assert config.openai_api_key is not None
        assert config.openai_api_key.get_secret_value() == OPENAI_KEY
        assert config.anthropic_api_key is not None
        assert config.anthropic_api_key.get_secret_value() == ANTHROPIC_KEY

    def test_a_key_is_masked_in_a_repr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_OPENAI_API_KEY", OPENAI_KEY)
        assert OPENAI_KEY not in repr(AISettings())


class TestGoogleKeyAlias:
    def test_accepts_the_google_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_GOOGLE_API_KEY", GOOGLE_KEY)
        monkeypatch.delenv("AI_GEMINI_API_KEY", raising=False)

        config = AISettings()

        assert config.google_api_key is not None
        assert config.google_api_key.get_secret_value() == GOOGLE_KEY

    def test_accepts_the_gemini_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AI_GOOGLE_API_KEY", raising=False)
        monkeypatch.setenv("AI_GEMINI_API_KEY", GOOGLE_KEY)

        config = AISettings()

        assert config.google_api_key is not None
        assert config.google_api_key.get_secret_value() == GOOGLE_KEY


class TestSettingsIntegration:
    def test_settings_expose_ai_configuration(self) -> None:
        assert isinstance(Settings().ai, AISettings)
