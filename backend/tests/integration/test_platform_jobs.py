"""Admin Control Center phase 7 (D-019): failed and stuck jobs across
workspaces, and the actions that clear them, audited."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import OrderSyncRun, SyncRunStatus, SyncTrigger
from app.models.platform_admin import PlatformAdminAudit
from tests.integration.test_platform_admin_auth import make_admin, sign_in
from tests.integration.test_platform_admin_auth import panel as panel
from tests.integration.test_platform_workspace_users import open_session, workspace

pytestmark = pytest.mark.integration

P = "/api/v1/platform"


async def run(
    db_session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    status: SyncRunStatus,
    started_minutes_ago: int = 5,
    error: str | None = None,
) -> OrderSyncRun:
    row = OrderSyncRun(
        tenant_id=tenant_id,
        trigger=SyncTrigger.SCHEDULED,
        status=status,
        started_at=datetime.now(UTC) - timedelta(minutes=started_minutes_ago),
        error_code="supplier_timeout" if error else None,
        error_message=error,
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def test_failed_jobs_are_listed_across_workspaces_with_their_workspace(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    a, _ = await workspace(client)
    b, _ = await workspace(client)
    ra = await run(db_session, a, status=SyncRunStatus.FAILED, error="timed out")
    rb = await run(db_session, b, status=SyncRunStatus.FAILED, error="refused")
    await run(db_session, a, status=SyncRunStatus.SUCCEEDED)
    headers = await sign_in(client, await make_admin(db_session, role="auditor"))

    listed = await client.get(f"{P}/jobs?kind=order_sync&state=failed", headers=headers)
    assert listed.status_code == 200, listed.text
    ids = {row["id"]: row for row in listed.json()["items"]}
    assert {str(ra.id), str(rb.id)} <= set(ids)
    assert ids[str(ra.id)]["tenantId"] == str(a)
    assert ids[str(ra.id)]["error"] == "supplier_timeout: timed out"
    assert set(ids[str(ra.id)]) == {
        "kind",
        "id",
        "tenantId",
        "tenantName",
        "status",
        "error",
        "startedAt",
        "finishedAt",
        "createdAt",
    }

    only_b = await client.get(
        f"{P}/jobs?kind=order_sync&state=failed&tenantId={b}", headers=headers
    )
    assert [row["id"] for row in only_b.json()["items"]] == [str(rb.id)]

    summary = (await client.get(f"{P}/jobs/summary", headers=headers)).json()
    assert summary["order_sync"]["failed"] >= 2
    assert set(summary) >= {"order_sync", "inventory_sync", "pipeline_run", "supplier_order"}


async def test_a_stuck_sync_is_detected_and_can_be_closed_but_a_live_one_cannot(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    tenant_id, _ = await workspace(client)
    stuck = await run(db_session, tenant_id, status=SyncRunStatus.RUNNING, started_minutes_ago=120)
    live = await run(db_session, tenant_id, status=SyncRunStatus.RUNNING, started_minutes_ago=2)
    headers = await sign_in(client, await make_admin(db_session, role="operations"), reauth=True)

    listed = await client.get(f"{P}/jobs?kind=order_sync&state=stuck", headers=headers)
    ids = [row["id"] for row in listed.json()["items"]]
    assert str(stuck.id) in ids and str(live.id) not in ids

    await open_session(client, headers, tenant_id)
    base = f"{P}/workspaces/{tenant_id}/jobs/order_sync"
    closed = await client.post(
        f"{base}/{stuck.id}/close", json={"reason": "blocked syncs"}, headers=headers
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["outcome"] == "failed"
    await db_session.refresh(stuck)
    assert stuck.error_code == "closed_by_support" and stuck.finished_at is not None

    refused = await client.post(
        f"{base}/{live.id}/close", json={"reason": "too soon"}, headers=headers
    )
    assert refused.status_code == 409
    rows = await db_session.scalars(
        sa.select(PlatformAdminAudit).where(
            PlatformAdminAudit.action == "workspace_sync_run_closed"
        )
    )
    assert [r.target_id for r in rows] == [str(stuck.id)]


async def test_jobs_follow_the_role(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    tenant_id, _ = await workspace(client)
    stuck = await run(db_session, tenant_id, status=SyncRunStatus.RUNNING, started_minutes_ago=120)
    finance = await sign_in(client, await make_admin(db_session, role="finance"))
    assert (await client.get(f"{P}/jobs", headers=finance)).status_code == 403

    support = await sign_in(
        client,
        await make_admin(db_session, role="support", email="support@droppilot.example"),
        email="support@droppilot.example",
        reauth=True,
    )
    assert (await client.get(f"{P}/jobs", headers=support)).status_code == 200
    await open_session(client, support, tenant_id)
    response = await client.post(
        f"{P}/workspaces/{tenant_id}/jobs/order_sync/{stuck.id}/close",
        json={"reason": "try"},
        headers=support,
    )
    assert response.status_code == 403 and response.json()["code"] == "permission_denied"


async def test_unknown_jobs_are_not_found(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    tenant_id, _ = await workspace(client)
    headers = await sign_in(client, await make_admin(db_session), reauth=True)
    await open_session(client, headers, tenant_id)
    base = f"{P}/workspaces/{tenant_id}/jobs"
    for path in (
        f"pipeline_run/{uuid.uuid4()}/cancel",
        f"rule_application/{uuid.uuid4()}/cancel",
        f"automation_run/{uuid.uuid4()}/retry",
        f"order_sync/{uuid.uuid4()}/close",
    ):
        response = await client.post(f"{base}/{path}", json={"reason": "probe"}, headers=headers)
        assert response.status_code == 404, (path, response.text)
