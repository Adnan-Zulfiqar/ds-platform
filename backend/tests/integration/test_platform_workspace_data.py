"""Admin Control Center phase 3 (D-019): an operator drills into one
workspace's data through its tenant-scoped repositories, and never sees
another workspace's rows or anyone's secrets."""

from __future__ import annotations

import csv
import io
import uuid
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import Order
from app.models.platform_admin import PlatformAdminAudit
from app.models.product import Product, ProductSource, ProductStatus
from app.models.store import Store, StorePlatform, StoreStatus
from tests.integration.test_ebay_c1_api import register
from tests.integration.test_platform_admin_auth import make_admin, sign_in
from tests.integration.test_platform_admin_auth import panel as panel

pytestmark = pytest.mark.integration

P = "/api/v1/platform/workspaces"


async def workspace(client: AsyncClient, name: str) -> uuid.UUID:
    owner = await register(client, companyName=name)
    return uuid.UUID(owner["identity"]["tenant"]["id"])


async def seed(
    db_session: AsyncSession, tenant_id: uuid.UUID, *, buyer: str = "Jane Buyer"
) -> dict[str, Any]:
    store = Store(
        tenant_id=tenant_id,
        name="Main store",
        slug=f"main-{uuid.uuid4().hex[:8]}",
        platform=StorePlatform.SHOPIFY,
        status=StoreStatus.CONNECTED,
        currency="USD",
        encrypted_credentials="ciphertext-that-must-never-leave",
    )
    db_session.add(store)
    await db_session.flush()
    product = Product(
        tenant_id=tenant_id,
        source=ProductSource.MANUAL,
        external_id=f"p-{uuid.uuid4().hex[:10]}",
        title="Garden lamp",
        status=ProductStatus.DRAFT,
        sell_price=Decimal("19.99"),
    )
    order = Order(
        tenant_id=tenant_id,
        store_id=store.id,
        source="manual",
        external_id=f"ord-{uuid.uuid4().hex[:10]}",
        buyer_name=buyer,
        buyer_country="GB",
        currency="GBP",
        total_amount=25,
    )
    db_session.add_all([product, order])
    await db_session.flush()
    return {"store": store.id, "product": product.id, "order": order.id}


async def audit_actions(db_session: AsyncSession, action: str) -> list[PlatformAdminAudit]:
    rows = await db_session.scalars(
        sa.select(PlatformAdminAudit).where(PlatformAdminAudit.action == action)
    )
    return list(rows)


async def test_users_list_shows_roles_and_sessions_but_no_secrets(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    tenant_id = await workspace(client, "People Co")
    headers = await sign_in(client, await make_admin(db_session))
    response = await client.get(f"{P}/{tenant_id}/users", headers=headers)
    assert response.status_code == 200, response.text
    [user] = response.json()["items"]
    assert user["roles"] == ["owner"]
    assert user["activeSessions"] >= 1
    assert "passwordHash" not in response.text and "tokenHash" not in response.text


async def test_each_view_shows_only_that_workspace(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    mine = await workspace(client, "Mine")
    theirs = await workspace(client, "Theirs")
    a = await seed(db_session, mine, buyer="Alice Mine")
    b = await seed(db_session, theirs, buyer="Bob Theirs")
    headers = await sign_in(client, await make_admin(db_session))

    orders = (await client.get(f"{P}/{mine}/orders", headers=headers)).json()
    assert [o["buyerName"] for o in orders["items"]] == ["Alice Mine"]
    stores = await client.get(f"{P}/{mine}/stores", headers=headers)
    assert [s["id"] for s in stores.json()["items"]] == [str(a["store"])]
    assert "ciphertext" not in stores.text and "encryptedCredentials" not in stores.text

    # Another workspace's ids under this workspace's path: not found, as for
    # any unknown id.
    assert (await client.get(f"{P}/{mine}/orders/{b['order']}", headers=headers)).status_code == 404
    assert (
        await client.get(f"{P}/{mine}/products/{b['product']}", headers=headers)
    ).status_code == 404
    # The right path works.
    detail = await client.get(f"{P}/{theirs}/orders/{b['order']}", headers=headers)
    assert detail.status_code == 200 and detail.json()["buyerName"] == "Bob Theirs"
    product = await client.get(f"{P}/{mine}/products/{a['product']}", headers=headers)
    assert product.status_code == 200 and product.json()["title"] == "Garden lamp"


async def test_every_list_view_is_audited_with_its_route(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    tenant_id = await workspace(client, "Audited Views")
    headers = await sign_in(client, await make_admin(db_session))
    paths = [
        "users",
        "invitations",
        "stores",
        "connections",
        "products",
        "products?publication=draft",
        "listings",
        "orders",
        "sync-runs",
        "sync-runs?kind=inventory",
        "notifications",
    ]
    for path in paths:
        response = await client.get(f"{P}/{tenant_id}/{path}", headers=headers)
        assert response.status_code == 200, (path, response.text)
    routes = {r.detail["route"] for r in await audit_actions(db_session, "workspace_viewed")}
    assert {
        "/workspaces/{tenant_id}/users",
        "/workspaces/{tenant_id}/connections",
        "/workspaces/{tenant_id}/orders",
        "/workspaces/{tenant_id}/sync-runs",
    } <= routes


async def test_an_unknown_filter_value_is_a_validation_error(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    tenant_id = await workspace(client, "Filters")
    headers = await sign_in(client, await make_admin(db_session))
    for path in ("orders?fulfillment_status=bogus", "stores?status=bogus", "listings?status=x"):
        response = await client.get(f"{P}/{tenant_id}/{path}", headers=headers)
        assert response.status_code == 422, (path, response.text)


async def test_exports_need_reauth_are_audited_and_neutralise_formulas(
    client: AsyncClient, db_session: AsyncSession, panel: None
) -> None:
    tenant_id = await workspace(client, "Export Co")
    await seed(db_session, tenant_id, buyer='=HYPERLINK("http://evil")')
    secret = await make_admin(db_session)

    plain = await sign_in(client, secret)
    refused = await client.get(f"{P}/{tenant_id}/export/orders", headers=plain)
    assert refused.status_code == 403 and refused.json()["code"] == "reauth_required"

    confirmed = await sign_in(client, secret, reauth=True)
    response = await client.get(f"{P}/{tenant_id}/export/orders", headers=confirmed)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    [row] = list(csv.DictReader(io.StringIO(response.text)))
    assert row["buyer_name"].startswith("'=")
    [audit] = await audit_actions(db_session, "workspace_data_exported")
    assert audit.detail == {"dataset": "orders", "rows": 1}
    assert str(audit.target_tenant_id) == str(tenant_id)


@pytest.mark.parametrize(
    ("role", "allowed"), [("auditor", 200), ("support", 200), ("finance", 403)]
)
async def test_workspace_data_follows_the_role(
    client: AsyncClient, db_session: AsyncSession, panel: None, role: str, allowed: int
) -> None:
    tenant_id = await workspace(client, f"Role {role}")
    headers = await sign_in(client, await make_admin(db_session, role=role))
    response = await client.get(f"{P}/{tenant_id}/orders", headers=headers)
    assert response.status_code == allowed
