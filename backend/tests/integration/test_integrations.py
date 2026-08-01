"""End-to-end tests for the AliExpress integration endpoints.

Real database, real encryption, real authorization. Only two things are faked,
both because they are external to what is under test:

* **Redis** — supplied by ``fakeredis``, so the OAuth state round trip is
  exercised rather than skipped on a machine without a Redis server.
* **AliExpress itself** — supplied by ``httpx.MockTransport``. No real
  credentials are used anywhere, and none exist.

The most important assertions here are the ones about what is *absent*: no
response body contains a secret, and no tenant can see another's connection.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from fakeredis import aioredis as fake_aioredis
from httpx import AsyncClient

from app.integrations.aliexpress import client as client_module
from app.integrations.aliexpress import service as service_module
from tests.integration.conftest import registration_payload

pytestmark = pytest.mark.integration

APP_KEY = "test-app-key-123"
APP_SECRET = "test-app-secret-never-real-value"

CONNECT_URL = "/api/v1/integrations/aliexpress/connect"
STATUS_URL = "/api/v1/integrations/aliexpress/status"
CALLBACK_URL = "/api/v1/integrations/aliexpress/callback"
DISCONNECT_URL = "/api/v1/integrations/aliexpress/disconnect"
WEBHOOK_URL = "/api/v1/integrations/aliexpress/webhook"


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> fake_aioredis.FakeRedis:
    """In-process Redis for OAuth state.

    Requiring a real server would mean these tests skip on any machine without
    one — and the OAuth state check is a CSRF defence, so it must not be the
    thing that goes untested.
    """
    client = fake_aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(service_module, "get_redis", lambda _purpose: client)
    return client


@pytest.fixture(autouse=True)
def platform_aliexpress_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Connect uses platform env credentials only — never a request body."""
    from pydantic import SecretStr

    monkeypatch.setattr(service_module.settings.aliexpress, "app_key", APP_KEY)
    monkeypatch.setattr(service_module.settings.aliexpress, "app_secret", SecretStr(APP_SECRET))


