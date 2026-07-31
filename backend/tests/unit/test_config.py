"""Tests for configuration parsing.

These exist because of a real startup failure. `CORS_ORIGINS` was documented in
`.env.example` as a comma-separated string, but pydantic-settings runs
`json.loads` on list-typed fields before field validators execute, so the
documented format raised `JSONDecodeError` during boot. The application could
not start with its own example configuration, and nothing caught it because no
test ever parsed a list field from an environment variable.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings

pytestmark = pytest.mark.unit


class TestListParsing:
    def test_comma_separated_origins_are_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The format documented in `.env.example`. This is the regression."""
        monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000,https://app.droppilot.ai")

        settings = Settings()

        assert settings.cors_origins == [
            "http://localhost:3000",
            "https://app.droppilot.ai",
        ]

    def test_a_single_origin_is_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")
        assert Settings().cors_origins == ["http://localhost:3000"]

    def test_surrounding_whitespace_is_stripped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Copied-and-pasted values routinely carry stray spaces."""
        monkeypatch.setenv("CORS_ORIGINS", " http://a.example , http://b.example ")
        assert Settings().cors_origins == ["http://a.example", "http://b.example"]

    def test_a_json_array_is_still_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The JSON form must keep working — some secret managers emit it."""
        monkeypatch.setenv("CORS_ORIGINS", '["http://a.example","http://b.example"]')
        assert Settings().cors_origins == ["http://a.example", "http://b.example"]

    def test_allowed_hosts_parses_the_same_way(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALLOWED_HOSTS", "app.droppilot.ai,api.droppilot.ai")
        assert Settings().allowed_hosts == ["app.droppilot.ai", "api.droppilot.ai"]

    def test_defaults_apply_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CORS_ORIGINS", raising=False)
        monkeypatch.delenv("ALLOWED_HOSTS", raising=False)

        settings = Settings()

        assert settings.cors_origins == ["http://localhost:3000"]
        assert settings.allowed_hosts == ["*"]


class TestDeployedEnvironmentGuards:
    def test_placeholder_secret_key_is_rejected_when_deployed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Refusing to boot is deliberately harsher than logging a warning."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("SECURITY_SECRET_KEY", "insecure-local-development-key-change-me")
        monkeypatch.setenv("ALLOWED_HOSTS", "app.droppilot.ai")

        with pytest.raises(ValueError, match="placeholder"):
            Settings()

    def test_wildcard_host_is_rejected_when_deployed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A wildcard Host permits Host header attacks."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("SECURITY_SECRET_KEY", "a" * 48)
        monkeypatch.setenv("ALLOWED_HOSTS", "*")

        with pytest.raises(ValueError, match="wildcard"):
            Settings()

    def test_a_short_signing_key_is_rejected_in_every_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """RFC 7518 section 3.2: HS256 needs at least 32 bytes.

        Enforced everywhere, not only when deployed — a short key set locally is
        how a short key reaches a secret manager.
        """
        monkeypatch.setenv("ENVIRONMENT", "local")
        monkeypatch.setenv("SECURITY_SECRET_KEY", "too-short")

        with pytest.raises(ValueError, match="at least 32 characters"):
            Settings()

    def test_local_environment_permits_the_placeholder(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ENVIRONMENT", "local")
        monkeypatch.setenv("SECURITY_SECRET_KEY", "insecure-local-development-key-change-me")

        assert Settings().environment.value == "local"
