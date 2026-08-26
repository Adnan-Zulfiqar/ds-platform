"""EBAY-C1 — the authorization request, asserted against eBay's own documents.

The consent URL is the one part of this flow that cannot be corrected after the
fact: a wrong endpoint, a wrong ``redirect_uri`` or an invented scope produces a
failure on eBay's page, where there is no log to read and no error body to
inspect. So it is pinned here character for character.

Every literal below was read from eBay's published documentation and OpenAPI
specifications on 26 August 2026 — not from memory. The scope strings came out
of the ``securitySchemes`` block of the spec for the API that needs each one.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from app.core.config import EbayEnvironment, settings
from app.integrations.ebay.oauth import (
    EBAY_OAUTH_SCOPES,
    EbayOAuthState,
    build_authorization_url,
    scope_parameter,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.ebay, "client_id", "DropPilo-DropPilo-PRD-abc123-def456")
    monkeypatch.setattr(settings.ebay, "redirect_uri_name", "DropPilot-RuName-PRD")
    monkeypatch.setattr(settings.ebay, "environment", EbayEnvironment.PRODUCTION)


class TestTheAuthorizationEndpoint:
    def test_production_consent_is_hosted_on_the_auth_host(self, configured: None) -> None:
        """``auth.ebay.com``, not ``api.ebay.com``.

        A different host from every API call, and pointing at the API host
        yields a page that is not a consent screen.
        """
        url = urlparse(build_authorization_url(state="s"))
        assert url.scheme == "https"
        assert url.netloc == "auth.ebay.com"
        assert url.path == "/oauth2/authorize"

    def test_sandbox_targets_the_sandbox_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings.ebay, "environment", EbayEnvironment.SANDBOX)
        assert settings.ebay.oauth_authorize_url == (
            "https://auth.sandbox.ebay.com/oauth2/authorize"
        )

    def test_the_token_endpoint_is_the_identity_service(self) -> None:
        assert settings.ebay.oauth_token_url.endswith("/identity/v1/oauth2/token")

    def test_the_identity_api_lives_on_apiz(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A genuine eBay quirk, and the source of a 404 that reads like a
        permissions error if it is got wrong."""
        monkeypatch.setattr(settings.ebay, "environment", EbayEnvironment.PRODUCTION)
        assert settings.ebay.identity_api_base == "https://apiz.ebay.com"


class TestTheConsentParameters:
    def test_every_required_parameter_is_present_and_correct(self, configured: None) -> None:
        params = parse_qs(urlparse(build_authorization_url(state="state-token")).query)
        assert params["client_id"] == ["DropPilo-DropPilo-PRD-abc123-def456"]
        assert params["response_type"] == ["code"]
        assert params["state"] == ["state-token"]

    def test_redirect_uri_is_the_runame_not_a_url(self, configured: None) -> None:
        """The parameter is named ``redirect_uri`` and is *not* a URL.

        eBay takes the opaque RuName here and resolves it to the accept and
        decline URLs configured against it in the portal. Sending an actual URL
        fails on eBay's own page with nothing diagnosable.
        """
        params = parse_qs(urlparse(build_authorization_url(state="s")).query)
        assert params["redirect_uri"] == ["DropPilot-RuName-PRD"]
        assert not params["redirect_uri"][0].startswith("http")

    def test_optional_parameters_are_omitted(self, configured: None) -> None:
        """``prompt`` and ``locale`` are documented as optional and left out.

        Forcing a re-login on an already-signed-in merchant is worse than what
        it prevents, and the marketplace comes from the seller's own account
        rather than a guess made here.
        """
        params = parse_qs(urlparse(build_authorization_url(state="s")).query)
        assert "prompt" not in params
        assert "locale" not in params

    def test_the_url_is_encoded_exactly_once(self, configured: None) -> None:
        """Double encoding is the classic OAuth bug and is invisible by eye.

        Parsing the query must recover the scope string with real spaces; a
        second encoding pass would leave ``%2520`` and eBay would reject the
        scope.
        """
        url = build_authorization_url(state="s")
        assert "%2520" not in url
        recovered = parse_qs(urlparse(url).query)["scope"][0]
        assert recovered == scope_parameter()
        assert " " in recovered


