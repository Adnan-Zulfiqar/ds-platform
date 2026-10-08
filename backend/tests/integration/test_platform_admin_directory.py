"""Track E5b — the workspace directory, suspend and reactivate (D-015)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.platform_admin import PlatformAdminAudit
from tests.integration.conftest import registration_payload
from tests.integration.test_ebay_c1_api import auth_header, register
from tests.integration.test_platform_admin_auth import make_admin, sign_in
from tests.integration.test_platform_admin_auth import panel as panel

pytestmark = pytest.mark.integration

TENANTS = "/api/v1/platform/tenants"


async def operator(client: AsyncClient, db_session: AsyncSession) -> dict[str, str]:
    """A super admin who has just re-authenticated, as suspension needs."""
    return await sign_in(client, await make_admin(db_session), reauth=True)


async def test_the_directory_lists_workspaces_with_counts_and_nothing_else(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    owner = await register(client, companyName="Acme Directory Test")
    headers = await operator(client, db_session)
    response = await client.get(TENANTS, params={"q": "Directory Test"}, headers=headers)
    assert response.status_code == 200, response.text
    [row] = response.json()["items"]
    assert row["id"] == owner["identity"]["tenant"]["id"]
    assert row["users"] == 1 and row["connectedStores"] == 0
    assert set(row) == {
        "id",
        "name",
        "slug",
        "status",
        "isActive",
        "createdAt",
        "users",
        "connectedStores",
    }


async def test_search_treats_wildcards_literally(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    await register(client, companyName="Plain Name Co")
    headers = await operator(client, db_session)
    response = await client.get(TENANTS, params={"q": "%"}, headers=headers)
    assert response.json()["items"] == []


async def test_suspending_stops_sign_in_and_is_audited_with_the_reason(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    payload = registration_payload()
    owner = (await client.post("/api/v1/auth/register", json=payload)).json()
    tenant_id = owner["identity"]["tenant"]["id"]
    headers = await operator(client, db_session)

    suspended = await client.post(
        f"{TENANTS}/{tenant_id}/suspend", json={"reason": "chargeback fraud"}, headers=headers
    )
    assert suspended.status_code == 200, suspended.text
    assert suspended.json()["isActive"] is False and suspended.json()["status"] == "suspended"
    login = await client.post(
        "/api/v1/auth/login", json={"email": payload["email"], "password": payload["password"]}
    )
    assert login.status_code == 401

    restored = await client.post(
        f"{TENANTS}/{tenant_id}/reactivate", json={"reason": "resolved"}, headers=headers
    )
    assert restored.json()["isActive"] is True
    login = await client.post(
        "/api/v1/auth/login", json={"email": payload["email"], "password": payload["password"]}
    )
    assert login.status_code == 200

    rows = (
        await db_session.scalars(
            sa.select(PlatformAdminAudit)
            .where(PlatformAdminAudit.target_tenant_id == uuid.UUID(tenant_id))
            .order_by(PlatformAdminAudit.created_at)
        )
    ).all()
    assert [(r.action, r.detail["reason"]) for r in rows] == [
        ("tenant_suspended", "chargeback fraud"),
        ("tenant_reactivated", "resolved"),
    ]
    audit = await client.get("/api/v1/platform/audit", headers=headers)
    assert {"tenant_suspended", "tenant_reactivated"} <= {a["action"] for a in audit.json()}


async def test_a_reason_is_required_and_unknown_workspaces_are_404(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    owner = await register(client)
    headers = await operator(client, db_session)
    tenant_id = owner["identity"]["tenant"]["id"]
    missing: dict[str, Any] = {}
    assert (
        await client.post(f"{TENANTS}/{tenant_id}/suspend", json=missing, headers=headers)
    ).status_code == 422
    assert (
        await client.post(
            f"{TENANTS}/{uuid.uuid4()}/suspend", json={"reason": "test"}, headers=headers
        )
    ).status_code == 404


async def test_a_tenant_owner_cannot_use_the_directory(client: AsyncClient, panel: None) -> None:
    owner = await register(client)
    assert (await client.get(TENANTS, headers=auth_header(owner))).status_code == 401
    tenant_id = owner["identity"]["tenant"]["id"]
    response = await client.post(
        f"{TENANTS}/{tenant_id}/suspend", json={"reason": "self"}, headers=auth_header(owner)
    )
    assert response.status_code == 401
