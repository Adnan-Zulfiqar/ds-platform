"""EBAY-C1 — the four endpoints, driven over HTTP.

Real database, real encryption, real authorization, real Redis. eBay's wire is
the only thing faked, and no eBay credential is used.

The service-level guarantees are asserted in ``test_ebay_c1_connection``; what
is under test *here* is the boundary — who may call what, what a response is
allowed to contain, and where a browser is sent when consent goes wrong.

The most important assertions in this file are about what is **absent**: no
response body, no error envelope and no OpenAPI schema contains a token, a
ciphertext, an authorization code or a client secret, and no workspace can see
another's connection.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.redis import RedisPurpose, close_redis_clients, get_redis
from app.models.ebay import EbayConnection, EbayConnectionStatus
from tests.integration.conftest import registration_payload
from tests.integration.ebay_c1_live import (
    ACCESS_TOKEN,
    REFRESH_TOKEN,
    SELLER_USER_ID,
    SELLER_USERNAME,
    FakeEbay,
    install,
)

pytestmark = pytest.mark.integration

STATUS_URL = "/api/v1/integrations/ebay/status"
CONNECT_URL = "/api/v1/integrations/ebay/connect"
CALLBACK_URL = "/api/v1/integrations/ebay/callback"
DISCONNECT_URL = "/api/v1/integrations/ebay/disconnect"


@pytest.fixture(autouse=True)
async def fresh_redis() -> AsyncIterator[None]:
    """A Redis client bound to this test's event loop, and no stale state."""
    await close_redis_clients()
    client = get_redis(RedisPurpose.CACHE)
    stale = await client.keys("ebay:oauth:state:*")
    if stale:
        await client.delete(*stale)
    yield
    await close_redis_clients()


@pytest.fixture
def ebay(monkeypatch: pytest.MonkeyPatch) -> FakeEbay:
    fake = FakeEbay()
    install(monkeypatch, fake)
    return fake


async def register(client: AsyncClient, **overrides: Any) -> dict[str, Any]:
    response = await client.post("/api/v1/auth/register", json=registration_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


def auth_header(body: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {body['tokens']['accessToken']}"}


def token_with_roles(body: dict[str, Any], *roles: str) -> dict[str, str]:
    """Mint an access token for the same principal with different roles.

    Registration always creates an owner, and the platform has no endpoint for
    demoting yourself — so the only way to test what a viewer may do is to issue
    a viewer's token against the real signing key and let the real dependency
    decide.
    """
    identity = body["identity"]
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": identity["user"]["id"],
            "tid": identity["tenant"]["id"],
            "typ": "access",
            "jti": uuid.uuid4().hex,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=15)).timestamp()),
            "iss": settings.security.jwt_issuer,
            "aud": settings.security.jwt_audience,
            "roles": list(roles),
        },
        settings.security.secret_key.get_secret_value(),
        algorithm=settings.security.jwt_algorithm,
    )
    return {"Authorization": f"Bearer {token}"}


async def begin(client: AsyncClient, headers: dict[str, str]) -> str:
    response = await client.post(CONNECT_URL, headers=headers)
    assert response.status_code == 201, response.text
    return str(response.json()["state"])


async def complete(client: AsyncClient, state: str, *, code: str = "consent-code") -> Any:
    return await client.get(CALLBACK_URL, params={"code": code, "state": state})


async def connect_fully(client: AsyncClient, headers: dict[str, str]) -> None:
    response = await complete(client, await begin(client, headers))
    assert response.status_code == 303, response.text
    assert response.headers["location"].endswith("?ebay=connected"), response.headers["location"]


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


