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

from app.core.config import (
    _ENV_FILES,
    AliExpressSettings,
    CelerySettings,
    DatabaseSettings,
    ObservabilitySettings,
    RedisSettings,
    SecuritySettings,
    Settings,
    StorageSettings,
    _EnvFileSettings,
)
from tests.environment import TEST_OTP_HMAC_KEY

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

        assert settings.cors_origins == ["http://localhost:3000", "http://localhost"]
        assert settings.allowed_hosts == ["*"]


class TestEnvFileLoading:
    """Every settings group must read the .env file.

    A regression guard for a defect found during Phase 3.5 live setup: only the
    root ``Settings`` declared ``env_file``, so every nested group
    (``POSTGRES_*``, ``SECURITY_*``, ``ALIEXPRESS_*``, ...) read ``os.environ``
    and silently ignored the file.

    The failure was invisible — the application booted, reported healthy, and
    ran on default credentials while the operator believed their ``.env`` had
    been applied. Credentials genuinely present in the file were reported
    missing by the application.
    """

    @pytest.mark.parametrize(
        "settings_class",
        [
            Settings,
            AliExpressSettings,
            SecuritySettings,
            DatabaseSettings,
            RedisSettings,
            CelerySettings,
            ObservabilitySettings,
            StorageSettings,
        ],
    )
    def test_every_settings_class_inherits_the_env_file_base(self, settings_class: type) -> None:
        """Inheritance is checked rather than `model_config["env_file"]`.

        `conftest` blanks that key so the suite cannot read a developer's real
        `.env`, which would make this assertion pass for the wrong reason — or
        fail on a clean checkout. The base class is what actually carries the
        setting, and it is what a new settings group would forget to inherit.
        """
        assert issubclass(settings_class, _EnvFileSettings), (
            f"{settings_class.__name__} does not inherit _EnvFileSettings, so it "
            "will ignore .env and silently fall back to defaults."
        )

    def test_env_file_paths_are_absolute(self) -> None:
        """A relative path resolves against the working directory.

        Launching from the repository root and from `backend/` would then read
        different files, or none.
        """
        from pathlib import Path

        assert _ENV_FILES, "no .env locations are configured at all"
        for path in _ENV_FILES:
            assert Path(path).is_absolute()

    def test_a_nested_group_reads_values_from_a_file(
        self, tmp_path: object, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The behaviour itself, not just the configuration.

        Asserting the config key exists would pass even if pydantic stopped
        honouring it, so this writes a file and checks a nested group picks it
        up.
        """
        from pathlib import Path

        env_path = Path(str(tmp_path)) / ".env"
        env_path.write_text(
            "ALIEXPRESS_APP_KEY=from-the-file\nALIEXPRESS_ENVIRONMENT=production\n",
            encoding="utf-8",
        )

        # Ensure the environment cannot be the source of the value.
        monkeypatch.delenv("ALIEXPRESS_APP_KEY", raising=False)
        monkeypatch.delenv("ALIEXPRESS_ENVIRONMENT", raising=False)

        config = AliExpressSettings(_env_file=str(env_path))  # type: ignore[call-arg]

        assert config.app_key == "from-the-file"
        assert config.environment == "production"

    def test_the_environment_still_wins_over_a_file(
        self, tmp_path: object, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Precedence matters for deployment: a container's environment must
        override whatever file happens to be baked into the image."""
        from pathlib import Path

        env_path = Path(str(tmp_path)) / ".env"
        env_path.write_text("ALIEXPRESS_APP_KEY=from-the-file\n", encoding="utf-8")
        monkeypatch.setenv("ALIEXPRESS_APP_KEY", "from-the-environment")

        config = AliExpressSettings(_env_file=str(env_path))  # type: ignore[call-arg]

        assert config.app_key == "from-the-environment"


class TestDeployedEnvironmentGuards:
    def test_placeholder_secret_key_is_rejected_when_deployed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Refusing to boot is deliberately harsher than logging a warning."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("SECURITY_SECRET_KEY", "insecure-local-development-key-change-me")
        monkeypatch.setenv("ALLOWED_HOSTS", "app.droppilot.ai")
        # Pinned so this asserts the placeholder rule rather than whichever
        # deployed guard the shell's environment happens to trip first.
        monkeypatch.setenv("SECURITY_OTP_HMAC_KEY", TEST_OTP_HMAC_KEY)

        with pytest.raises(ValueError, match="placeholder"):
            Settings()

    def test_wildcard_host_is_rejected_when_deployed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A wildcard Host permits Host header attacks."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("SECURITY_SECRET_KEY", "a" * 48)
        monkeypatch.setenv("ALLOWED_HOSTS", "*")
        monkeypatch.setenv("SECURITY_OTP_HMAC_KEY", TEST_OTP_HMAC_KEY)

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
