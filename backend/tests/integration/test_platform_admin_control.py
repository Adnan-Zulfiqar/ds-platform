"""Admin Control Center foundation (D-018): roles, sessions, re-authentication
and the richer audit trail, through the API on real Postgres."""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError
from app.models.platform_admin import PlatformAdmin, PlatformAdminAudit, PlatformAdminSession
from app.services.platform_admin import AuditContext, PlatformAdminService, PlatformPrincipal
from tests.integration.conftest import STRONG_PASSWORD
from tests.integration.test_ebay_c1_api import register
from tests.integration.test_platform_admin_auth import (
    EMAIL,
    ME,
    REAUTH,
    code,
    make_admin,
    sign_in,
)
from tests.integration.test_platform_admin_auth import panel as panel

pytestmark = pytest.mark.integration

P = "/api/v1/platform"
SECOND = "second@droppilot.example"


async def audit_rows(db_session: AsyncSession, action: str) -> list[PlatformAdminAudit]:
    rows = await db_session.scalars(
        sa.select(PlatformAdminAudit)
        .where(PlatformAdminAudit.action == action)
        .order_by(PlatformAdminAudit.created_at)
    )
    return list(rows)


async def admin_id(db_session: AsyncSession, email: str) -> uuid.UUID:
    return (
        await db_session.execute(sa.select(PlatformAdmin.id).where(PlatformAdmin.email == email))
    ).scalar_one()


# --- me, sessions, logout ------------------------------------------------------