class TestStatus:
    async def test_requires_authentication(self, client: AsyncClient) -> None:
        assert (await client.get(STATUS_URL)).status_code == 401

    async def test_reports_configured_and_not_connected(
        self, client: AsyncClient, ebay: FakeEbay
    ) -> None:
        """Two separate facts, because they have different remedies.

        "This server has no eBay credentials" is an operator problem; "nobody
        has connected yet" is a merchant one, and a card that conflated them
        would show the wrong button.
        """
        body = await register(client)
        response = await client.get(STATUS_URL, headers=auth_header(body))

        assert response.status_code == 200
        assert response.json() == {"configured": True, "connected": False, "connection": None}

    async def test_reports_unconfigured_when_the_server_has_no_credentials(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings.ebay, "client_id", "")
        body = await register(client)
        response = await client.get(STATUS_URL, headers=auth_header(body))

        assert response.status_code == 200
        assert response.json()["configured"] is False

    async def test_a_viewer_may_read_the_status(self, client: AsyncClient, ebay: FakeEbay) -> None:
        """Whether a sales channel is working is operational information the
        whole team needs, including members who cannot change it."""
        body = await register(client)
        response = await client.get(STATUS_URL, headers=token_with_roles(body, "viewer"))
        assert response.status_code == 200

    async def test_a_connected_status_carries_no_credential_of_any_kind(
        self, client: AsyncClient, ebay: FakeEbay
    ) -> None:
        """The guarantee the response schema exists to provide.

        Asserted against the raw response text rather than parsed fields, so a
        token smuggled into a nested object, an error detail or an unexpected
        extra key is caught too.
        """
        body = await register(client)
        await connect_fully(client, auth_header(body))

        response = await client.get(STATUS_URL, headers=auth_header(body))
        assert response.status_code == 200

        raw = response.text
        for secret in (
            ACCESS_TOKEN,
            REFRESH_TOKEN,
            "test-only-cert-id",
            SELLER_USER_ID,
            "encrypted",
            "gAAAAA",  # the Fernet ciphertext prefix
        ):
            assert secret not in raw, f"{secret!r} reached an API response"

        payload = response.json()["connection"]
        assert payload["ebayUsername"] == SELLER_USERNAME
        assert payload["marketplaceId"] == "EBAY_GB"
        assert payload["needsReconnect"] is False
        assert "sell.inventory" in " ".join(payload["scopes"])

    async def test_a_connection_awaiting_reconnect_is_not_connected(
        self, client: AsyncClient, ebay: FakeEbay, db_session: AsyncSession
    ) -> None:
        """Computed server-side so every consumer agrees what it means.

        A client inferring "connected" from the enum would call a row that needs
        re-consent healthy, and the card would offer no way out of it.
        """
        body = await register(client)
        await connect_fully(client, auth_header(body))

        await db_session.execute(
            sa.update(EbayConnection).values(
                status=EbayConnectionStatus.RECONNECT_REQUIRED,
                reconnect_reason="refresh_token_revoked",
            )
        )

        payload = (await client.get(STATUS_URL, headers=auth_header(body))).json()
        assert payload["connected"] is False
        assert payload["connection"]["needsReconnect"] is True
        assert payload["connection"]["reconnectReason"] == "refresh_token_revoked"

    async def test_one_workspace_cannot_see_anothers_connection(
        self, client: AsyncClient, ebay: FakeEbay
    ) -> None:
        """The isolation claim, at the boundary where it would actually leak."""
        first = await register(client)
        await connect_fully(client, auth_header(first))

        second = await register(client)
        payload = (await client.get(STATUS_URL, headers=auth_header(second))).json()

        assert payload == {"configured": True, "connected": False, "connection": None}


# ---------------------------------------------------------------------------
# Connect
# ---------------------------------------------------------------------------


