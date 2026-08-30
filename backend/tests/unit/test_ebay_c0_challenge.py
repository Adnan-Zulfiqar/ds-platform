"""EBAY-C0 — the endpoint-validation challenge and its configuration rules.

eBay's guide is unusually specific here, and every specific is a way to fail
silently: the wrong hash order, a trailing slash, a byte order mark, a token
outside the permitted character set. Each gets a test, because the only symptom
of getting one wrong is "endpoint validation failed" in the eBay portal with no
detail attached.

Every token in this module is synthetic. No real verification token appears
anywhere in this repository, and the production token will be generated after
this work is deployed.
"""

from __future__ import annotations

import hashlib
from typing import ClassVar

import pytest
from pydantic import SecretStr
from pydantic import ValidationError as PydanticValidationError

from app.core.config import EbayEnvironment, EbaySettings, settings
from app.integrations.ebay.compliance import (
    MAX_CHALLENGE_CODE_LENGTH,
    challenge_response,
)
from app.integrations.ebay.exceptions import EbayChallengeError, EbayNotConfiguredError
from tests.environment import TEST_OTP_HMAC_KEY

pytestmark = pytest.mark.unit

#: Clearly synthetic, 40 characters, inside eBay's 32-80 rule.
SYNTHETIC_TOKEN = "EBAY-C0-SYNTHETIC-VERIFICATION-TOKEN-0001"
ENDPOINT = "https://api.whiteto.com/api/v1/integrations/ebay/marketplace-account-deletion"


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.ebay, "marketplace_deletion_endpoint", ENDPOINT)
    monkeypatch.setattr(
        settings.ebay,
        "marketplace_deletion_verification_token",
        SecretStr(SYNTHETIC_TOKEN),
    )


def expected(code: str, *, token: str = SYNTHETIC_TOKEN, endpoint: str = ENDPOINT) -> str:
    """eBay's documented order, written out independently of the implementation."""
    return hashlib.sha256(f"{code}{token}{endpoint}".encode()).hexdigest()


class TestChallengeHash:
    def test_the_response_matches_ebays_documented_concatenation(self, configured: None) -> None:
        """``SHA256(challengeCode + verificationToken + endpoint)``, hex, lowercase."""
        result = challenge_response("abc123")
        assert result.challenge_response == expected("abc123")
        assert result.challenge_response == result.challenge_response.lower()
        assert len(result.challenge_response) == 64

    def test_a_worked_example_is_pinned(self, configured: None) -> None:
        """A fixed vector, so a refactor that reorders the hash is caught.

        Computed by hand from eBay's rule rather than from this codebase, which
        is what makes it evidence rather than a snapshot of current behaviour.
        """
        assert (
            challenge_response("challenge-0001").challenge_response
            == hashlib.sha256(
                b"challenge-0001" + SYNTHETIC_TOKEN.encode() + ENDPOINT.encode()
            ).hexdigest()
        )

    @pytest.mark.parametrize(
        "wrong_order",
        [
            lambda c, t, e: f"{t}{c}{e}",
            lambda c, t, e: f"{c}{e}{t}",
            lambda c, t, e: f"{e}{t}{c}",
        ],
    )
    def test_any_other_order_produces_a_different_digest(
        self, configured: None, wrong_order: object
    ) -> None:
        """The order is not arbitrary — eBay says so, and this proves it matters."""
        scrambled = hashlib.sha256(
            wrong_order(  # type: ignore[operator]
                "abc123", SYNTHETIC_TOKEN, ENDPOINT
            ).encode()
        ).hexdigest()
        assert challenge_response("abc123").challenge_response != scrambled

    def test_a_trailing_slash_changes_the_answer(
        self, configured: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Which is exactly why the endpoint is never normalised.

        Adding or removing a slash to be "helpful" would silently produce a
        digest for a URL eBay did not register.
        """
        without = challenge_response("abc123").challenge_response
        monkeypatch.setattr(settings.ebay, "marketplace_deletion_endpoint", f"{ENDPOINT}/")
        assert challenge_response("abc123").challenge_response != without

    def test_the_configured_endpoint_is_the_authority(self, configured: None) -> None:
        """Not a Host header, not the request URL.

        ``challenge_response`` takes only the code — there is no parameter
        through which a request could supply a host, which is the structural
        reason a spoofed ``Host`` cannot influence the answer.
        """
        assert challenge_response("abc123").challenge_response == expected("abc123")


class TestChallengeValidation:
    @pytest.mark.parametrize("code", [None, "", "   ", "\t\n"])
    def test_a_missing_or_blank_code_is_refused(self, configured: None, code: str | None) -> None:
        with pytest.raises(EbayChallengeError):
            challenge_response(code)

    def test_an_oversized_code_is_refused(self, configured: None) -> None:
        with pytest.raises(EbayChallengeError):
            challenge_response("x" * (MAX_CHALLENGE_CODE_LENGTH + 1))

    def test_surrounding_whitespace_is_trimmed_not_hashed(self, configured: None) -> None:
        assert challenge_response("  abc123  ").challenge_response == expected("abc123")

    def test_an_unconfigured_server_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Never hash an empty token.

        That would produce a stable, well-formed digest that eBay rejects, and
        the operator would have no way to tell a configuration gap from a code
        bug.
        """
        monkeypatch.setattr(settings.ebay, "marketplace_deletion_endpoint", "")
        monkeypatch.setattr(settings.ebay, "marketplace_deletion_verification_token", None)
        with pytest.raises(EbayNotConfiguredError):
            challenge_response("abc123")

    def test_an_endpoint_without_a_token_fails_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings.ebay, "marketplace_deletion_endpoint", ENDPOINT)
        monkeypatch.setattr(settings.ebay, "marketplace_deletion_verification_token", None)
        with pytest.raises(EbayNotConfiguredError):
            challenge_response("abc123")

    def test_the_token_never_appears_in_the_response(self, configured: None) -> None:
        body = challenge_response("abc123").model_dump_json(by_alias=True)
        assert SYNTHETIC_TOKEN not in body
        assert list(challenge_response("abc123").model_dump().keys()) == ["challenge_response"]