async def test_me_reports_role_permissions_and_the_current_session(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    headers = await sign_in(client, await make_admin(db_session, role="auditor"))
    body = (await client.get(ME, headers=headers)).json()
    assert body["role"] == "auditor"
    assert body["permissions"] == ["audit.read", "operators.read", "tenants.read"]
    assert body["session"]["current"] is True
    assert body["reauthValidUntil"] is None


async def test_logout_ends_the_session_while_the_token_is_still_unexpired(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    headers = await sign_in(client, await make_admin(db_session))
    assert (await client.post(f"{P}/auth/logout", headers=headers)).status_code == 204
    assert (await client.get(ME, headers=headers)).status_code == 401
    assert len(await audit_rows(db_session, "logout")) == 1


async def test_an_operator_can_end_their_other_session_but_not_someone_elses(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    secret = await make_admin(db_session)
    laptop = await sign_in(client, secret)
    phone_response = await client.post(
        f"{P}/auth/login",
        json={"email": EMAIL, "password": STRONG_PASSWORD, "code": code(secret, ahead=1)},
    )
    phone = {"Authorization": f"Bearer {phone_response.json()['accessToken']}"}

    sessions = (await client.get(f"{P}/auth/sessions", headers=laptop)).json()
    assert len(sessions) == 2
    [other] = [s for s in sessions if not s["current"]]
    revoked = await client.post(f"{P}/auth/sessions/{other['id']}/revoke", headers=laptop)
    assert revoked.status_code == 204
    assert (await client.get(ME, headers=phone)).status_code == 401
    assert (await client.get(ME, headers=laptop)).status_code == 200

    stranger = await sign_in(client, await make_admin(db_session, email=SECOND), email=SECOND)
    mine = (await client.get(ME, headers=laptop)).json()["session"]["id"]
    response = await client.post(f"{P}/auth/sessions/{mine}/revoke", headers=stranger)
    assert response.status_code == 404
    assert (await client.get(ME, headers=laptop)).status_code == 200


async def test_a_deactivated_operator_loses_access_immediately(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    headers = await sign_in(client, await make_admin(db_session))
    await db_session.execute(sa.update(PlatformAdmin).values(is_active=False))
    assert (await client.get(ME, headers=headers)).status_code == 401


# --- permissions ---------------------------------------------------------------


@pytest.mark.parametrize("role", ["support", "finance"])
async def test_read_only_roles_cannot_see_the_audit_or_suspend_and_are_audited(
    client: AsyncClient, db_session: AsyncSession, panel: None, role: str
) -> None:
    owner = await register(client)
    tenant_id = owner["identity"]["tenant"]["id"]
    headers = await sign_in(client, await make_admin(db_session, role=role), reauth=True)

    assert (await client.get(f"{P}/tenants", headers=headers)).status_code == 200
    audit = await client.get(f"{P}/audit", headers=headers)
    assert audit.status_code == 403 and audit.json()["code"] == "permission_denied"
    suspend = await client.post(
        f"{P}/tenants/{tenant_id}/suspend", json={"reason": "try it"}, headers=headers
    )
    assert suspend.status_code == 403
    denied = await audit_rows(db_session, "permission_denied")
    assert [r.detail["permission"] for r in denied] == ["audit.read", "tenants.suspend"]
    assert {r.outcome for r in denied} == {"failure"}
    assert {r.actor_role for r in denied} == {role}


async def test_an_admin_cannot_manage_operators(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    await make_admin(db_session)
    target = await admin_id(db_session, EMAIL)
    headers = await sign_in(
        client,
        await make_admin(db_session, email=SECOND, role="admin"),
        email=SECOND,
        reauth=True,
    )
    assert (await client.get(f"{P}/operators", headers=headers)).status_code == 200
    response = await client.post(
        f"{P}/operators/{target}/role",
        json={"role": "support", "reason": "takeover attempt"},
        headers=headers,
    )
    assert response.status_code == 403


# --- re-authentication -----------------------------------------------------------


async def test_suspension_needs_a_recent_reauthentication(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    owner = await register(client)
    tenant_id = owner["identity"]["tenant"]["id"]
    secret = await make_admin(db_session)
    headers = await sign_in(client, secret)
    url = f"{P}/tenants/{tenant_id}/suspend"

    first = await client.post(url, json={"reason": "abuse"}, headers=headers)
    assert first.status_code == 403 and first.json()["code"] == "reauth_required"

    confirmed = await client.post(
        REAUTH, json={"password": STRONG_PASSWORD, "code": code(secret, ahead=1)}, headers=headers
    )
    assert confirmed.status_code == 200, confirmed.text
    assert (await client.get(ME, headers=headers)).json()["reauthValidUntil"] is not None
    assert (await client.post(url, json={"reason": "abuse"}, headers=headers)).status_code == 200


async def test_reauthentication_refuses_a_wrong_password_and_a_replayed_code(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    secret = await make_admin(db_session)
    headers = await sign_in(client, secret)
    wrong = await client.post(
        REAUTH,
        json={"password": "Wrong-Password-123", "code": code(secret, ahead=1)},
        headers=headers,
    )
    assert wrong.status_code == 401
    # The sign-in code was already spent; it cannot confirm anything again.
    replay = await client.post(
        REAUTH, json={"password": STRONG_PASSWORD, "code": code(secret)}, headers=headers
    )
    assert replay.status_code == 401
    assert [r.detail["reason"] for r in await audit_rows(db_session, "reauth_failed")] == [
        "credentials",
        "totp",
    ]


# --- operator management ---------------------------------------------------------


async def test_a_role_change_ends_the_targets_sessions_and_is_audited(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    super_headers = await sign_in(client, await make_admin(db_session), reauth=True)
    second_secret = await make_admin(db_session, email=SECOND, role="admin")
    second_headers = await sign_in(client, second_secret, email=SECOND)
    target = await admin_id(db_session, SECOND)

    response = await client.post(
        f"{P}/operators/{target}/role",
        json={"role": "auditor", "reason": "moved to compliance"},
        headers=super_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["role"] == "auditor" and response.json()["openSessions"] == 0
    assert (await client.get(ME, headers=second_headers)).status_code == 401

    [row] = await audit_rows(db_session, "operator_role_changed")
    assert row.detail["before"] == {"role": "admin"}
    assert row.detail["after"] == {"role": "auditor"}
    assert row.target_type == "operator" and row.target_id == str(target)
    assert row.actor_role == "super_admin" and row.user_agent and row.client_ip


async def test_an_operator_cannot_change_their_own_role_or_deactivate_themselves(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    headers = await sign_in(client, await make_admin(db_session), reauth=True)
    me = await admin_id(db_session, EMAIL)
    role = await client.post(
        f"{P}/operators/{me}/role", json={"role": "support", "reason": "self"}, headers=headers
    )
    assert role.status_code == 403
    off = await client.post(
        f"{P}/operators/{me}/deactivate", json={"reason": "self"}, headers=headers
    )
    assert off.status_code == 403


async def test_deactivating_an_operator_ends_their_sessions(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    headers = await sign_in(client, await make_admin(db_session), reauth=True)
    second_headers = await sign_in(
        client, await make_admin(db_session, email=SECOND, role="support"), email=SECOND
    )
    target = await admin_id(db_session, SECOND)
    off = await client.post(
        f"{P}/operators/{target}/deactivate", json={"reason": "left the team"}, headers=headers
    )
    assert off.status_code == 200 and off.json()["isActive"] is False
    assert (await client.get(ME, headers=second_headers)).status_code == 401
    on = await client.post(
        f"{P}/operators/{target}/reactivate", json={"reason": "returned"}, headers=headers
    )
    assert on.json()["isActive"] is True
    # Reactivation does not resurrect the ended sessions.
    assert (await client.get(ME, headers=second_headers)).status_code == 401


async def test_the_last_active_super_admin_cannot_be_removed(db_session: AsyncSession) -> None:
    """Unreachable through the API in a quiet system (only a super admin
    manages operators, and nobody changes themselves), so this checks the
    guard directly: an actor whose own account was deactivated meanwhile
    tries to demote the only remaining super admin."""
    await make_admin(db_session)
    await make_admin(db_session, email=SECOND)
    service = PlatformAdminService(db_session)
    actor = await db_session.get(PlatformAdmin, await admin_id(db_session, SECOND))
    assert actor is not None
    actor.is_active = False
    await db_session.flush()
    principal = PlatformPrincipal(admin=actor, session=PlatformAdminSession(admin_id=actor.id))
    target = await admin_id(db_session, EMAIL)
    with pytest.raises(ConflictError):
        await service.set_operator_role(
            principal, target, role="support", reason="test", ctx=AuditContext()
        )
    with pytest.raises(ConflictError):
        await service.set_operator_active(
            principal, target, active=False, reason="test", ctx=AuditContext()
        )


async def test_operator_responses_never_carry_secrets(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    headers = await sign_in(client, await make_admin(db_session))
    body = (await client.get(f"{P}/operators", headers=headers)).text
    for field in ("passwordHash", "encryptedTotpSecret", "totpLastStep", "accessToken"):
        assert field not in body


async def test_the_audit_view_carries_request_context(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    headers = await sign_in(client, await make_admin(db_session))
    rows = (await client.get(f"{P}/audit", headers=headers)).json()
    login = next(r for r in rows if r["action"] == "login_succeeded")
    assert login["outcome"] == "success"
    assert login["actorRole"] == "super_admin"
    assert login["targetType"] == "session"
    assert login["userAgent"]
