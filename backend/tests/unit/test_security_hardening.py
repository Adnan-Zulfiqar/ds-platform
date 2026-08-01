"""Production security guards for authentication configuration and logging.

Written after a pre-deployment audit found four ways to start a *deployed*
environment insecurely, and a logging pipeline that emitted credentials
verbatim. Every case below was observed failing before the fix and is pinned
here so it cannot come back.

Refusing to boot is deliberately harsher than warning. A warning in a container
log is a warning nobody reads, and every one of these is silent at runtime —
the application starts, reports healthy, and is wrong.
"""

from __future__ import annotations

import io
import logging
from typing import Any

import pytest

from app.core.config import SecuritySettings, Settings

pytestmark = pytest.mark.unit

#: A valid Fernet key, generated for these tests and used nowhere else.
VALID_FERNET = "c2VjdXJlLWtleS10aGF0LWlzLTMyLWJ5dGVzLWxvbmcheA=="
VALID_SIGNING_KEY = "a" * 48

#: A deployed environment with nothing wrong with it. Each test breaks exactly
#: one thing, so a failure names the control that stopped working.
DEPLOYED: dict[str, str] = {
    "ENVIRONMENT": "production",
    "ALLOWED_HOSTS": "app.droppilot.ai",
    "SECURITY_SECRET_KEY": VALID_SIGNING_KEY,
    "SECURITY_ENCRYPTION_KEYS": VALID_FERNET,
    "LOG_INCLUDE_REQUEST_BODY": "false",
    "SECURITY_COOKIE_SECURE": "true",
}


@pytest.fixture
def deployed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bind a valid production environment, isolated from the developer's own."""
    for name in [
        *DEPLOYED,
        "SECURITY_JWT_ALGORITHM",
        "CORS_ORIGINS",
        "ALIEXPRESS_APP_KEY",
    ]:
        monkeypatch.delenv(name, raising=False)
    for name, value in DEPLOYED.items():
        monkeypatch.setenv(name, value)


