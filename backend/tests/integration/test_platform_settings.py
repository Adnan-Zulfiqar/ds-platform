"""Admin Control Center phase 10 (D-019): maintenance mode, announcements and
broadcasts, super-admin only, audited."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import set_tenant_id
from app.models.notification import Notification
from app.models.platform_admin import PlatformAdminAudit
from app.tasks import notifications as notification_tasks
from tests.integration.conftest import registration_payload
from tests.integration.test_ebay_c1_api import auth_header
from tests.integration.test_platform_admin_auth import make_admin, sign_in
from tests.integration.test_platform_admin_auth import panel as panel

pytestmark = pytest.mark.integration

P = "/api/v1/platform"
STATUS = "/api/v1/system/status"


async def merchant(client: AsyncClient) -> tuple[dict[str, object], dict[str, str]]:
    payload = registration_payload()
    body = (await client.post("/api/v1/auth/register", json=payload)).json()
    return payload, auth_header(body)


async def audit(db_session: AsyncSession, action: str) -> list[PlatformAdminAudit]:
    return list(
        await db_session.scalars(
            sa.select(PlatformAdminAudit).where(PlatformAdminAudit.action == action)
        )
    )


async def test_maintenance_makes_the_merchant_api_read_only_and_keeps_sign_in(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    payload, headers = await merchant(client)
    operator = await sign_in(client, await make_admin(db_session), reauth=True)

    on = await client.post(
        f"{P}/settings/maintenance",
        json={"enabled": True, "message": "Database upgrade until 14:00 UTC", "reason": "upgrade"},
        headers=operator,
    )
    assert on.status_code == 200, on.text
    assert on.json()["maintenance"]["enabled"] is True

    write = await client.post("/api/v1/notifications/read-all", headers=headers)
    assert write.status_code == 503 and write.json()["code"] == "maintenance"
    assert (await client.get("/api/v1/notifications", headers=headers)).status_code == 200
    login = await client.post(
        "/api/v1/auth/login", json={"email": payload["email"], "password": payload["password"]}
    )
    assert login.status_code == 200
    status = (await client.get(STATUS)).json()
    assert status["maintenance"] is True
    assert status["maintenanceMessage"] == "Database upgrade until 14:00 UTC"

    off = await client.post(
        f"{P}/settings/maintenance", json={"enabled": False, "reason": "done"}, headers=operator
    )
    assert off.json()["maintenance"]["enabled"] is False
    assert (await client.post("/api/v1/notifications/read-all", headers=headers)).status_code == 200
    assert [
        len(await audit(db_session, a)) for a in ("maintenance_enabled", "maintenance_disabled")
    ] == [1, 1]


async def test_only_a_super_admin_changes_platform_settings(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    admin = await sign_in(client, await make_admin(db_session, role="admin"), reauth=True)
    for path, body in (
        ("/settings/maintenance", {"enabled": True, "reason": "try"}),
        ("/announcements", {"title": "Hello", "reason": "try"}),
        ("/broadcasts", {"title": "Hello", "body": "Hi", "reason": "try"}),
    ):
        response = await client.post(f"{P}{path}", json=body, headers=admin)
        assert response.status_code == 403, (path, response.text)
    assert (await client.get(f"{P}/settings", headers=admin)).status_code == 200


async def test_an_announcement_is_public_while_live_and_gone_when_ended(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    operator = await sign_in(client, await make_admin(db_session), reauth=True)
    created = await client.post(
        f"{P}/announcements",
        json={
            "title": "Scheduled maintenance Sunday",
            "body": "Expect 10 minutes of read-only mode.",
            "level": "warning",
            "reason": "planned upgrade",
        },
        headers=operator,
    )
    assert created.status_code == 200, created.text
    [announcement] = created.json()["announcements"]

    live = (await client.get(STATUS)).json()["announcements"]
    assert [(a["title"], a["level"]) for a in live] == [("Scheduled maintenance Sunday", "warning")]

    ended = await client.post(
        f"{P}/announcements/{announcement['id']}/end", json={"reason": "done"}, headers=operator
    )
    assert ended.status_code == 200
    assert (await client.get(STATUS)).json()["announcements"] == []
    assert len(await audit(db_session, "announcement_created")) == 1
    assert len(await audit(db_session, "announcement_ended")) == 1


async def test_a_broadcast_is_audited_and_its_task_reaches_every_workspace_once(
    client: AsyncClient,
    db_session: AsyncSession,
    panel: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _ = await merchant(client)
    operator = await sign_in(client, await make_admin(db_session), reauth=True)
    response = await client.post(
        f"{P}/broadcasts",
        json={"title": "New feature", "body": "Bulk AI is live.", "reason": "release"},
        headers=operator,
    )
    assert response.status_code == 202, response.text
    broadcast_id = response.json()["broadcastId"]
    [row] = await audit(db_session, "broadcast_sent")
    assert row.target_id == broadcast_id

    @asynccontextmanager
    async def shared() -> AsyncIterator[AsyncSession]:
        yield db_session
        await db_session.flush()

    monkeypatch.setattr(notification_tasks, "transaction", shared)
    tenant_ids = list(
        await db_session.scalars(
            sa.text("SELECT id FROM tenants WHERE status IN ('active','trial')")
        )
    )
    assert tenant_ids
    first = [
        await notification_tasks._broadcast_one(t, broadcast_id, "New feature", "Bulk AI is live.")
        for t in tenant_ids
    ]
    again = [
        await notification_tasks._broadcast_one(t, broadcast_id, "New feature", "Bulk AI is live.")
        for t in tenant_ids
    ]
    assert all(first) and not any(again)
    set_tenant_id(uuid.UUID(str(tenant_ids[0])))
    copies = await db_session.scalar(
        sa.select(sa.func.count())
        .select_from(Notification)
        .where(Notification.payload["broadcast_id"].astext == broadcast_id)
    )
    assert copies == len(tenant_ids)
