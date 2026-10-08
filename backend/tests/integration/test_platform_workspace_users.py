"""Admin Control Center phase 4 (D-019): support sessions gate every change
inside a workspace, and the user controls do what they say, audited, with
owners protected."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import set_tenant_id
from app.core.password import hash_password
from app.models.notification import Notification
from app.models.platform_admin import PlatformAdminAudit
from app.models.role import RoleName
from app.models.user import User
from app.repositories.role import RoleRepository
from tests.integration.conftest import STRONG_PASSWORD, registration_payload
from tests.integration.test_platform_admin_auth import make_admin, sign_in
from tests.integration.test_platform_admin_auth import panel as panel

pytestmark = pytest.mark.integration

P = "/api/v1/platform/workspaces"
LOGIN = "/api/v1/auth/login"


async def workspace(client: AsyncClient) -> tuple[uuid.UUID, dict[str, Any]]:
    payload = registration_payload()
    body = (await client.post("/api/v1/auth/register", json=payload)).json()
    owner = {
        "email": payload["email"],
        "password": payload["password"],
        "id": body["identity"]["user"]["id"],
    }
    return uuid.UUID(body["identity"]["tenant"]["id"]), owner


async def add_member(db_session: AsyncSession, tenant_id: uuid.UUID, role: str = "member") -> User:
    user = User(
        tenant_id=tenant_id,
        email=f"member+{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password(STRONG_PASSWORD),
        is_active=True,
        is_verified=True,
    )
    db_session.add(user)
    await db_session.flush()
    await RoleRepository(db_session).assign_by_name(user_id=user.id, name=RoleName(role))
    return user


async def login(client: AsyncClient, email: str, password: str = STRONG_PASSWORD) -> int:
    response = await client.post(LOGIN, json={"email": email, "password": password})
    return response.status_code


async def open_session(client: AsyncClient, headers: dict[str, str], tenant_id: uuid.UUID) -> None:
    response = await client.post(
        f"{P}/{tenant_id}/support-session",
        json={"reason": "ticket 4411: locked out", "minutes": 30},
        headers=headers,
    )
    assert response.status_code == 200, response.text


async def actions(db_session: AsyncSession, action: str) -> list[PlatformAdminAudit]:
    rows = await db_session.scalars(
        sa.select(PlatformAdminAudit).where(PlatformAdminAudit.action == action)
    )
    return list(rows)


async def test_a_change_needs_reauth_and_then_a_support_session(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    tenant_id, _owner = await workspace(client)
    member = await add_member(db_session, tenant_id)
    url = f"{P}/{tenant_id}/users/{member.id}/disable"
    secret = await make_admin(db_session)

    plain = await sign_in(client, secret)
    first = await client.post(url, json={"reason": "abuse report"}, headers=plain)
    assert first.status_code == 403 and first.json()["code"] == "reauth_required"


async def test_support_session_is_required_visible_and_audited(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    tenant_id, _owner = await workspace(client)
    member = await add_member(db_session, tenant_id)
    headers = await sign_in(client, await make_admin(db_session), reauth=True)
    url = f"{P}/{tenant_id}/users/{member.id}/disable"

    refused = await client.post(url, json={"reason": "abuse report"}, headers=headers)
    assert refused.status_code == 403 and refused.json()["code"] == "support_session_required"

    await open_session(client, headers, tenant_id)
    current = (await client.get(f"{P}/{tenant_id}/support-session", headers=headers)).json()
    assert current["reason"] == "ticket 4411: locked out"
    set_tenant_id(tenant_id)
    notices = (
        await db_session.scalars(sa.select(Notification).where(Notification.tenant_id == tenant_id))
    ).all()
    assert any("DropPilot support" in n.title for n in notices)
    assert (
        await client.post(url, json={"reason": "abuse report"}, headers=headers)
    ).status_code == 200

    ended = await client.post(f"{P}/{tenant_id}/support-session/end", headers=headers)
    assert ended.status_code == 204
    again = await client.post(
        f"{P}/{tenant_id}/users/{member.id}/enable", json={"reason": "resolved"}, headers=headers
    )
    assert again.status_code == 403 and again.json()["code"] == "support_session_required"
    assert [
        len(await actions(db_session, a))
        for a in ("support_session_opened", "support_session_ended")
    ] == [1, 1]


async def test_disabling_a_member_stops_sign_in_and_enabling_restores_it(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    tenant_id, _owner = await workspace(client)
    member = await add_member(db_session, tenant_id)
    assert await login(client, member.email) == 200
    headers = await sign_in(client, await make_admin(db_session), reauth=True)
    await open_session(client, headers, tenant_id)

    off = await client.post(
        f"{P}/{tenant_id}/users/{member.id}/disable",
        json={"reason": "abuse report"},
        headers=headers,
    )
    assert off.status_code == 200 and off.json()["isActive"] is False
    assert await login(client, member.email) == 401
    on = await client.post(
        f"{P}/{tenant_id}/users/{member.id}/enable", json={"reason": "cleared"}, headers=headers
    )
    assert on.json()["isActive"] is True
    assert await login(client, member.email) == 200
    [row] = await actions(db_session, "workspace_user_disabled")
    assert row.detail["reason"] == "abuse report" and row.detail["sessions_ended"] >= 1
    assert str(row.target_tenant_id) == str(tenant_id) and row.target_id == str(member.id)


async def test_the_last_active_owner_cannot_be_disabled(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    tenant_id, owner = await workspace(client)
    headers = await sign_in(client, await make_admin(db_session), reauth=True)
    await open_session(client, headers, tenant_id)
    response = await client.post(
        f"{P}/{tenant_id}/users/{owner['id']}/disable", json={"reason": "test"}, headers=headers
    )
    assert response.status_code == 409


async def test_role_changes_are_limited_and_audited(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    tenant_id, owner = await workspace(client)
    member = await add_member(db_session, tenant_id, "viewer")
    headers = await sign_in(client, await make_admin(db_session), reauth=True)
    await open_session(client, headers, tenant_id)
    base = f"{P}/{tenant_id}/users"

    changed = await client.post(
        f"{base}/{member.id}/role", json={"role": "admin", "reason": "promoted"}, headers=headers
    )
    assert changed.status_code == 200 and changed.json()["roles"] == ["admin"]
    [row] = await actions(db_session, "workspace_user_role_changed")
    assert row.detail["before"] == {"roles": ["viewer"]} and row.detail["after"] == {
        "roles": ["admin"]
    }

    owner_change = await client.post(
        f"{base}/{owner['id']}/role", json={"role": "member", "reason": "takeover"}, headers=headers
    )
    assert owner_change.status_code == 409
    to_owner = await client.post(
        f"{base}/{member.id}/role", json={"role": "owner", "reason": "takeover"}, headers=headers
    )
    assert to_owner.status_code == 422


async def test_required_password_reset_makes_the_old_password_fail_like_a_wrong_one(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    tenant_id, _owner = await workspace(client)
    member = await add_member(db_session, tenant_id)
    wrong = await client.post(LOGIN, json={"email": member.email, "password": "Wrong-Password-123"})
    headers = await sign_in(client, await make_admin(db_session), reauth=True)
    await open_session(client, headers, tenant_id)
    response = await client.post(
        f"{P}/{tenant_id}/users/{member.id}/require-password-reset",
        json={"reason": "credential stuffing suspected"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    old = await client.post(LOGIN, json={"email": member.email, "password": STRONG_PASSWORD})
    assert old.status_code == wrong.status_code == 401
    assert old.json()["message"] == wrong.json()["message"]


async def test_another_workspaces_user_is_not_found(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    mine, _ = await workspace(client)
    theirs, _ = await workspace(client)
    stranger = await add_member(db_session, theirs)
    headers = await sign_in(client, await make_admin(db_session), reauth=True)
    await open_session(client, headers, mine)
    response = await client.post(
        f"{P}/{mine}/users/{stranger.id}/disable", json={"reason": "probe"}, headers=headers
    )
    assert response.status_code == 404


@pytest.mark.parametrize(("role", "expected"), [("finance", 403), ("auditor", 403)])
async def test_roles_without_support_sessions_cannot_open_one(
    client: AsyncClient, db_session: AsyncSession, panel: None, role: str, expected: int
) -> None:
    tenant_id, _ = await workspace(client)
    headers = await sign_in(client, await make_admin(db_session, role=role), reauth=True)
    response = await client.post(
        f"{P}/{tenant_id}/support-session", json={"reason": "look around"}, headers=headers
    )
    assert response.status_code == expected


async def test_operations_can_open_a_session_but_not_manage_users(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    tenant_id, _ = await workspace(client)
    member = await add_member(db_session, tenant_id)
    headers = await sign_in(client, await make_admin(db_session, role="operations"), reauth=True)
    await open_session(client, headers, tenant_id)
    response = await client.post(
        f"{P}/{tenant_id}/users/{member.id}/disable", json={"reason": "test"}, headers=headers
    )
    assert response.status_code == 403 and response.json()["code"] == "permission_denied"