@pytest.fixture(autouse=True)
def _allow_outbound(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bypass the outbound rate limiter, which has its own tests."""
    from app.integrations.rate_limiter import RateLimitDecision

    async def _allow(self: Any, tenant_id: str) -> RateLimitDecision:
        return RateLimitDecision(allowed=True, remaining=99, retry_after_seconds=0)

    monkeypatch.setattr("app.integrations.rate_limiter.OutboundRateLimiter.acquire", _allow)


def patch_aliexpress(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    """Point the integration client at a mock transport."""

    class _Patched(httpx.AsyncClient):
        def __init__(self, **kwargs: Any) -> None:
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(**kwargs)

    monkeypatch.setattr(client_module.httpx, "AsyncClient", _Patched)


def token_handler(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "access_token": "issued-access-token",
            "refresh_token": "issued-refresh-token",
            "expires_in": 86400,
            "user_id": "seller-1",
        },
    )


async def register(client: AsyncClient, **overrides: Any) -> dict[str, Any]:
    response = await client.post("/api/v1/auth/register", json=registration_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


def auth_header(body: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {body['tokens']['accessToken']}"}


async def begin_connection(client: AsyncClient, headers: dict[str, str]) -> str:
    """Start a connection and return the OAuth state token."""
    response = await client.post(CONNECT_URL, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["state"]


class TestConnect:
    async def test_returns_an_authorization_url_and_state(self, client: AsyncClient) -> None:
        body = await register(client)
        response = await client.post(CONNECT_URL, headers=auth_header(body))

        assert response.status_code == 201
        payload = response.json()
        assert APP_KEY in payload["authorizationUrl"]
        assert payload["state"]
        assert payload["expiresInSeconds"] > 0

    async def test_the_app_secret_is_never_returned(self, client: AsyncClient) -> None:
        """The guarantee the whole schema design exists to provide."""
        body = await register(client)
        response = await client.post(CONNECT_URL, headers=auth_header(body))

        assert APP_SECRET not in response.text

    async def test_the_app_secret_is_not_stored_on_the_connection(
        self, client: AsyncClient, db_session: Any
    ) -> None:
        """Platform app secret stays in env — never on the tenant row."""
        body = await register(client)
        await begin_connection(client, auth_header(body))

        raw = (
            await db_session.execute(
                sa.text("SELECT encrypted_app_secret FROM aliexpress_connections")
            )
        ).scalar_one()

        assert raw is None

    async def test_requires_admin_or_owner(self, client: AsyncClient) -> None:
        """Connecting decides where every future order is placed.

        A viewer must not be able to make that change.
        """
        import uuid as uuid_module
        from datetime import UTC, datetime, timedelta

        import jwt

        from app.core.config import settings

        body = await register(client)
        identity = body["identity"]
        now = datetime.now(UTC)
        viewer_token = jwt.encode(
            {
                "sub": identity["user"]["id"],
                "tid": identity["tenant"]["id"],
                "typ": "access",
                "jti": uuid_module.uuid4().hex,
                "iat": int(now.timestamp()),
                "exp": int((now + timedelta(minutes=15)).timestamp()),
                "iss": settings.security.jwt_issuer,
                "aud": settings.security.jwt_audience,
                "roles": ["viewer"],
            },
            settings.security.secret_key.get_secret_value(),
            algorithm=settings.security.jwt_algorithm,
        )

        response = await client.post(
            CONNECT_URL,
            headers={"Authorization": f"Bearer {viewer_token}"},
        )

        assert response.status_code == 403

    async def test_rejects_an_unauthenticated_request(self, client: AsyncClient) -> None:
        response = await client.post(CONNECT_URL)
        assert response.status_code == 401


class TestStatus:
    async def test_reports_not_connected_initially(self, client: AsyncClient) -> None:
        body = await register(client)
        response = await client.get(STATUS_URL, headers=auth_header(body))

        assert response.status_code == 200
        assert response.json() == {"connected": False, "connection": None}

    async def test_a_pending_connection_is_not_connected(self, client: AsyncClient) -> None:
        """Started but not completed is not the same as connected."""
        body = await register(client)
        await begin_connection(client, auth_header(body))

        payload = (await client.get(STATUS_URL, headers=auth_header(body))).json()

        assert payload["connected"] is False
        assert payload["connection"]["status"] == "pending"
        assert payload["connection"]["appKey"] == APP_KEY

    async def test_never_exposes_credentials(self, client: AsyncClient) -> None:
        body = await register(client)
        await begin_connection(client, auth_header(body))

        response = await client.get(STATUS_URL, headers=auth_header(body))

        assert APP_SECRET not in response.text
        for forbidden in ("encryptedAppSecret", "encrypted_app_secret", "accessToken"):
            assert forbidden not in response.text

    async def test_readable_by_any_authenticated_role(self, client: AsyncClient) -> None:
        """Whether the integration is healthy is operational information."""
        body = await register(client)
        assert (await client.get(STATUS_URL, headers=auth_header(body))).status_code == 200


class TestCallback:
    async def test_completes_the_connection(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        patch_aliexpress(monkeypatch, token_handler)

        body = await register(client)
        headers = auth_header(body)
        state = await begin_connection(client, headers)

        response = await client.get(
            CALLBACK_URL,
            params={"code": "auth-code", "state": state},
            headers=headers,
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert "aliexpress=connected" in response.headers["location"]

        status = (await client.get(STATUS_URL, headers=headers)).json()
        assert status["connected"] is True
        assert status["connection"]["status"] == "connected"

    async def test_completes_without_a_bearer_token(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AliExpress redirects the browser here; no Authorization header arrives."""
        patch_aliexpress(monkeypatch, token_handler)

        body = await register(client)
        headers = auth_header(body)
        state = await begin_connection(client, headers)

        response = await client.get(
            CALLBACK_URL,
            params={"code": "auth-code", "state": state},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert "aliexpress=connected" in response.headers["location"]

        status = (await client.get(STATUS_URL, headers=headers)).json()
        assert status["connected"] is True

    async def test_tokens_are_stored_encrypted(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch, db_session: Any
    ) -> None:
        patch_aliexpress(monkeypatch, token_handler)

        body = await register(client)
        headers = auth_header(body)
        state = await begin_connection(client, headers)
        await client.get(
            CALLBACK_URL,
            params={"code": "auth-code", "state": state},
            headers=headers,
            follow_redirects=False,
        )

        row = (
            await db_session.execute(
                sa.text(
                    "SELECT encrypted_access_token, encrypted_refresh_token "
                    "FROM aliexpress_connections"
                )
            )
        ).one()

        assert "issued-access-token" not in row[0]
        assert "issued-refresh-token" not in row[1]
        assert row[0].startswith("gAAAAA")

    async def test_an_unknown_state_is_rejected(self, client: AsyncClient) -> None:
        """The CSRF defence.

        Without it, an attacker could complete consent with their own AliExpress
        account and deliver the code to a victim's browser, binding the
        attacker's supplier account to the victim's workspace.
        """
        body = await register(client)
        headers = auth_header(body)
        await begin_connection(client, headers)

        response = await client.get(
            CALLBACK_URL,
            params={"code": "auth-code", "state": "not-a-real-state"},
            headers=headers,
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert "aliexpress=failed" in response.headers["location"]
        assert (await client.get(STATUS_URL, headers=headers)).json()["connected"] is False

    async def test_a_state_cannot_be_replayed(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Consumed on first use, so a repeated callback fails."""
        patch_aliexpress(monkeypatch, token_handler)

        body = await register(client)
        headers = auth_header(body)
        state = await begin_connection(client, headers)

        first = await client.get(
            CALLBACK_URL,
            params={"code": "auth-code", "state": state},
            headers=headers,
            follow_redirects=False,
        )
        second = await client.get(
            CALLBACK_URL,
            params={"code": "auth-code", "state": state},
            headers=headers,
            follow_redirects=False,
        )

        assert "aliexpress=connected" in first.headers["location"]
        assert "aliexpress=failed" in second.headers["location"]

    async def test_a_denied_consent_redirects_without_connecting(self, client: AsyncClient) -> None:
        body = await register(client)
        headers = auth_header(body)

        response = await client.get(
            CALLBACK_URL,
            params={"error": "access_denied"},
            headers=headers,
            follow_redirects=False,
        )

        assert "aliexpress=denied" in response.headers["location"]

    async def test_missing_parameters_redirect_without_connecting(
        self, client: AsyncClient
    ) -> None:
        body = await register(client)
        response = await client.get(CALLBACK_URL, headers=auth_header(body), follow_redirects=False)
        assert "aliexpress=invalid" in response.headers["location"]

    async def test_rejected_credentials_leave_the_connection_in_error(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Invalid credentials must not present as a working connection."""

        def rejecting(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"code": "25", "msg": "Invalid signature"})

        patch_aliexpress(monkeypatch, rejecting)

        body = await register(client)
        headers = auth_header(body)
        state = await begin_connection(client, headers)

        response = await client.get(
            CALLBACK_URL,
            params={"code": "auth-code", "state": state},
            headers=headers,
            follow_redirects=False,
        )

        assert "aliexpress=failed" in response.headers["location"]

        status = (await client.get(STATUS_URL, headers=headers)).json()
        assert status["connected"] is False
        assert status["connection"]["status"] == "error"
        assert status["connection"]["lastError"]


class TestTokenExpiry:
    async def test_a_connection_without_expiry_reports_expired(self, client: AsyncClient) -> None:
        """An unknown expiry is treated as expired throughout.

        Acting on a token of unknown validity risks a sync failing in a way that
        looks like an outage.
        """
        body = await register(client)
        await begin_connection(client, auth_header(body))

        payload = (await client.get(STATUS_URL, headers=auth_header(body))).json()
        assert payload["connection"]["isTokenExpired"] is True

    async def test_a_fresh_token_is_not_expired(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        patch_aliexpress(monkeypatch, token_handler)

        body = await register(client)
        headers = auth_header(body)
        state = await begin_connection(client, headers)
        await client.get(
            CALLBACK_URL,
            params={"code": "auth-code", "state": state},
            headers=headers,
            follow_redirects=False,
        )

        payload = (await client.get(STATUS_URL, headers=headers)).json()
        assert payload["connection"]["isTokenExpired"] is False
        assert payload["connection"]["tokenExpiresAt"]


class TestDisconnect:
    async def test_removes_the_connection(self, client: AsyncClient, db_session: Any) -> None:
        body = await register(client)
        headers = auth_header(body)
        await begin_connection(client, headers)

        response = await client.delete(DISCONNECT_URL, headers=headers)
        assert response.status_code == 200

        assert (await client.get(STATUS_URL, headers=headers)).json()["connected"] is False

        # The row is gone, not soft-deleted: a customer who disconnects has
        # asked us to forget their credentials.
        remaining = (
            await db_session.execute(sa.text("SELECT COUNT(*) FROM aliexpress_connections"))
        ).scalar_one()
        assert remaining == 0

    async def test_is_idempotent(self, client: AsyncClient) -> None:
        """A double click must not produce a confusing error."""
        body = await register(client)
        headers = auth_header(body)

        assert (await client.delete(DISCONNECT_URL, headers=headers)).status_code == 200
        assert (await client.delete(DISCONNECT_URL, headers=headers)).status_code == 200

    async def test_requires_admin_or_owner(self, client: AsyncClient) -> None:
        import uuid as uuid_module
        from datetime import UTC, datetime, timedelta

        import jwt

        from app.core.config import settings

        body = await register(client)
        identity = body["identity"]
        now = datetime.now(UTC)
        member_token = jwt.encode(
            {
                "sub": identity["user"]["id"],
                "tid": identity["tenant"]["id"],
                "typ": "access",
                "jti": uuid_module.uuid4().hex,
                "iat": int(now.timestamp()),
                "exp": int((now + timedelta(minutes=15)).timestamp()),
                "iss": settings.security.jwt_issuer,
                "aud": settings.security.jwt_audience,
                "roles": ["member"],
            },
            settings.security.secret_key.get_secret_value(),
            algorithm=settings.security.jwt_algorithm,
        )

        response = await client.delete(
            DISCONNECT_URL, headers={"Authorization": f"Bearer {member_token}"}
        )
        assert response.status_code == 403


class TestTenantIsolation:
    async def test_a_tenant_cannot_see_another_tenants_connection(
        self, client: AsyncClient
    ) -> None:
        """The property that matters most here.

        A leaked row does not expose a product listing — it exposes another
        company's supplier credentials.
        """
        first = await register(client, email="one@acme.example", companyName="Acme")
        await begin_connection(client, auth_header(first))

        second = await register(client, email="two@globex.example", companyName="Globex")

        payload = (await client.get(STATUS_URL, headers=auth_header(second))).json()

        assert payload["connected"] is False
        assert payload["connection"] is None

    async def test_disconnecting_does_not_affect_another_tenant(self, client: AsyncClient) -> None:
        first = await register(client, email="one@acme.example", companyName="Acme")
        await begin_connection(client, auth_header(first))

        second = await register(client, email="two@globex.example", companyName="Globex")
        await client.delete(DISCONNECT_URL, headers=auth_header(second))

        # The first tenant's connection survives.
        payload = (await client.get(STATUS_URL, headers=auth_header(first))).json()
        assert payload["connection"] is not None
        assert payload["connection"]["appKey"] == APP_KEY

    async def test_each_tenant_keeps_its_own_connection(self, client: AsyncClient) -> None:
        """Both tenants share the platform app key but keep separate rows."""
        first = await register(client, email="one@acme.example", companyName="Acme")
        await begin_connection(client, auth_header(first))

        second = await register(client, email="two@globex.example", companyName="Globex")
        await begin_connection(client, auth_header(second))

        acme = (await client.get(STATUS_URL, headers=auth_header(first))).json()
        globex = (await client.get(STATUS_URL, headers=auth_header(second))).json()

        assert acme["connection"]["id"] != globex["connection"]["id"]
        assert acme["connection"]["appKey"] == APP_KEY
        assert globex["connection"]["appKey"] == APP_KEY


class TestWebhook:
    async def test_accepts_an_unauthenticated_json_payload(self, client: AsyncClient) -> None:
        response = await client.post(
            WEBHOOK_URL,
            json={"message_type": "ORDER_STATUS", "order_id": "12345"},
        )

        assert response.status_code == 200
        assert response.json() == {"status": "received"}

    async def test_accepts_an_empty_body(self, client: AsyncClient) -> None:
        response = await client.post(WEBHOOK_URL, content=b"")

        assert response.status_code == 200
        assert response.json() == {"status": "received"}

    async def test_rejects_invalid_signature_when_secret_configured(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pydantic import SecretStr

        monkeypatch.setattr(
            "app.integrations.aliexpress.webhook.settings.aliexpress.webhook_secret",
            SecretStr("integration-webhook-secret"),
        )
        response = await client.post(
            WEBHOOK_URL,
            content=b'{"message_id":"bad-sig"}',
            headers={
                "content-type": "application/json",
                "x-aliexpress-signature": "00" * 32,
            },
        )
        assert response.status_code == 401

    async def test_accepts_valid_signature_when_secret_configured(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pydantic import SecretStr

        from app.integrations.aliexpress.webhook_security import compute_hmac_sha256_hex

        secret = "integration-webhook-secret"
        body = b'{"message_id":"good-sig","type":"ORDER_STATUS"}'
        digest = compute_hmac_sha256_hex(secret=secret, raw_body=body)
        monkeypatch.setattr(
            "app.integrations.aliexpress.webhook.settings.aliexpress.webhook_secret",
            SecretStr(secret),
        )
        response = await client.post(
            WEBHOOK_URL,
            content=body,
            headers={
                "content-type": "application/json",
                "x-aliexpress-signature": digest,
            },
        )
        assert response.status_code == 200
        assert response.json() == {"status": "received"}

    async def test_is_documented_in_openapi(self, client: AsyncClient) -> None:
        spec = (await client.get("/openapi.json")).json()
        operation = spec["paths"][WEBHOOK_URL]["post"]

        assert operation["summary"] == "Receive AliExpress push notifications"
        assert (
            "AliExpressWebhookAckResponse"
            in operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        )
        assert operation.get("security") is None
