"""Admin Control Center phase 5 (D-019): store and integration controls,
through support sessions, audited, reusing the merchant's own services."""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification
from app.models.platform_admin import PlatformAdminAudit
from app.models.store import Store, StorePlatform, StoreStatus
from tests.integration.test_platform_admin_auth import make_admin, sign_in
from tests.integration.test_platform_admin_auth import panel as panel
from tests.integration.test_platform_workspace_users import open_session, workspace

pytestmark = pytest.mark.integration

P = "/api/v1/platform/workspaces"


async def add_store(db_session: AsyncSession, tenant_id: uuid.UUID) -> Store:
    store = Store(
        tenant_id=tenant_id,
        name="Acme Shopify",
        slug=f"acme-{uuid.uuid4().hex[:8]}",
        platform=StorePlatform.SHOPIFY,
        status=StoreStatus.CONNECTED,
        currency="USD",
    )
    db_session.add(store)
    await db_session.flush()
    return store


async def audit(db_session: AsyncSession, action: str) -> list[PlatformAdminAudit]:
    return list(
        await db_session.scalars(
            sa.select(PlatformAdminAudit).where(PlatformAdminAudit.action == action)
        )
    )


async def test_pausing_and_resuming_a_store_is_visible_and_audited(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    tenant_id, _ = await workspace(client)
    store = await add_store(db_session, tenant_id)
    headers = await sign_in(client, await make_admin(db_session, role="operations"), reauth=True)
    await open_session(client, headers, tenant_id)

    paused = await client.post(
        f"{P}/{tenant_id}/stores/{store.id}/pause",
        json={"reason": "duplicate price pushes"},
        headers=headers,
    )
    assert paused.status_code == 200, paused.text
    assert (
        paused.json()["syncPausedAt"]
        and paused.json()["syncPausedReason"] == "duplicate price pushes"
    )
    listed = (await client.get(f"{P}/{tenant_id}/stores", headers=headers)).json()["items"]
    assert listed[0]["syncPausedAt"] is not None

    resumed = await client.post(
        f"{P}/{tenant_id}/stores/{store.id}/resume", json={"reason": "fixed"}, headers=headers
    )
    assert resumed.json()["syncPausedAt"] is None

    titles = [
        n.title
        for n in await db_session.scalars(
            sa.select(Notification).where(Notification.tenant_id == tenant_id)
        )
    ]
    assert any("paused updates to Acme Shopify" in t for t in titles)
    assert any("resumed updates to Acme Shopify" in t for t in titles)
    [row] = await audit(db_session, "workspace_store_paused")
    assert row.target_type == "store" and row.target_id == str(store.id)
    assert row.detail["reason"] == "duplicate price pushes"
    assert len(await audit(db_session, "workspace_store_resumed")) == 1


async def test_store_changes_need_the_role_and_a_support_session(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    tenant_id, _ = await workspace(client)
    store = await add_store(db_session, tenant_id)
    url = f"{P}/{tenant_id}/stores/{store.id}/pause"

    support = await sign_in(client, await make_admin(db_session, role="support"), reauth=True)
    await open_session(client, support, tenant_id)
    refused = await client.post(url, json={"reason": "try"}, headers=support)
    assert refused.status_code == 403 and refused.json()["code"] == "permission_denied"


async def test_another_workspaces_store_is_not_found(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    mine, _ = await workspace(client)
    theirs, _ = await workspace(client)
    stranger = await add_store(db_session, theirs)
    headers = await sign_in(client, await make_admin(db_session), reauth=True)
    await open_session(client, headers, mine)
    response = await client.post(
        f"{P}/{mine}/stores/{stranger.id}/pause", json={"reason": "probe"}, headers=headers
    )
    assert response.status_code == 404


async def test_a_failed_sync_still_leaves_a_failure_in_the_audit(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    """No AliExpress connection: the sync cannot start. The request rolls
    back, but the attempt is recorded as a failure in its own transaction."""
    tenant_id, _ = await workspace(client)
    headers = await sign_in(client, await make_admin(db_session), reauth=True)
    await open_session(client, headers, tenant_id)
    response = await client.post(
        f"{P}/{tenant_id}/sync/orders", json={"reason": "merchant asked"}, headers=headers
    )
    assert response.status_code >= 400
    rows = await audit(db_session, "workspace_order_sync_started")
    assert [r.outcome for r in rows] == ["failure"]
    assert rows[0].detail["reason"] == "merchant asked"