class TestDeployedEncryptionKeys:
    """The failure mode here is worse than a broken signing key.

    A bad signing key breaks authentication loudly and at once. A bad
    encryption key does not: the service starts, reports healthy, serves
    traffic, and only fails when someone connects a supplier — by which point
    it reads as an integration bug rather than a deployment one.
    """

    def test_a_valid_production_configuration_still_starts(self, deployed: None) -> None:
        """The guard rail must not block a correct deployment."""
        assert Settings().environment.value == "production"

    def test_empty_encryption_keys_are_rejected(
        self, deployed: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SECURITY_ENCRYPTION_KEYS", "")

        with pytest.raises(ValueError, match="SECURITY_ENCRYPTION_KEYS is empty"):
            Settings()

    @pytest.mark.parametrize("published", sorted(SecuritySettings.PUBLISHED_TEST_ENCRYPTION_KEYS))
    def test_a_key_published_in_this_repository_is_rejected(
        self, deployed: None, monkeypatch: pytest.MonkeyPatch, published: str
    ) -> None:
        """These are printed in `conftest.py`, so they are public.

        A deployment that inherited one from a copied `.env` would encrypt every
        customer credential with a key any reader of this repository already
        has.
        """
        monkeypatch.setenv("SECURITY_ENCRYPTION_KEYS", published)

        with pytest.raises(ValueError, match="published in this"):
            Settings()

    def test_a_published_key_is_caught_even_when_not_first(
        self, deployed: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Keys are a rotation list. An old published key left at the end still
        decrypts everything encrypted while it was in front."""
        published = sorted(SecuritySettings.PUBLISHED_TEST_ENCRYPTION_KEYS)[0]
        monkeypatch.setenv("SECURITY_ENCRYPTION_KEYS", f"{VALID_FERNET},{published}")

        with pytest.raises(ValueError, match="published in this"):
            Settings()

    def test_the_signing_key_may_not_double_as_an_encryption_key(
        self, deployed: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """They are rotated on different schedules.

        A signing key can be replaced the moment a leak is suspected, at the
        cost of ending every session. An encryption key cannot — stored
        ciphertext must be re-encrypted first. Sharing one value blocks an
        urgent rotation behind a slow migration.
        """
        monkeypatch.setenv("SECURITY_ENCRYPTION_KEYS", VALID_SIGNING_KEY)

        with pytest.raises(ValueError, match="must not also appear"):
            Settings()


class TestDeployedCookieSecurity:
    def test_an_insecure_refresh_cookie_is_rejected(
        self, deployed: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The refresh cookie is the longest-lived credential a browser holds.

        Without `Secure` it travels over plain HTTP, where anyone on the path
        can lift it and mint access tokens for its full thirty-day life. It is
        weakened locally on purpose — the test client speaks HTTP — which is
        exactly why the deployed case needs a guard rather than a convention.
        """
        monkeypatch.setenv("SECURITY_COOKIE_SECURE", "false")

        with pytest.raises(ValueError, match="SECURITY_COOKIE_SECURE"):
            Settings()


class TestLocalDevelopmentIsUnaffected:
    """Hardening that breaks local development gets disabled, and then protects
    nothing. Each production guard is checked to be inert locally."""

    def test_local_starts_with_no_encryption_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENVIRONMENT", "local")
        monkeypatch.setenv("SECURITY_SECRET_KEY", SecuritySettings.LOCAL_PLACEHOLDER_KEY)
        monkeypatch.setenv("SECURITY_ENCRYPTION_KEYS", "")

        assert Settings().security.encryption_keys == []

    def test_local_starts_with_an_insecure_cookie(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Required: the test client speaks HTTP and would never send a Secure
        cookie, so every refresh test would fail for an unrelated reason."""
        monkeypatch.setenv("ENVIRONMENT", "local")
        monkeypatch.setenv("SECURITY_SECRET_KEY", SecuritySettings.LOCAL_PLACEHOLDER_KEY)
        monkeypatch.setenv("SECURITY_COOKIE_SECURE", "false")

        assert Settings().security.cookie_secure is False

    def test_local_starts_with_the_published_test_keys(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The suite itself uses them; rejecting them locally would break it."""
        monkeypatch.setenv("ENVIRONMENT", "local")
        monkeypatch.setenv("SECURITY_SECRET_KEY", SecuritySettings.LOCAL_PLACEHOLDER_KEY)
        monkeypatch.setenv(
            "SECURITY_ENCRYPTION_KEYS",
            ",".join(sorted(SecuritySettings.PUBLISHED_TEST_ENCRYPTION_KEYS)),
        )

        assert len(Settings().security.encryption_keys) == 2


class TestLogRedaction:
    """A safety net, not the primary defence.

    Call sites are expected not to log credentials, and the audit found none
    that did. But discipline describes the code as it is today, and this
    pipeline is also fed by third-party libraries and by whatever is written
    next year. Before this processor existed, six of six secret-shaped fields
    were emitted verbatim.
    """

    CANARY = "SuperSecretValue-CANARY-9f3a2b"

    def _emit(self, **fields: Any) -> str:
        from app.core.logging import configure_logging, get_logger

        configure_logging()
        buffer = io.StringIO()
        handler = logging.StreamHandler(buffer)
        root = logging.getLogger()
        root.addHandler(handler)
        root.setLevel(logging.INFO)
        try:
            get_logger("test.redaction").info("probe", **fields)
            handler.flush()
            return buffer.getvalue()
        finally:
            root.removeHandler(handler)

    @pytest.mark.parametrize(
        "field",
        [
            "password",
            "token",
            "access_token",
            "refresh_token",
            "secret",
            "app_secret",
            "authorization",
            "api_key",
            "private_key",
            "encryption_key",
            "cookie",
            "signature",
        ],
    )
    def test_a_credential_shaped_field_is_redacted(self, field: str) -> None:
        output = self._emit(**{field: self.CANARY})

        assert self.CANARY not in output
        assert "[redacted]" in output

    def test_the_field_name_survives_redaction(self) -> None:
        """The key is kept so a reader can tell the field was present.

        "There was an Authorization header and it was redacted" is useful;
        silence is not.
        """
        output = self._emit(authorization=self.CANARY)

        assert "authorization" in output
        assert self.CANARY not in output

    def test_a_nested_secret_is_redacted(self) -> None:
        """Payloads arrive as one object, so a top-level-only scrub would miss
        the common case."""
        output = self._emit(payload={"user": "ada", "password": self.CANARY})

        assert self.CANARY not in output

    def test_a_secret_inside_a_list_of_objects_is_redacted(self) -> None:
        output = self._emit(items=[{"token": self.CANARY}])

        assert self.CANARY not in output

    def test_ordinary_fields_are_untouched(self) -> None:
        """Over-redaction blinds the diagnostics this exists to protect."""
        output = self._emit(user_email="ada@example.com", count=42, path="/api/v1/products")

        assert "ada@example.com" in output
        assert "/api/v1/products" in output
        assert "42" in output

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("token_type", "access"),
            ("signature_header_present", True),
            ("has_token", False),
            ("token_count", 3),
        ],
    )
    def test_allowlisted_diagnostics_survive(self, field: str, value: Any) -> None:
        """`token_type` says which kind of token was rejected;
        `signature_header_present` answers whether a provider signs its
        webhooks at all. Redacting either would remove the answer and leave the
        question."""
        output = self._emit(**{field: value})

        assert "[redacted]" not in output
        assert str(value).lower() in output.lower()

    def test_a_boolean_derived_from_a_secret_is_kept(self) -> None:
        """`password_valid=False` is a useful diagnostic and reveals nothing."""
        output = self._emit(password_valid=False)

        assert "[redacted]" not in output

    def test_redaction_terminates_on_deeply_nested_input(self) -> None:
        """A pathological structure must not turn one log line into a hang."""
        nested: dict[str, Any] = {"password": self.CANARY}
        for _ in range(50):
            nested = {"inner": nested}

        output = self._emit(payload=nested)

        assert isinstance(output, str)
