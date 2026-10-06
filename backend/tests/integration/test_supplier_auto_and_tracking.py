"""Automatic placement and tracking sync (Track F, D-017).

Real Postgres; AliExpress and the store's "mark shipped" are fakes. The
properties: automatic mode does nothing while its switch is off and never
touches an order a person should look at; a tracking number is stored when
AliExpress has one and sent to the store only with auto-tracking on; a store
refusal is recorded without losing the number; an order split into several
parcels is never pushed as if it were one.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import set_tenant_id
from app.core.exceptions import ValidationError
from app.integrations.shopify.sync import ShopifySyncService
from app.models.order import OrderSource, PaymentStatus
from app.models.supplier_order import SupplierOrder, SupplierOrderStatus
from app.repositories.supplier_order import SupplierOrderRepository
from app.services.supplier_ordering import SupplierOrderingService
from app.services.supplier_tracking import SupplierTrackingService
from app.tasks import supplier_orders as tasks
from tests.integration.test_supplier_orders import catalogue, channel_order, workspace

pytestmark = pytest.mark.integration


class FakeAliExpress:
    def __init__(self) -> None:
        self.tracking: dict[str, str] = {}
        self.calls: list[str] = []

    async def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(method)
        number = self.tracking.get(str(params.get("ae_order_id")))
        lines = [{"carrier_name": "Cainiao", "mail_no": number}] if number else []
        return {"result": {"ret": True, "data": {"tracking_detail_line_list": lines}}}


@pytest.fixture
def shared(monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession) -> FakeAliExpress:
    fake = FakeAliExpress()

    @asynccontextmanager
    async def on_test_session() -> AsyncIterator[AsyncSession]:
        yield db_session
        await db_session.flush()

    async def client(_self: object) -> FakeAliExpress:
        return fake

    monkeypatch.setattr(tasks, "transaction", on_test_session)
    monkeypatch.setattr(
        "app.services.supplier_tracking.AliExpressService.authenticated_client", client
    )
    monkeypatch.setattr(tasks, "place_after_commit", lambda _s, _id: None)
    return fake


async def placed(
    db_session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    source: OrderSource = OrderSource.WOOCOMMERCE,
    ae_ids: list[str] | None = None,
) -> tuple[uuid.UUID, SupplierOrder]:
    product_id, [only] = await catalogue(
        db_session, tenant_id, variants=["14:Only"], external_id=uuid.uuid4().hex[:12]
    )
    order_id = await channel_order(db_session, tenant_id, [(product_id, only, 1)], source=source)
    row = await SupplierOrderRepository(db_session).create(
        order_id=order_id,
        status=SupplierOrderStatus.PLACED.value,
        trigger="manual",
        external_order_ids=ae_ids or ["8001"],
    )
    return order_id, row


async def switches(db_session: AsyncSession, *, auto_order: bool, auto_tracking: bool) -> None:
    await SupplierOrderingService(db_session).update_settings(
        auto_order=auto_order, auto_tracking=auto_tracking, fallback_shipping_method=None
    )


# --- automatic placement ------------------------------------------------------


async def test_auto_mode_does_nothing_while_the_switch_is_off(
    client: AsyncClient, db_session: AsyncSession, shared: FakeAliExpress
) -> None:
    _, tenant_id = await workspace(client)
    product_id, [only] = await catalogue(db_session, tenant_id, variants=["14:Only"])
    order_id = await channel_order(db_session, tenant_id, [(product_id, only, 1)])

    assert await tasks._auto_place(tenant_id, [order_id]) == 0
    set_tenant_id(tenant_id)
    assert await SupplierOrderRepository(db_session).for_order(order_id) is None


async def test_auto_mode_queues_new_paid_orders_and_leaves_the_rest(
    client: AsyncClient, db_session: AsyncSession, shared: FakeAliExpress
) -> None:
    _, tenant_id = await workspace(client)
    await switches(db_session, auto_order=True, auto_tracking=False)
    product_id, [only] = await catalogue(db_session, tenant_id, variants=["14:Only"])
    fresh = await channel_order(db_session, tenant_id, [(product_id, only, 1)])
    unpaid = await channel_order(
        db_session, tenant_id, [(product_id, only, 1)], payment_status=PaymentStatus.UNPAID
    )
    reviewed = await channel_order(db_session, tenant_id, [(product_id, only, 1)])
    await SupplierOrderRepository(db_session).create(
        order_id=reviewed, status=SupplierOrderStatus.FAILED.value, trigger="manual"
    )

    assert await tasks._auto_place(tenant_id, [fresh, unpaid, reviewed]) == 1
    set_tenant_id(tenant_id)
    rows = SupplierOrderRepository(db_session)
    queued = await rows.for_order(fresh)
    assert queued is not None
    assert (queued.status, queued.trigger) == (SupplierOrderStatus.QUEUED.value, "auto")
    assert await rows.for_order(unpaid) is None
    failed = await rows.for_order(reviewed)
    assert failed is not None and failed.status == SupplierOrderStatus.FAILED.value  # untouched


async def test_a_paid_shopify_import_offers_the_order_to_auto_mode(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, tenant_id = await workspace(client)
    offered: list[uuid.UUID] = []
    monkeypatch.setattr(
        tasks, "auto_order_after_commit", lambda _s, order_id: offered.append(order_id)
    )
    from app.models.store import Store, StorePlatform, StoreStatus

    store = Store(
        tenant_id=tenant_id,
        name="Shop",
        slug=f"s-{uuid.uuid4().hex[:6]}",
        platform=StorePlatform.SHOPIFY,
        status=StoreStatus.CONNECTED,
        currency="USD",
    )
    db_session.add(store)
    await db_session.flush()
    sync = ShopifySyncService(db_session)
    raw = {"id": 1, "financial_status": "paid", "currency": "USD", "line_items": []}
    await sync.upsert_order_from_shopify(store_id=store.id, raw=raw)
    await sync.upsert_order_from_shopify(
        store_id=store.id, raw={**raw, "id": 2, "financial_status": "pending"}
    )
    assert len(offered) == 1


def test_the_hook_batches_per_transaction_and_forgets_on_rollback(
    monkeypatch: pytest.MonkeyPatch, tenant_id: uuid.UUID
) -> None:
    listeners: dict[str, Any] = {}

    class Sync:
        def __init__(self) -> None:
            self.info: dict[str, Any] = {}

    class Session:
        def __init__(self) -> None:
            self.sync_session = Sync()

    monkeypatch.setattr(
        tasks.event, "listen", lambda _t, name, fn, **_: listeners.__setitem__(name, fn)
    )
    sent: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(tasks.auto_place, "delay", lambda t, ids: sent.append((t, ids)))
    set_tenant_id(tenant_id)
    a, b = uuid.uuid4(), uuid.uuid4()
    session = Session()
    tasks.auto_order_after_commit(session, a)
    tasks.auto_order_after_commit(session, b)
    tasks.auto_order_after_commit(session, a)
    assert sent == []  # nothing before the commit
    listeners["after_commit"](object())
    assert sent == [(str(tenant_id), [str(a), str(b)])]

    tasks.auto_order_after_commit(session, a)
    listeners["after_rollback"](object())
    assert "auto_order_ids" not in session.sync_session.info


# --- tracking -----------------------------------------------------------------


async def test_tracking_is_stored_but_not_sent_while_auto_tracking_is_off(
    client: AsyncClient, db_session: AsyncSession, shared: FakeAliExpress
) -> None:
    _, tenant_id = await workspace(client)
    _, row = await placed(db_session, tenant_id)
    shared.tracking["8001"] = "LP00123"

    counts = await tasks._sync_tracking(tenant_id, limit=10)
    set_tenant_id(tenant_id)

    assert counts == {"checked": 1, "found": 1, "pushed": 0, "failed": 0}
    assert (row.tracking_number, row.tracking_carrier) == ("LP00123", "Cainiao")
    assert row.status == SupplierOrderStatus.PLACED.value
    assert row.tracking_pushed_at is None


async def test_auto_tracking_sends_to_the_store_once_and_marks_shipped(
    client: AsyncClient,
    db_session: AsyncSession,
    shared: FakeAliExpress,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, tenant_id = await workspace(client)
    await switches(db_session, auto_order=False, auto_tracking=True)
    order_id, row = await placed(db_session, tenant_id)
    shared.tracking["8001"] = "LP00123"
    shipped: list[dict[str, Any]] = []

    async def mark_shipped(_self: object, oid: uuid.UUID, **kwargs: Any) -> None:
        shipped.append({"order_id": oid, **kwargs})

    monkeypatch.setattr(
        "app.integrations.woocommerce.orders.WooCommerceOrderService.mark_shipped", mark_shipped
    )

    counts = await tasks._sync_tracking(tenant_id, limit=10)
    set_tenant_id(tenant_id)
    assert counts["pushed"] == 1
    assert shipped == [
        {
            "order_id": order_id,
            "company": "Cainiao",
            "tracking_number": "LP00123",
            "tracking_url": None,
            "notify_customer": True,
        }
    ]
    assert row.status == SupplierOrderStatus.SHIPPED.value
    assert row.tracking_pushed_at is not None

    assert (await tasks._sync_tracking(tenant_id, limit=10))["checked"] == 0  # not asked again
    assert len(shipped) == 1


async def test_a_store_refusal_is_recorded_and_the_number_kept(
    client: AsyncClient,
    db_session: AsyncSession,
    shared: FakeAliExpress,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, tenant_id = await workspace(client)
    await switches(db_session, auto_order=False, auto_tracking=True)
    _, row = await placed(db_session, tenant_id, source=OrderSource.EBAY)
    shared.tracking["8001"] = "LP00123"

    async def refuse(_self: object, _oid: uuid.UUID, **_: Any) -> None:
        raise ValidationError("Unknown carrier code.")

    monkeypatch.setattr("app.integrations.ebay.orders.EbayOrderService.mark_shipped", refuse)

    counts = await tasks._sync_tracking(tenant_id, limit=10)
    set_tenant_id(tenant_id)
    assert counts["failed"] == 1
    assert row.status == SupplierOrderStatus.PLACED.value
    assert row.tracking_number == "LP00123"
    assert row.error_code == "tracking_push:validation_error"


async def test_several_parcels_are_never_pushed_as_one(
    client: AsyncClient,
    db_session: AsyncSession,
    shared: FakeAliExpress,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, tenant_id = await workspace(client)
    await switches(db_session, auto_order=False, auto_tracking=True)
    _, row = await placed(db_session, tenant_id, ae_ids=["8001", "8002"])
    shared.tracking.update({"8001": "LP1", "8002": "LP2"})
    pushed: list[object] = []
    monkeypatch.setattr(
        "app.integrations.woocommerce.orders.WooCommerceOrderService.mark_shipped",
        lambda *a, **k: pushed.append(a),
    )

    counts = await tasks._sync_tracking(tenant_id, limit=10)
    set_tenant_id(tenant_id)
    assert counts["pushed"] == 0
    assert pushed == []
    assert row.error_code == "multiple_parcels"
    with pytest.raises(ValidationError):
        await SupplierTrackingService(db_session).push(row)


async def test_the_manual_push_endpoint_needs_a_supplier_order_and_a_number(
    client: AsyncClient, db_session: AsyncSession, shared: FakeAliExpress
) -> None:
    from tests.integration.test_ebay_c1_api import auth_header

    owner, tenant_id = await workspace(client)
    product_id, [only] = await catalogue(db_session, tenant_id, variants=["14:Only"])
    bare = await channel_order(db_session, tenant_id, [(product_id, only, 1)])
    url = f"/api/v1/orders/{bare}/supplier-order/push-tracking"
    assert (await client.post(url, headers=auth_header(owner))).status_code == 404

    set_tenant_id(tenant_id)  # the request cleared the context on its way out
    order_id, _ = await placed(db_session, tenant_id)
    url = f"/api/v1/orders/{order_id}/supplier-order/push-tracking"
    response = await client.post(url, headers=auth_header(owner))
    assert response.status_code == 422
