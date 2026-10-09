"""Admin Control Center phase 9 (D-019): the audit trail cannot be changed in
the database, and the audit and security centre searches and exports it."""

from __future__ import annotations

import csv
import io
import uuid

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.platform_admin import PlatformAdmin, PlatformAdminAudit
from tests.integration.conftest import STRONG_PASSWORD
from tests.integration.test_platform_admin_auth import (
    EMAIL,
    LOGIN,
    REAUTH,
    code,
    make_admin,
    sign_in,
)
from tests.integration.test_platform_admin_auth import panel as panel

pytestmark = pytest.mark.integration

P = "/api/v1/platform"


async def a_row(db_session: AsyncSession, **values: object) -> PlatformAdminAudit:
    row = PlatformAdminAudit(action=values.pop("action", "test_action"), detail={}, **values)
    db_session.add(row)
    await db_session.flush()
    return row


async def refused(db_session: AsyncSession, statement: sa.Executable) -> bool:
    try:
        async with db_session.begin_nested():
            await db_session.execute(statement)
    except DBAPIError as exc:
        return "append-only" in str(exc)
    return False


async def test_the_database_refuses_to_change_or_remove_audit_rows(
    db_session: AsyncSession,
) -> None:
    row = await a_row(db_session, action="tenant_suspended")
    table = PlatformAdminAudit.__table__
    assert await refused(
        db_session, sa.update(table).where(table.c.id == row.id).values(action="nothing_happened")
    )
    assert await refused(db_session, sa.delete(table).where(table.c.id == row.id))
    assert await refused(db_session, sa.text("TRUNCATE platform_admin_audit"))
    still = await db_session.scalar(sa.select(table.c.action).where(table.c.id == row.id))
    assert still == "tenant_suspended"


async def test_removing_an_operator_still_clears_their_id_on_audit_rows(
    db_session: AsyncSession,
) -> None:
    """The one allowed change: the foreign key's ON DELETE SET NULL."""
    await make_admin(db_session, email="gone@droppilot.example")
    admin = await db_session.scalar(
        sa.select(PlatformAdmin).where(PlatformAdmin.email == "gone@droppilot.example")
    )
    assert admin is not None
    row = await a_row(db_session, action="login_succeeded", admin_id=admin.id)
    await db_session.execute(sa.delete(PlatformAdmin).where(PlatformAdmin.id == admin.id))
    table = PlatformAdminAudit.__table__
    cleared = await db_session.execute(
        sa.select(table.c.admin_id, table.c.action).where(table.c.id == row.id)
    )
    assert cleared.one() == (None, "login_succeeded")


async def test_the_audit_search_filters_and_pages(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    headers = await sign_in(client, await make_admin(db_session, role="auditor"))
    tenant = uuid.uuid4()
    for _ in range(3):
        await a_row(db_session, action="workspace_viewed", outcome="success")
    await a_row(db_session, action="workspace_user_disabled", outcome="success")
    await a_row(db_session, action="permission_denied", outcome="failure")

    prefix = await client.get(f"{P}/audit/search?action=workspace_&size=2", headers=headers)
    assert prefix.status_code == 200, prefix.text
    body = prefix.json()
    assert body["meta"]["totalItems"] == 4 and len(body["items"]) == 2
    assert all(i["action"].startswith("workspace_") for i in body["items"])

    failures = (await client.get(f"{P}/audit/search?outcome=failure", headers=headers)).json()
    assert {i["action"] for i in failures["items"]} == {"permission_denied"}
    exact = (await client.get(f"{P}/audit/search?action=workspace_viewed", headers=headers)).json()
    assert exact["meta"]["totalItems"] == 3
    none = (await client.get(f"{P}/audit/search?tenantId={tenant}", headers=headers)).json()
    assert none["items"] == []
    # The operator's email is joined in for display.
    own = (await client.get(f"{P}/audit/search?action=login_succeeded", headers=headers)).json()
    assert own["items"][0]["adminEmail"] == EMAIL


async def test_the_security_summary_counts_refusals_by_kind_and_address(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    secret = await make_admin(db_session, role="auditor")
    for _ in range(2):
        await client.post(
            LOGIN, json={"email": EMAIL, "password": "Wrong-Password-123", "code": "000000"}
        )
    headers = await sign_in(client, secret)
    summary = (await client.get(f"{P}/security?hours=24", headers=headers)).json()
    assert summary["windowHours"] == 24
    assert summary["byAction"]["login_failed"] == 2
    assert summary["topIps"][0]["failures"] >= 2


async def test_an_audit_export_needs_reauth_and_is_itself_audited(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    secret = await make_admin(db_session, role="auditor")
    headers = await sign_in(client, secret)
    await a_row(db_session, action="workspace_viewed")
    first = await client.get(f"{P}/audit/export?action=workspace_viewed", headers=headers)
    assert first.status_code == 403 and first.json()["code"] == "reauth_required"

    confirmed = await client.post(
        REAUTH, json={"password": STRONG_PASSWORD, "code": code(secret, ahead=1)}, headers=headers
    )
    assert confirmed.status_code == 200
    response = await client.get(f"{P}/audit/export?action=workspace_viewed", headers=headers)
    assert response.status_code == 200, response.text
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert [r["action"] for r in rows] == ["workspace_viewed"]
    exported = await db_session.scalars(
        sa.select(PlatformAdminAudit).where(PlatformAdminAudit.action == "audit_exported")
    )
    [row] = exported.all()
    assert row.detail == {"rows": 1, "filters": {"action": "workspace_viewed"}}


async def test_support_cannot_read_or_export_the_audit(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    headers = await sign_in(client, await make_admin(db_session, role="support"), reauth=True)
    assert (await client.get(f"{P}/audit/search", headers=headers)).status_code == 403
    assert (await client.get(f"{P}/audit/export", headers=headers)).status_code == 403
    assert (await client.get(f"{P}/security", headers=headers)).status_code == 403
