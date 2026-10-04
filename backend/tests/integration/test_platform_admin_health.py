"""Track E5c — a workspace's failure counts, never its rows (D-015)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification, NotificationKind
from tests.integration.test_ebay_c1_api import auth_header, register
from tests.integration.test_platform_admin_auth import panel as panel
from tests.integration.test_platform_admin_directory import operator

pytestmark = pytest.mark.integration


async def failed_email(db_session: AsyncSession, tenant_id: str, *, hours_ago: float = 1) -> None:
    note = Notification(
        tenant_id=uuid.UUID(tenant_id),
        kind=NotificationKind.SYNC_FAILED,
        title="x",
        body="",
        is_read=False,
        email_status="failed",
    )
    db_session.add(note)
    await db_session.flush()
    await db_session.execute(
        sa.update(Notification)
        .where(Notification.id == note.id)
        .values(created_at=datetime.now(UTC) - timedelta(hours=hours_ago))
    )


async def test_counts_cover_the_window_and_only_this_workspace(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    first = await register(client)
    second = await register(client)
    first_id = first["identity"]["tenant"]["id"]
    await failed_email(db_session, first_id)
    await failed_email(db_session, first_id, hours_ago=30)  # outside the window
    await failed_email(db_session, second["identity"]["tenant"]["id"])  # another workspace
    headers = await operator(client, db_session)

    response = await client.get(f"/api/v1/platform/tenants/{first_id}/health", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "tenantId": first_id,
        "windowHours": 24,
        "failedOrderSyncs": 0,
        "failedInventorySyncs": 0,
        "listingsInError": 0,
        "failedNotificationEmails": 1,
    }


async def test_unknown_workspaces_are_404_and_tenants_cannot_ask(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    owner = await register(client)
    headers = await operator(client, db_session)
    unknown = await client.get(f"/api/v1/platform/tenants/{uuid.uuid4()}/health", headers=headers)
    assert unknown.status_code == 404
    own = owner["identity"]["tenant"]["id"]
    refused = await client.get(f"/api/v1/platform/tenants/{own}/health", headers=auth_header(owner))
    assert refused.status_code == 401
