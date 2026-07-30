"""End-to-end authentication flows against a real database.

Covers every flow named in the Phase 1 specification: registration, sign-in,
wrong password, token refresh, sign-out, expired tokens, unauthorised requests,
and tenant isolation.

These drive the HTTP API rather than calling services directly, so they also
cover dependency wiring, error translation, and cookie handling — the parts a
service-level test would miss.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from app.core.config import settings
from tests.integration.conftest import STRONG_PASSWORD, registration_payload

pytestmark = pytest.mark.integration

REFRESH_COOKIE = settings.security.refresh_cookie_name


async def register(client: AsyncClient, **overrides: Any) -> dict[str, Any]:
    response = await client.post("/api/v1/auth/register", json=registration_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


def auth_header(body: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {body['tokens']['accessToken']}"}


class TestRegistration:
    async def test_creates_tenant_user_and_owner_role(self, client: AsyncClient) -> None:
        body = await register(client, companyName="Acme Trading", email="ada@example.com")

        identity = body["identity"]
        assert identity["tenant"]["name"] == "Acme Trading"
        assert identity["tenant"]["slug"] == "acme-trading"
        assert identity["user"]["email"] == "ada@example.com"
        assert identity["roles"] == ["owner"]

    async def test_returns_tokens_and_sets_refresh_cookie(self, client: AsyncClient) -> None:
        response = await client.post("/api/v1/auth/register", json=registration_payload())
        body = response.json()

        assert body["tokens"]["accessToken"]
        assert body["tokens"]["expiresIn"] > 0
        # Never in the body for a browser client — it is in the httpOnly cookie.
        assert body["tokens"]["refreshToken"] is None
        assert REFRESH_COOKIE in response.cookies

    async def test_never_returns_the_password_hash(self, client: AsyncClient) -> None:
        response = await client.post("/api/v1/auth/register", json=registration_payload())
        assert "passwordHash" not in response.text
        assert "password_hash" not in response.text

    async def test_rejects_a_duplicate_email(self, client: AsyncClient) -> None:
        await register(client, email="taken@example.com")

        response = await client.post(
            "/api/v1/auth/register", json=registration_payload(email="taken@example.com")
        )
        assert response.status_code == 409
        assert response.json()["code"] == "conflict"

    async def test_rejects_a_weak_password(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/v1/auth/register", json=registration_payload(password="short")
        )
        assert response.status_code == 422
        assert response.json()["code"] == "validation_error"

    async def test_second_company_with_the_same_name_gets_a_distinct_slug(
        self, client: AsyncClient
    ) -> None:
        """Two customers called Acme is ordinary, not an error."""
        first = await register(client, companyName="Acme", email="one@example.com")
        second = await register(client, companyName="Acme", email="two@example.com")

        assert first["identity"]["tenant"]["slug"] == "acme"
        assert second["identity"]["tenant"]["slug"] == "acme-2"


class TestLogin:
    async def test_succeeds_with_correct_credentials(self, client: AsyncClient) -> None:
        await register(client, email="ada@example.com")

        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "ada@example.com", "password": STRONG_PASSWORD},
        )

        assert response.status_code == 200
        assert response.json()["identity"]["user"]["email"] == "ada@example.com"

    async def test_email_is_matched_case_insensitively(self, client: AsyncClient) -> None:
        await register(client, email="ada@example.com")

        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "ADA@Example.COM", "password": STRONG_PASSWORD},
        )
        assert response.status_code == 200

    async def test_rejects_a_wrong_password(self, client: AsyncClient) -> None:
        await register(client, email="ada@example.com")

        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "ada@example.com", "password": "Wrong-Password-1234"},
        )

        assert response.status_code == 401
        assert response.json()["code"] == "invalid_credentials"

    async def test_unknown_and_wrong_password_are_indistinguishable(
        self, client: AsyncClient
    ) -> None:
        """The response must not reveal whether an account exists.

        Any difference here turns the login form into an account enumeration
        oracle.
        """
        await register(client, email="ada@example.com")

        wrong_password = await client.post(
            "/api/v1/auth/login",
            json={"email": "ada@example.com", "password": "Wrong-Password-1234"},
        )
        unknown_account = await client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@example.com", "password": "Wrong-Password-1234"},
        )

        assert wrong_password.status_code == unknown_account.status_code
        assert wrong_password.json()["code"] == unknown_account.json()["code"]
        assert wrong_password.json()["message"] == unknown_account.json()["message"]


class TestProtectedAccess:
    async def test_rejects_a_request_with_no_token(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/auth/me")

        assert response.status_code == 401
        assert response.json()["code"] == "authentication_required"

    async def test_rejects_a_malformed_token(self, client: AsyncClient) -> None:
        response = await client.get(
            "/api/v1/auth/me", headers={"Authorization": "Bearer not-a-real-token"}
        )
        assert response.status_code == 401

    async def test_rejects_an_expired_token(self, client: AsyncClient) -> None:
        """A token past its expiry must not be honoured.

        Minted directly with a past expiry rather than waiting fifteen minutes.
        """
        import uuid
        from datetime import UTC, datetime, timedelta

        import jwt

        past = datetime.now(UTC) - timedelta(hours=2)
        token = jwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "tid": str(uuid.uuid4()),
                "typ": "access",
                "jti": uuid.uuid4().hex,
                "iat": int((past - timedelta(minutes=15)).timestamp()),
                "exp": int(past.timestamp()),
                "iss": settings.security.jwt_issuer,
                "aud": settings.security.jwt_audience,
                "roles": ["owner"],
            },
            settings.security.secret_key.get_secret_value(),
            algorithm=settings.security.jwt_algorithm,
        )

        response = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 401
        assert response.json()["code"] == "token_expired"

    async def test_me_returns_identity_tenant_and_roles(self, client: AsyncClient) -> None:
        body = await register(client, email="ada@example.com", companyName="Acme Trading")

        response = await client.get("/api/v1/auth/me", headers=auth_header(body))

        assert response.status_code == 200
        payload = response.json()
        assert payload["user"]["email"] == "ada@example.com"
        assert payload["tenant"]["name"] == "Acme Trading"
        assert payload["roles"] == ["owner"]


class TestTokenRotation:
    async def test_refresh_issues_a_new_access_token(self, client: AsyncClient) -> None:
        await register(client)

        response = await client.post("/api/v1/auth/refresh", json={})

        assert response.status_code == 200
        assert response.json()["tokens"]["accessToken"]

    async def test_refresh_rotates_the_refresh_token(self, client: AsyncClient) -> None:
        await register(client)
        original = client.cookies.get(REFRESH_COOKIE)

        await client.post("/api/v1/auth/refresh", json={})
        rotated = client.cookies.get(REFRESH_COOKIE)

        assert rotated is not None
        assert rotated != original

    async def test_reusing_a_consumed_token_is_rejected(self, client: AsyncClient) -> None:
        """Rotation must invalidate the token it consumed.

        Cookies are cleared before the replay so the stolen value in the body is
        the only credential presented. The handler prefers the cookie — correct
        for a real browser — so leaving it in place would test the live session
        rather than the stolen token.
        """
        await register(client)
        stolen = client.cookies.get(REFRESH_COOKIE)

        await client.post("/api/v1/auth/refresh", json={})

        client.cookies.clear()
        replay = await client.post("/api/v1/auth/refresh", json={"refreshToken": stolen})
        assert replay.status_code == 401

    async def test_reuse_terminates_every_session(self, client: AsyncClient) -> None:
        """Reuse means two parties hold the token, and we cannot tell which is
        which. The safe response is to end every session and make both sign in
        again.
        """
        await register(client)
        stolen = client.cookies.get(REFRESH_COOKIE)

        await client.post("/api/v1/auth/refresh", json={})
        rotated = client.cookies.get(REFRESH_COOKIE)

        # The attacker replays the captured token, tripping detection.
        client.cookies.clear()
        await client.post("/api/v1/auth/refresh", json={"refreshToken": stolen})

        # The legitimate client's freshly rotated token is now dead too.
        legitimate = await client.post("/api/v1/auth/refresh", json={"refreshToken": rotated})
        assert legitimate.status_code == 401

    async def test_access_token_cannot_be_used_to_refresh(self, client: AsyncClient) -> None:
        body = await register(client)
        access = body["tokens"]["accessToken"]

        client.cookies.delete(REFRESH_COOKIE)
        response = await client.post("/api/v1/auth/refresh", json={"refreshToken": access})

        assert response.status_code == 401


class TestLogout:
    async def test_revokes_the_refresh_token(self, client: AsyncClient) -> None:
        await register(client)

        logout = await client.post("/api/v1/auth/logout", json={})
        assert logout.status_code == 200

        response = await client.post("/api/v1/auth/refresh", json={})
        assert response.status_code == 401

    async def test_succeeds_without_a_session(self, client: AsyncClient) -> None:
        """Signing out must never fail — a user who sees an error and assumes
        they are still signed in is a worse outcome than a no-op."""
        response = await client.post("/api/v1/auth/logout", json={})
        assert response.status_code == 200

    async def test_logout_all_revokes_every_session(self, client: AsyncClient) -> None:
        body = await register(client, email="ada@example.com")

        response = await client.post("/api/v1/auth/logout-all", json={}, headers=auth_header(body))

        assert response.status_code == 200
        assert (await client.post("/api/v1/auth/refresh", json={})).status_code == 401


class TestTenantIsolation:
    async def test_a_user_cannot_see_another_tenants_users(self, client: AsyncClient) -> None:
        """The property the whole architecture exists to guarantee."""
        first = await register(client, companyName="Acme", email="ada@acme.example")
        second = await register(client, companyName="Globex", email="bob@globex.example")

        acme_users = await client.get("/api/v1/users", headers=auth_header(first))
        globex_users = await client.get("/api/v1/users", headers=auth_header(second))

        acme_emails = {u["email"] for u in acme_users.json()["items"]}
        globex_emails = {u["email"] for u in globex_users.json()["items"]}

        assert acme_emails == {"ada@acme.example"}
        assert globex_emails == {"bob@globex.example"}
        assert not acme_emails & globex_emails

    async def test_fetching_another_tenants_user_returns_404_not_403(
        self, client: AsyncClient
    ) -> None:
        """404 rather than 403: a 403 would confirm the id exists and let an
        attacker enumerate other tenants' identifiers."""
        first = await register(client, companyName="Acme", email="ada@acme.example")
        second = await register(client, companyName="Globex", email="bob@globex.example")

        victim_id = second["identity"]["user"]["id"]
        response = await client.get(f"/api/v1/users/{victim_id}", headers=auth_header(first))

        assert response.status_code == 404
        assert response.json()["code"] == "not_found"

    async def test_each_registration_creates_a_separate_tenant(self, client: AsyncClient) -> None:
        first = await register(client, companyName="Acme", email="ada@acme.example")
        second = await register(client, companyName="Globex", email="bob@globex.example")

        assert first["identity"]["tenant"]["id"] != second["identity"]["tenant"]["id"]