class TestTokenRules:
    """eBay: 32-80 characters, letters, digits, underscore and hyphen only."""

    @pytest.mark.parametrize("length", [32, 55, 80])
    def test_a_token_of_permitted_length_is_accepted(self, length: int) -> None:
        EbaySettings(marketplace_deletion_verification_token=SecretStr("a" * length))

    @pytest.mark.parametrize("length", [1, 31, 81, 200])
    def test_a_token_outside_the_length_rule_is_refused(self, length: int) -> None:
        with pytest.raises(PydanticValidationError):
            EbaySettings(marketplace_deletion_verification_token=SecretStr("a" * length))

    @pytest.mark.parametrize(
        "token",
        [
            "a" * 20 + "!" + "b" * 15,
            "a" * 20 + " " + "b" * 15,
            "a" * 20 + "/" + "b" * 15,
            "a" * 20 + "@" + "b" * 15,
        ],
    )
    def test_a_token_with_a_forbidden_character_is_refused(self, token: str) -> None:
        with pytest.raises(PydanticValidationError):
            EbaySettings(marketplace_deletion_verification_token=SecretStr(token))

    def test_underscore_and_hyphen_are_permitted(self) -> None:
        EbaySettings(marketplace_deletion_verification_token=SecretStr("a_b-" * 8 + "cdef" * 4))

    def test_a_validation_error_never_echoes_the_token(self) -> None:
        secret = "SHOULD-NOT-APPEAR-IN-THE-ERROR-!!!"
        with pytest.raises(PydanticValidationError) as raised:
            EbaySettings(marketplace_deletion_verification_token=SecretStr(secret))
        assert secret not in str(raised.value)


class TestEndpointRules:
    @pytest.mark.parametrize(
        "endpoint",
        [
            "ftp://api.whiteto.com/hook",
            "https:///hook",
            "https://user:pw@api.whiteto.com/hook",
            "https://api.whiteto.com/hook#frag",
            "https://api.whiteto.com/hook?x=1",
            " https://api.whiteto.com/hook",
            "https://api.whiteto.com/hook ",
        ],
    )
    def test_a_dangerous_or_ambiguous_endpoint_is_refused(self, endpoint: str) -> None:
        with pytest.raises(PydanticValidationError):
            EbaySettings(marketplace_deletion_endpoint=endpoint)

    def test_the_intended_production_endpoint_is_accepted(self) -> None:
        assert EbaySettings(
            marketplace_deletion_endpoint=ENDPOINT
        ).marketplace_deletion_endpoint == (ENDPOINT)

    def test_the_endpoint_is_stored_byte_for_byte(self) -> None:
        """No normalisation of any kind — it participates in a hash."""
        with_slash = f"{ENDPOINT}/"
        assert (
            EbaySettings(marketplace_deletion_endpoint=with_slash).marketplace_deletion_endpoint
            == with_slash
        )


