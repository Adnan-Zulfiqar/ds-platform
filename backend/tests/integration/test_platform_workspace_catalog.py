"""Admin Control Center phase 6 (D-019): catalogue and order actions,
reusing the merchant's own services and guards, audited."""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import Order
from app.models.platform_admin import PlatformAdminAudit
from app.models.product import ImportStatus, ProductImport, ProductSource
from app.models.supplier_order import SupplierOrder
from tests.integration.test_platform_admin_auth import make_admin, sign_in
from tests.integration.test_platform_admin_auth import panel as panel
from tests.integration.test_platform_workspace_data import seed
from tests.integration.test_platform_workspace_users import open_session, workspace

pytestmark = pytest.mark.integration

P = "/api/v1/platform/workspaces"


async def audit(db_session: AsyncSession, action: str) -> list[PlatformAdminAudit]:
    return list(
        await db_session.scalars(
            sa.select(PlatformAdminAudit).where(PlatformAdminAudit.action == action)
        )
    )


async def ready(
    client: AsyncClient, db_session: AsyncSession, role: str = "operations"
) -> tuple[uuid.UUID, dict[str, str]]:
    tenant_id, _ = await workspace(client)
    headers = await sign_in(client, await make_admin(db_session, role=role), reauth=True)
    await open_session(client, headers, tenant_id)
    return tenant_id, headers


async def test_failed_imports_are_listed_and_a_retry_that_cannot_run_is_audited(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    tenant_id, headers = await ready(client, db_session)
    failed = ProductImport(
        tenant_id=tenant_id,
        source=ProductSource.ALIEXPRESS,
        external_id="1005001234567890",
        status=ImportStatus.FAILED,
        error_code="supplier_unreachable",
        error_message="timed out",
    )
    db_session.add(failed)
    await db_session.flush()

    listed = await client.get(f"{P}/{tenant_id}/imports?status=failed", headers=headers)
    assert [i["id"] for i in listed.json()["items"]] == [str(failed.id)]

    retried = await client.post(
        f"{P}/{tenant_id}/imports/{failed.id}/retry",
        json={"reason": "merchant asked"},
        headers=headers,
    )
    # No AliExpress connection in this workspace: the retry cannot run, and
    # the attempt is still recorded.
    assert retried.status_code >= 400
    assert [r.outcome for r in await audit(db_session, "workspace_import_retried")] == ["failure"]


async def test_resyncing_a_products_listings_is_queued_and_audited(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    tenant_id, headers = await ready(client, db_session)
    ids = await seed(db_session, tenant_id)
    response = await client.post(
        f"{P}/{tenant_id}/products/{ids['product']}/resync-listings",
        json={"reason": "price drift reported"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"productId": str(ids["product"]), "listings": 0}
    [row] = await audit(db_session, "workspace_listings_resync_queued")
    assert row.detail["listings"] == 0 and row.target_id == str(ids["product"])


async def test_a_stuck_supplier_order_is_released_as_support_and_only_from_placing(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    tenant_id, headers = await ready(client, db_session)
    ids = await seed(db_session, tenant_id)
    stuck = SupplierOrder(tenant_id=tenant_id, order_id=ids["order"], status="placing")
    db_session.add(stuck)
    await db_session.flush()
    url = f"{P}/{tenant_id}/orders/{ids['order']}/supplier-order/release"

    released = await client.post(url, json={"reason": "checked AliExpress: none"}, headers=headers)
    assert released.status_code == 200, released.text
    assert released.json()["status"] == "failed"
    assert released.json()["errorCode"] == "released_by_support"
    assert len(await audit(db_session, "workspace_supplier_order_released")) == 1

    again = await client.post(url, json={"reason": "twice"}, headers=headers)
    assert again.status_code == 409


async def test_refreshing_an_order_without_a_supplier_connection_is_audited_as_a_failure(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    tenant_id, headers = await ready(client, db_session)
    ids = await seed(db_session, tenant_id)
    response = await client.post(
        f"{P}/{tenant_id}/orders/{ids['order']}/refresh",
        json={"reason": "status looks stale"},
        headers=headers,
    )
    assert response.status_code >= 400, response.text
    rows = await audit(db_session, "workspace_order_refreshed")
    assert [r.outcome for r in rows] == ["failure"]


async def test_catalogue_and_order_actions_follow_the_role(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    tenant_id, headers = await ready(client, db_session, role="support")
    ids = await seed(db_session, tenant_id)
    for url in (
        f"{P}/{tenant_id}/products/{ids['product']}/resync-listings",
        f"{P}/{tenant_id}/orders/{ids['order']}/refresh",
    ):
        response = await client.post(url, json={"reason": "try"}, headers=headers)
        assert response.status_code == 403 and response.json()["code"] == "permission_denied"


async def test_another_workspaces_order_cannot_be_released(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    tenant_id, headers = await ready(client, db_session)
    theirs, _ = await workspace(client)
    other = await seed(db_session, theirs)
    response = await client.post(
        f"{P}/{tenant_id}/orders/{other['order']}/supplier-order/release",
        json={"reason": "probe"},
        headers=headers,
    )
    assert response.status_code == 404
    assert (
        await db_session.scalar(
            sa.select(sa.func.count()).select_from(Order).where(Order.id == other["order"])
        )
        == 1
    )
