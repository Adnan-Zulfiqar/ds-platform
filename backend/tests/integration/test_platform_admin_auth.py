"""Track E5a — platform operator sign-in, through the API (D-015).

Real Postgres (migrations). The panel is enabled for 127.0.0.1, the address
the test client connects from, except where a test checks it is off.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import totp
from app.core.config import settings
from app.models.platform_admin import PlatformAdmin, PlatformAdminAudit
from app.services import platform_admin as platform_service
from app.services.platform_admin import PlatformAdminService
from tests.integration.conftest import STRONG_PASSWORD
from tests.integration.test_ebay_c1_api import auth_header, register

pytestmark = pytest.mark.integration

LOGIN = "/api/v1/platform/auth/login"
ME = "/api/v1/platform/me"
REAUTH = "/api/v1/platform/auth/reauth"
EMAIL = "ops@droppilot.example"


@pytest.fixture
def panel(monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession) -> None:
    monkeypatch.setattr(settings.platform_admin, "allowed_cidrs", "127.0.0.1/32")

    @asynccontextmanager
    async def shared() -> AsyncIterator[AsyncSession]:
        # The failure audit commits in its own transaction in production; in
        # tests it shares the test session so it can see the admin row.
        yield db_session
        await db_session.flush()

    monkeypatch.setattr(platform_service, "transaction", shared)


async def make_admin(
    db_session: AsyncSession, *, email: str = EMAIL, role: str = "super_admin"
) -> str:
    created = await PlatformAdminService(db_session).create_admin(
        email=email, password=STRONG_PASSWORD, role=role
    )
    return created.totp_secret


def code(secret: str, *, ahead: int = 0) -> str:
    """``ahead`` steps into the future, inside the ±1 drift window: a second
    code for the same account in one test (a code is good once)."""
    return totp.code_at(secret, totp.current_step() + ahead)


async def sign_in(
    client: AsyncClient, secret: str, *, email: str = EMAIL, reauth: bool = False
) -> dict[str, str]:
    response = await client.post(
        LOGIN, json={"email": email, "password": STRONG_PASSWORD, "code": code(secret)}
    )
    assert response.status_code == 200, response.text
    headers = {"Authorization": f"Bearer {response.json()['accessToken']}"}
    if reauth:
        confirmed = await client.post(
            REAUTH,
            json={"password": STRONG_PASSWORD, "code": code(secret, ahead=1)},
            headers=headers,
        )
        assert confirmed.status_code == 200, confirmed.text
    return headers


async def actions(db_session: AsyncSession) -> list[str]:
    rows = await db_session.scalars(
        sa.select(PlatformAdminAudit.action).order_by(PlatformAdminAudit.created_at)
    )
    return list(rows)


async def test_password_and_code_sign_in_and_reach_me(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    secret = await make_admin(db_session)
    response = await client.post(
        LOGIN, json={"email": EMAIL, "password": STRONG_PASSWORD, "code": code(secret)}
    )
    assert response.status_code == 200, response.text
    token = response.json()["accessToken"]
    me = await client.get(ME, headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200 and me.json()["email"] == EMAIL
    assert "encryptedTotpSecret" not in me.text and "passwordHash" not in me.text
    assert await actions(db_session) == ["admin_created", "login_succeeded"]


async def test_every_wrong_detail_gets_the_same_refusal_and_is_audited(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    secret = await make_admin(db_session)
    attempts = [
        {"email": "nobody@example.com", "password": STRONG_PASSWORD, "code": code(secret)},
        {"email": EMAIL, "password": "Wrong-Password-123", "code": code(secret)},
        {"email": EMAIL, "password": STRONG_PASSWORD, "code": "000000"},
    ]
    bodies = []
    for attempt in attempts:
        response = await client.post(LOGIN, json=attempt)
        assert response.status_code == 401
        bodies.append((response.json()["code"], response.json()["message"]))
    assert len(set(bodies)) == 1
    assert (await actions(db_session)).count("login_failed") == 3


async def test_a_code_works_once(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    secret = await make_admin(db_session)
    body = {"email": EMAIL, "password": STRONG_PASSWORD, "code": code(secret)}
    assert (await client.post(LOGIN, json=body)).status_code == 200
    assert (await client.post(LOGIN, json=body)).status_code == 401


async def test_a_disabled_admin_cannot_sign_in(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    secret = await make_admin(db_session)
    await db_session.execute(sa.update(PlatformAdmin).values(is_active=False))
    response = await client.post(
        LOGIN, json={"email": EMAIL, "password": STRONG_PASSWORD, "code": code(secret)}
    )
    assert response.status_code == 401


async def test_a_tenant_owner_token_cannot_reach_the_platform(
    client: AsyncClient, panel: None
) -> None:
    owner = await register(client)
    assert (await client.get(ME, headers=auth_header(owner))).status_code == 401


async def test_a_platform_token_cannot_reach_tenant_routes(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    secret = await make_admin(db_session)
    token = (
        await client.post(
            LOGIN, json={"email": EMAIL, "password": STRONG_PASSWORD, "code": code(secret)}
        )
    ).json()["accessToken"]
    response = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


async def test_the_panel_does_not_exist_until_networks_are_configured(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.platform_admin, "allowed_cidrs", "")
    assert (
        await client.post(LOGIN, json={"email": "a", "password": "b", "code": "123456"})
    ).status_code == 404
    assert (await client.get(ME)).status_code == 404


async def test_a_caller_outside_the_allowed_networks_gets_404(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.platform_admin, "allowed_cidrs", "203.0.113.0/24")
    assert (await client.get(ME)).status_code == 404


async def test_creating_the_same_admin_twice_is_refused(db_session: AsyncSession) -> None:
    await make_admin(db_session)
    with pytest.raises(Exception, match="already exists"):
        await make_admin(db_session)