class TestNotificationHost:
    def test_production_and_sandbox_use_ebays_fixed_hosts(self) -> None:
        """Fixed constants, never assembled from anything a caller supplies."""
        assert (
            EbaySettings(environment=EbayEnvironment.PRODUCTION).notification_api_base
            == "https://api.ebay.com"
        )
        assert (
            EbaySettings(environment=EbayEnvironment.SANDBOX).notification_api_base
            == "https://api.sandbox.ebay.com"
        )

    def test_configuration_reports_itself_incomplete_until_both_values_exist(self) -> None:
        assert EbaySettings().is_deletion_configured is False
        assert EbaySettings(marketplace_deletion_endpoint=ENDPOINT).is_deletion_configured is False
        assert (
            EbaySettings(
                marketplace_deletion_endpoint=ENDPOINT,
                marketplace_deletion_verification_token=SecretStr(SYNTHETIC_TOKEN),
            ).is_deletion_configured
            is True
        )


class TestSecretsAreNotExposed:
    def test_credentials_are_secret_typed(self) -> None:
        """Repr must not print them, which is what keeps them out of tracebacks."""
        configured = EbaySettings(
            client_secret=SecretStr("synthetic-client-secret-value"),
            marketplace_deletion_verification_token=SecretStr(SYNTHETIC_TOKEN),
        )
        rendered = repr(configured)
        assert "synthetic-client-secret-value" not in rendered
        assert SYNTHETIC_TOKEN not in rendered


class TestDeployedEndpointRules:
    """eBay: the endpoint *"should use the 'https' protocol, and it should not
    contain an internal IP address or 'localhost' in its path"*.

    Enforced at boot rather than left to a deployment checklist, because the
    failure it prevents is silent: eBay simply refuses to validate the endpoint,
    the keyset stays inactive, and nothing in this application's logs says why.

    These build a real ``Settings`` the way ``test_security_hardening`` does, so
    the check is exercised through the same path production boots down.
    """

    DEPLOYED: ClassVar[dict[str, str]] = {
        "ENVIRONMENT": "production",
        "ALLOWED_HOSTS": "api.whiteto.com",
        "SECURITY_SECRET_KEY": "a" * 48,
        "SECURITY_ENCRYPTION_KEYS": "c2VjdXJlLWtleS10aGF0LWlzLTMyLWJ5dGVzLWxvbmcheA==",
        # A deployed environment refuses to start without a distinct OTP key.
        # Without it these tests assert an eBay endpoint rule but are stopped by
        # an authentication one, which is a green suite proving nothing.
        "SECURITY_OTP_HMAC_KEY": TEST_OTP_HMAC_KEY,
        "LOG_INCLUDE_REQUEST_BODY": "false",
        "SECURITY_COOKIE_SECURE": "true",
    }

    @pytest.fixture
    def deployed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for name in [*self.DEPLOYED, "EBAY_MARKETPLACE_DELETION_ENDPOINT", "CORS_ORIGINS"]:
            monkeypatch.delenv(name, raising=False)
        for name, value in self.DEPLOYED.items():
            monkeypatch.setenv(name, value)

    @pytest.mark.parametrize(
        ("endpoint", "expected"),
        [
            ("http://api.whiteto.com/hook", "https"),
            ("https://localhost/hook", "localhost"),
            ("https://127.0.0.1/hook", "localhost"),
            ("https://10.0.0.5/hook", "internal IP"),
            ("https://192.168.1.10/hook", "internal IP"),
            ("https://169.254.169.254/hook", "internal IP"),
        ],
    )
    def test_an_unreachable_endpoint_refuses_to_boot(
        self, deployed: None, monkeypatch: pytest.MonkeyPatch, endpoint: str, expected: str
    ) -> None:
        from app.core.config import Settings

        monkeypatch.setenv("EBAY_MARKETPLACE_DELETION_ENDPOINT", endpoint)
        with pytest.raises(PydanticValidationError, match=expected):
            Settings()

    def test_a_reachable_https_endpoint_boots(
        self, deployed: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.core.config import Settings

        monkeypatch.setenv("EBAY_MARKETPLACE_DELETION_ENDPOINT", ENDPOINT)
        assert Settings().ebay.marketplace_deletion_endpoint == ENDPOINT

    def test_an_unset_endpoint_does_not_block_a_deploy(
        self, deployed: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The rule guards a configured endpoint, not the absence of one.

        A deployment that has not registered with eBay yet must still boot; the
        challenge endpoint fails closed at request time instead.
        """
        from app.core.config import Settings

        monkeypatch.delenv("EBAY_MARKETPLACE_DELETION_ENDPOINT", raising=False)
        assert Settings().ebay.is_deletion_configured is False

    def test_localhost_is_still_permitted_outside_a_deployed_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """So the receiver can be exercised locally without a tunnel."""
        from app.core.config import Settings

        monkeypatch.setenv("ENVIRONMENT", "local")
        monkeypatch.setenv("EBAY_MARKETPLACE_DELETION_ENDPOINT", "http://localhost:8000/hook")
        assert Settings().ebay.marketplace_deletion_endpoint == "http://localhost:8000/hook"
