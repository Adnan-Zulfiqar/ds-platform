"""Admin Control Center phase 2 (D-019): the dashboard counts real rows, and
an operator enters one workspace through its tenant-scoped queries, audited."""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import get_tenant_id
from app.models.platform_admin import PlatformAdminAudit
from tests.integration.test_ebay_c1_api import register
from tests.integration.test_platform_admin_auth import make_admin, sign_in
from tests.integration.test_platform_admin_auth import panel as panel

pytestmark = pytest.mark.integration

P = "/api/v1/platform"


async def test_the_dashboard_counts_real_workspaces_and_reports_system_health(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    headers = await sign_in(client, await make_admin(db_session))
    before = (await client.get(f"{P}/dashboard", headers=headers)).json()
    await register(client)
    await register(client)
    after = await client.get(f"{P}/dashboard", headers=headers)
    assert after.status_code == 200, after.text
    body = after.json()
    assert sum(body["tenantsByStatus"].values()) == sum(before["tenantsByStatus"].values()) + 2
    assert body["tenantsNew7d"] == before["tenantsNew7d"] + 2
    assert body["usersActive"] == before["usersActive"] + 2
    assert body["system"]["database"] is True
    assert body["system"]["migrationRevision"]
    assert len(body["signups30d"]) == 31 and len(body["orders14d"]) == 14
    assert body["signups30d"][-1]["count"] >= 2
    assert set(body["failed24h"]) >= {"orderSyncs", "inventorySyncs", "productImports"}


@pytest.mark.parametrize("role", ["support", "finance", "operations", "auditor"])
async def test_every_role_reads_the_dashboard(
    client: AsyncClient, db_session: AsyncSession, panel: None, role: str
) -> None:
    headers = await sign_in(client, await make_admin(db_session, role=role))
    assert (await client.get(f"{P}/dashboard", headers=headers)).status_code == 200


async def test_a_workspace_overview_counts_only_that_workspace_and_is_audited(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    mine = await register(client, companyName="Overview Target")
    await register(client, companyName="Someone Else")
    tenant_id = mine["identity"]["tenant"]["id"]
    headers = await sign_in(client, await make_admin(db_session))

    response = await client.get(f"{P}/workspaces/{tenant_id}", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == tenant_id and body["name"] == "Overview Target"
    assert body["users"] == 1 and body["activeUsers"] == 1
    for secret in ("stripeCustomerId", "encrypted", "passwordHash", "tokenHash"):
        assert secret not in response.text

    rows = (
        await db_session.scalars(
            sa.select(PlatformAdminAudit).where(PlatformAdminAudit.action == "workspace_viewed")
        )
    ).all()
    assert [(str(r.target_tenant_id), r.detail["route"]) for r in rows] == [
        (tenant_id, "/api/v1/platform/workspaces/{tenant_id}")
    ]
    # The workspace context ends with the request.
    assert get_tenant_id() is None or str(get_tenant_id()) != tenant_id


async def test_finance_cannot_enter_a_workspace(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    owner = await register(client)
    headers = await sign_in(client, await make_admin(db_session, role="finance"))
    response = await client.get(
        f"{P}/workspaces/{owner['identity']['tenant']['id']}", headers=headers
    )
    assert response.status_code == 403


async def test_an_unknown_workspace_is_404_and_not_audited_as_a_view(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    headers = await sign_in(client, await make_admin(db_session))
    response = await client.get(f"{P}/workspaces/{uuid.uuid4()}", headers=headers)
    assert response.status_code == 404
    viewed = await db_session.scalar(
        sa.select(sa.func.count())
        .select_from(PlatformAdminAudit)
        .where(PlatformAdminAudit.action == "workspace_viewed")
    )
    assert viewed == 0
