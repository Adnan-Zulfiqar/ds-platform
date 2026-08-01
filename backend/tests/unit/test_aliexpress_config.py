"""Tests for AliExpress configuration and credential resolution.

Two properties are pinned here:

* **Credentials come from configuration, never from a literal.** The platform
  application belongs to the operator, so it must be settable per deployment.
* **The callback URL accepts either environment name.** Renaming a setting is
  exactly the kind of change that silently falls back to a default, and here the
  default is a localhost URL that AliExpress would reject — which presents as a
  credentials problem rather than a configuration one.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from app.core.config import AliExpressSettings, Settings
from app.core.exceptions import ValidationError
from app.integrations.aliexpress.service import AliExpressService

pytestmark = pytest.mark.unit

PLATFORM_KEY = "platform-app-key"
PLATFORM_SECRET = "platform-app-secret"
TENANT_KEY = "tenant-app-key"
TENANT_SECRET = "tenant-app-secret"

CALLBACK = "https://api.whiteto.com/api/v1/integrations/aliexpress/callback"


class TestCallbackUrlAlias:
    def test_accepts_the_console_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`ALIEXPRESS_CALLBACK_URL` matches the AliExpress console's wording."""
        monkeypatch.setenv("ALIEXPRESS_CALLBACK_URL", CALLBACK)
        monkeypatch.delenv("ALIEXPRESS_REDIRECT_URI", raising=False)

        assert AliExpressSettings().callback_url == CALLBACK

    def test_accepts_the_original_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Phase 3 shipped `ALIEXPRESS_REDIRECT_URI`; it must keep working.

        Dropping it would break any environment still using it, and the failure
        would look like bad credentials rather than a renamed setting.
        """
        monkeypatch.delenv("ALIEXPRESS_CALLBACK_URL", raising=False)
        monkeypatch.setenv("ALIEXPRESS_REDIRECT_URI", CALLBACK)

        assert AliExpressSettings().callback_url == CALLBACK

    def test_the_console_name_wins_when_both_are_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALIEXPRESS_CALLBACK_URL", CALLBACK)
        monkeypatch.setenv("ALIEXPRESS_REDIRECT_URI", "https://example.invalid/other")

        assert AliExpressSettings().callback_url == CALLBACK

    def test_falls_back_to_localhost_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ALIEXPRESS_CALLBACK_URL", raising=False)
        monkeypatch.delenv("ALIEXPRESS_REDIRECT_URI", raising=False)

        assert "localhost" in AliExpressSettings().callback_url


class TestApplicationSettings:
    def test_credentials_load_from_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALIEXPRESS_APP_KEY", PLATFORM_KEY)
        monkeypatch.setenv("ALIEXPRESS_APP_SECRET", PLATFORM_SECRET)

        config = AliExpressSettings()

        assert config.app_key == PLATFORM_KEY
        assert config.app_secret is not None
        assert config.app_secret.get_secret_value() == PLATFORM_SECRET

    def test_the_secret_is_masked_in_a_repr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`SecretStr` keeps the value out of tracebacks and debug output."""
        monkeypatch.setenv("ALIEXPRESS_APP_SECRET", PLATFORM_SECRET)

        assert PLATFORM_SECRET not in repr(AliExpressSettings())

    def test_defaults_are_empty_rather_than_placeholder_values(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No credential may be hardcoded, not even a fake one.

        A non-empty default would let a misconfigured deployment appear
        configured and fail later at the gateway.
        """
        monkeypatch.delenv("ALIEXPRESS_APP_KEY", raising=False)
        monkeypatch.delenv("ALIEXPRESS_APP_SECRET", raising=False)

        config = AliExpressSettings()

        assert config.app_key == ""
        assert config.app_secret is None

    def test_environment_defaults_to_test(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Matches the application's actual status; production is opt-in."""
        monkeypatch.delenv("ALIEXPRESS_ENVIRONMENT", raising=False)
        assert AliExpressSettings().environment == "test"

    def test_environment_rejects_an_unknown_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALIEXPRESS_ENVIRONMENT", "staging")
        with pytest.raises(Exception, match="environment"):
            AliExpressSettings()

    def test_settings_expose_aliexpress_configuration(self) -> None:
        assert isinstance(Settings().aliexpress, AliExpressSettings)


class TestCredentialResolution:
    """DropPilot owns the AliExpress developer application.

    Tenants authorize that application; they never supply an app key/secret.
    """

    def test_platform_credentials_are_used(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.integrations.aliexpress import service as service_module

        monkeypatch.setattr(service_module.settings.aliexpress, "app_key", PLATFORM_KEY)
        monkeypatch.setattr(
            service_module.settings.aliexpress, "app_secret", SecretStr(PLATFORM_SECRET)
        )

        assert AliExpressService.platform_credentials() == (
            PLATFORM_KEY,
            PLATFORM_SECRET,
        )
        assert AliExpressService.resolve_credentials(None, None) == (
            PLATFORM_KEY,
            PLATFORM_SECRET,
        )

    def test_tenant_credentials_are_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.integrations.aliexpress import service as service_module

        monkeypatch.setattr(service_module.settings.aliexpress, "app_key", PLATFORM_KEY)
        monkeypatch.setattr(
            service_module.settings.aliexpress, "app_secret", SecretStr(PLATFORM_SECRET)
        )

        with pytest.raises(ValidationError, match="owned by the platform"):
            AliExpressService.resolve_credentials(TENANT_KEY, TENANT_SECRET)

    @pytest.mark.parametrize(
        ("key", "secret"),
        [(TENANT_KEY, None), (None, TENANT_SECRET), (TENANT_KEY, "  ")],
    )
    def test_any_tenant_supplied_half_is_rejected(
        self, monkeypatch: pytest.MonkeyPatch, key: str | None, secret: str | None
    ) -> None:
        from app.integrations.aliexpress import service as service_module

        monkeypatch.setattr(service_module.settings.aliexpress, "app_key", PLATFORM_KEY)
        monkeypatch.setattr(
            service_module.settings.aliexpress, "app_secret", SecretStr(PLATFORM_SECRET)
        )

        with pytest.raises(ValidationError, match="owned by the platform"):
            AliExpressService.resolve_credentials(key, secret)

    def test_raises_when_nothing_is_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.integrations.aliexpress import service as service_module

        monkeypatch.setattr(service_module.settings.aliexpress, "app_key", "")
        monkeypatch.setattr(service_module.settings.aliexpress, "app_secret", None)

        with pytest.raises(ValidationError, match="not configured"):
            AliExpressService.platform_credentials()

    def test_the_error_does_not_leak_a_secret(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.integrations.aliexpress import service as service_module

        monkeypatch.setattr(service_module.settings.aliexpress, "app_key", PLATFORM_KEY)
        monkeypatch.setattr(
            service_module.settings.aliexpress, "app_secret", SecretStr(PLATFORM_SECRET)
        )

        with pytest.raises(ValidationError) as exc_info:
            AliExpressService.resolve_credentials(TENANT_KEY, TENANT_SECRET)

        assert PLATFORM_SECRET not in str(exc_info.value)
        assert TENANT_SECRET not in str(exc_info.value)