class TestTheScopeDecision:
    """The exact set, and — just as important — what is deliberately absent."""

    def test_the_audited_scopes_are_requested(self, configured: None) -> None:
        requested = set(
            parse_qs(urlparse(build_authorization_url(state="s")).query)["scope"][0].split()
        )
        assert requested == {
            "https://api.ebay.com/oauth/api_scope",
            "https://api.ebay.com/oauth/api_scope/commerce.identity.readonly",
            "https://api.ebay.com/oauth/api_scope/sell.account",
            "https://api.ebay.com/oauth/api_scope/sell.inventory",
            "https://api.ebay.com/oauth/api_scope/sell.fulfillment",
        }

    def test_no_finance_dispute_marketing_or_advertising_scope_is_requested(self) -> None:
        """Permissions this platform has no use for are not asked for.

        Each of these exists and would widen the consent screen. A merchant who
        sees a request for access to their money is right to refuse it.
        """
        forbidden = (
            "sell.finances",
            "sell.payment.dispute",
            "sell.marketing",
            "sell.advertising",
            "sell.reputation",
            "sell.stores",
        )
        joined = scope_parameter()
        for scope in forbidden:
            assert scope not in joined, f"{scope} is requested but nothing uses it"

    def test_no_extended_identity_scope_is_requested(self) -> None:
        """``commerce.identity.readonly`` alone returns the id without the person.

        The email, name, address and phone variants would each pull personal
        data this platform does not need — and would then have to be declared
        under the EBAY-C0 storage contract.
        """
        joined = scope_parameter()
        for extended in ("identity.email", "identity.name", "identity.address", "identity.phone"):
            assert extended not in joined

    def test_read_only_variants_are_not_paired_with_their_manage_scope(self) -> None:
        """eBay: "there is no need to specify a read-only scope if the
        corresponding view and manage scope is also being specified"."""
        joined = scope_parameter()
        for pair in ("sell.account", "sell.inventory", "sell.fulfillment"):
            assert f"{pair}.readonly" not in joined

    def test_the_scope_order_is_stable(self) -> None:
        """So the authorization URL can be asserted on at all.

        A set-derived order would make the URL differ between runs and turn
        every assertion above into a flake.
        """
        assert scope_parameter() == " ".join(EBAY_OAUTH_SCOPES)
        assert scope_parameter().split()[0] == "https://api.ebay.com/oauth/api_scope"


class TestTheStateToken:
    def test_state_is_long_and_unguessable(self) -> None:
        tokens = {
            EbayOAuthState.create(
                tenant_id="t", user_id="u", environment="production", return_to="/x"
            ).token
            for _ in range(200)
        }
        assert len(tokens) == 200, "state tokens collided — not from a CSPRNG"
        assert all(len(token) >= 40 for token in tokens)

    def test_the_binding_is_not_carried_inside_the_token(self) -> None:
        """The tenant lives server-side, because a binding in the token is a
        binding the caller can edit."""
        state = EbayOAuthState.create(
            tenant_id="11111111-1111-1111-1111-111111111111",
            user_id="22222222-2222-2222-2222-222222222222",
            environment="production",
            return_to="https://app.example.test/settings/integrations",
        )
        assert "1111" not in state.token
        assert "2222" not in state.token

    def test_a_state_round_trips_through_its_stored_form(self) -> None:
        original = EbayOAuthState.create(
            tenant_id="t-1", user_id="u-1", environment="production", return_to="/back"
        )
        restored = EbayOAuthState.from_dict(original.token, dict(original.to_dict()))
        assert restored.tenant_id == "t-1"
        assert restored.user_id == "u-1"
        assert restored.environment == "production"
        assert restored.return_to == "/back"
        assert restored.created_at == original.created_at


class TestConfigurationGate:
    def test_oauth_is_not_configured_without_a_runame(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fails closed. Without a RuName the consent request would carry an
        empty ``redirect_uri`` and be refused with nothing useful in the reply."""
        from pydantic import SecretStr

        monkeypatch.setattr(settings.ebay, "client_id", "id")
        monkeypatch.setattr(settings.ebay, "client_secret", SecretStr("secret"))
        monkeypatch.setattr(settings.ebay, "redirect_uri_name", "")
        assert settings.ebay.is_oauth_configured is False

    def test_oauth_is_configured_when_all_three_are_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pydantic import SecretStr

        monkeypatch.setattr(settings.ebay, "client_id", "id")
        monkeypatch.setattr(settings.ebay, "client_secret", SecretStr("secret"))
        monkeypatch.setattr(settings.ebay, "redirect_uri_name", "RuName")
        assert settings.ebay.is_oauth_configured is True