class TestConnect:
    async def test_returns_a_consent_url_for_this_server(
        self, client: AsyncClient, ebay: FakeEbay
    ) -> None:
        body = await register(client)
        response = await client.post(CONNECT_URL, headers=auth_header(body))

        assert response.status_code == 201
        payload = response.json()
        assert payload["authorizationUrl"].startswith("https://auth.ebay.com/oauth2/authorize?")
        assert "DropPilo-TestOnly-PRD-000000" in payload["authorizationUrl"]
        assert "DropPilot-TestOnly-RuName" in payload["authorizationUrl"]
        assert payload["state"]
        assert payload["expiresInSeconds"] == settings.ebay.oauth_state_ttl_seconds

    async def test_the_client_secret_never_appears_in_the_consent_url(
        self, client: AsyncClient, ebay: FakeEbay
    ) -> None:
        body = await register(client)
        response = await client.post(CONNECT_URL, headers=auth_header(body))
        assert "test-only-cert-id" not in response.text

    async def test_requires_admin_or_owner(self, client: AsyncClient, ebay: FakeEbay) -> None:
        """Connecting a sales channel decides where this workspace's listings
        and orders go. That is not a change a viewer or a member should make."""
        body = await register(client)
        for role in ("viewer", "member"):
            response = await client.post(CONNECT_URL, headers=token_with_roles(body, role))
            assert response.status_code == 403, f"{role} was allowed to connect"

    async def test_rejects_an_unauthenticated_request(self, client: AsyncClient) -> None:
        assert (await client.post(CONNECT_URL)).status_code == 401

    async def test_refuses_when_the_server_has_no_ebay_credentials(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fails closed, with a code the card can act on.

        Issuing a consent URL with an empty ``client_id`` would send the seller
        to an eBay error page with nothing this platform could diagnose.
        """
        monkeypatch.setattr(settings.ebay, "client_id", "")
        body = await register(client)
        response = await client.post(CONNECT_URL, headers=auth_header(body))

        assert response.status_code == 422
        assert response.json()["code"] == "ebay_not_configured"


# ---------------------------------------------------------------------------
# Callback
# ---------------------------------------------------------------------------


class TestCallback:
    async def test_a_successful_consent_redirects_into_the_application(
        self, client: AsyncClient, ebay: FakeEbay
    ) -> None:
        """A browser arrives here from eBay's consent screen and must land on a
        page, not on a JSON body it cannot read."""
        body = await register(client)
        response = await complete(client, await begin(client, auth_header(body)))

        assert response.status_code == 303
        assert response.headers["location"] == (
            f"{settings.ebay.frontend_return_url}?ebay=connected"
        )

    async def test_no_bearer_token_is_required(self, client: AsyncClient, ebay: FakeEbay) -> None:
        """eBay redirects the browser here directly, with no session.

        The state token — issued during an authenticated admin request and
        validated server-side — is what binds the callback to a workspace.
        """
        body = await register(client)
        state = await begin(client, auth_header(body))

        response = await client.get(CALLBACK_URL, params={"code": "consent-code", "state": state})
        assert response.status_code == 303
        assert response.headers["location"].endswith("?ebay=connected")

    async def test_a_declined_consent_says_so(self, client: AsyncClient, ebay: FakeEbay) -> None:
        """The seller pressed "Not now". Not an error, and not a failure to
        report as one."""
        response = await client.get(
            CALLBACK_URL,
            params={"error": "access_denied", "error_description": "user declined"},
        )
        assert response.status_code == 303
        assert response.headers["location"].endswith("?ebay=denied")
        assert ebay.token_requests == []

    async def test_an_upstream_message_is_never_reflected_into_the_redirect(
        self, client: AsyncClient, ebay: FakeEbay
    ) -> None:
        """The reason code comes from a fixed vocabulary this application owns.

        eBay's ``error_description`` is attacker-influenceable text on an
        unauthenticated endpoint; putting it in a URL the browser then loads is
        how a reflected-content bug is built.
        """
        response = await client.get(
            CALLBACK_URL,
            params={
                "error": "access_denied",
                "error_description": "<script>alert(1)</script>",
            },
        )
        location = response.headers["location"]
        assert location.endswith("?ebay=denied")
        assert "script" not in location

    async def test_missing_parameters_are_refused(
        self, client: AsyncClient, ebay: FakeEbay
    ) -> None:
        for params in ({}, {"code": "x"}, {"state": "y"}):
            response = await client.get(CALLBACK_URL, params=params)
            assert response.status_code == 303
            assert response.headers["location"].endswith("?ebay=invalid")
        assert ebay.token_requests == []

    async def test_an_unknown_state_is_refused_without_reaching_ebay(
        self, client: AsyncClient, ebay: FakeEbay
    ) -> None:
        response = await complete(client, "a-state-this-server-never-issued")
        assert response.status_code == 303
        assert response.headers["location"].endswith("?ebay=invalid")
        assert ebay.token_requests == [], "a forged callback spent an authorization code"

    async def test_a_replayed_callback_is_refused(
        self, client: AsyncClient, ebay: FakeEbay
    ) -> None:
        body = await register(client)
        state = await begin(client, auth_header(body))

        assert (await complete(client, state)).headers["location"].endswith("?ebay=connected")
        replayed = await complete(client, state)
        assert replayed.headers["location"].endswith("?ebay=invalid")

    async def test_a_seller_already_linked_elsewhere_is_named_as_such(
        self, client: AsyncClient, ebay: FakeEbay
    ) -> None:
        """A distinct code, because the remedy is distinct.

        "Disconnect it from the other workspace first" is actionable; a generic
        failure is a support ticket. The code says only that much — never which
        workspace holds it.

        Stops at the redirect: recovering from the integrity error rolls the
        repository's session back, which in this harness is the whole test
        transaction. The database-level claim — that nothing was written for the
        second workspace — is asserted in ``test_ebay_c1_connection`` against
        committed rows, where it can be.
        """
        first = await register(client)
        await connect_fully(client, auth_header(first))

        second = await register(client)
        response = await complete(client, await begin(client, auth_header(second)))

        assert response.status_code == 303
        location = response.headers["location"]
        assert location.endswith("?ebay=already_linked")
        assert first["identity"]["tenant"]["id"] not in location

    async def test_the_authorization_code_never_appears_in_the_redirect(
        self, client: AsyncClient, ebay: FakeEbay
    ) -> None:
        """A live credential must not end up in a browser history or a referrer."""
        body = await register(client)
        state = await begin(client, auth_header(body))
        response = await complete(client, state, code="v^1.1#i^1#secret-consent-code")

        location = response.headers["location"]
        assert "secret-consent-code" not in location
        assert state not in location


# ---------------------------------------------------------------------------
# Disconnect
# ---------------------------------------------------------------------------


class TestDisconnect:
    async def test_removes_the_connection(self, client: AsyncClient, ebay: FakeEbay) -> None:
        body = await register(client)
        await connect_fully(client, auth_header(body))

        response = await client.delete(DISCONNECT_URL, headers=auth_header(body))
        assert response.status_code == 200

        payload = (await client.get(STATUS_URL, headers=auth_header(body))).json()
        assert payload == {"configured": True, "connected": False, "connection": None}

    async def test_is_idempotent(self, client: AsyncClient, ebay: FakeEbay) -> None:
        """A double click must not produce a confusing failure."""
        body = await register(client)
        assert (await client.delete(DISCONNECT_URL, headers=auth_header(body))).status_code == 200
        assert (await client.delete(DISCONNECT_URL, headers=auth_header(body))).status_code == 200

    async def test_requires_admin_or_owner(self, client: AsyncClient, ebay: FakeEbay) -> None:
        body = await register(client)
        for role in ("viewer", "member"):
            response = await client.delete(DISCONNECT_URL, headers=token_with_roles(body, role))
            assert response.status_code == 403, f"{role} was allowed to disconnect"

    async def test_rejects_an_unauthenticated_request(self, client: AsyncClient) -> None:
        assert (await client.delete(DISCONNECT_URL)).status_code == 401

    async def test_one_workspace_cannot_disconnect_another(
        self, client: AsyncClient, ebay: FakeEbay
    ) -> None:
        """There is no identifier to pass, which is the point.

        The connection is found from the caller's own tenant context, so there
        is no parameter an attacker could aim at somebody else's row.
        """
        first = await register(client)
        await connect_fully(client, auth_header(first))

        second = await register(client)
        assert (await client.delete(DISCONNECT_URL, headers=auth_header(second))).status_code == 200

        payload = (await client.get(STATUS_URL, headers=auth_header(first))).json()
        assert payload["connected"] is True, "another workspace's disconnect took this one down"


# ---------------------------------------------------------------------------
# The published contract
# ---------------------------------------------------------------------------


class TestOpenAPIContract:
    async def test_no_ebay_response_schema_can_carry_a_credential(
        self, client: AsyncClient
    ) -> None:
        """Structural, against the generated document rather than one response.

        A field that does not exist in the schema cannot leak in any response,
        including ones no test happens to exercise.
        """
        schema = (await client.get("/openapi.json")).json()
        forbidden = ("accessToken", "refreshToken", "encryptedAccessToken", "clientSecret")

        for name in ("EbayConnectionRead", "EbayStatusResponse", "EbayAuthorizationResponse"):
            properties = schema["components"]["schemas"][name].get("properties", {})
            leaked = [field for field in forbidden if field in properties]
            assert not leaked, f"{name} exposes {leaked}"

    async def test_the_four_endpoints_are_published(self, client: AsyncClient) -> None:
        schema = (await client.get("/openapi.json")).json()
        paths = schema["paths"]
        assert "get" in paths["/api/v1/integrations/ebay/status"]
        assert "post" in paths["/api/v1/integrations/ebay/connect"]
        assert "get" in paths["/api/v1/integrations/ebay/callback"]
        assert "delete" in paths["/api/v1/integrations/ebay/disconnect"]
