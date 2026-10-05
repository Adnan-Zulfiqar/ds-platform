"""The scheduled tasks reach every channel, and the shipment refresh runs.

Found by the 2026-10-04 analysis: ``shipment.refresh`` raised ``TypeError``
on every run; the inventory sweep pushed only to eBay; the pricing sweep and
the supplier refresh pushed to nothing. No broker, no database.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest

import app.tasks.orders as orders_tasks
from app.tasks import inventory as inventory_tasks
from app.tasks import pricing as pricing_tasks
from app.tasks import products as products_tasks
from app.tasks.integrations import channels
from app.tasks.shipments import refresh_shipments

pytestmark = pytest.mark.unit


@pytest.fixture
def fan_out(monkeypatch: pytest.MonkeyPatch) -> list[tuple[uuid.UUID, list[uuid.UUID]]]:
    calls: list[tuple[uuid.UUID, list[uuid.UUID]]] = []
    monkeypatch.setattr(
        channels, "enqueue_price_quantity", lambda t, ids: calls.append((t, list(ids))) or {}
    )
    return calls


@asynccontextmanager
async def _no_db() -> AsyncIterator[object]:
    yield object()


def test_shipment_refresh_delegates_without_passing_self_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenants = [uuid.uuid4(), uuid.uuid4()]
    seen: list[tuple[uuid.UUID, int]] = []

    async def connected() -> list[uuid.UUID]:
        return tenants

    async def refresh(tenant_id: uuid.UUID, *, limit: int) -> int:
        seen.append((tenant_id, limit))
        return 3

    monkeypatch.setattr(orders_tasks, "_connected_tenants", connected)
    monkeypatch.setattr(orders_tasks, "_refresh_tenant_orders", refresh)
    assert refresh_shipments.apply(kwargs={"limit": 5}).get(propagate=True) == 6
    assert seen == [(tenants[0], 5), (tenants[1], 5)]


def test_inventory_sweep_pushes_changed_products_to_every_channel(
    monkeypatch: pytest.MonkeyPatch, fan_out: list[tuple[uuid.UUID, list[uuid.UUID]]]
) -> None:
    tenant, changed = uuid.uuid4(), [uuid.uuid4(), uuid.uuid4()]

    @dataclass
    class Run:
        products_seen: int = 3
        products_changed: int = 2
        status: Any = field(default_factory=lambda: type("S", (), {"value": "ok"})())

    class FakeSync:
        changed_product_ids = changed

        def __init__(self, _session: object) -> None: ...

        async def sync(self, *, trigger: object) -> Run:
            return Run()

    monkeypatch.setattr(inventory_tasks, "transaction", _no_db)
    monkeypatch.setattr(inventory_tasks, "InventorySyncService", FakeSync)
    inventory_tasks.sync_inventory_one.apply(args=[str(tenant)]).get(propagate=True)
    assert fan_out == [(tenant, changed)]


def test_pricing_sweep_pushes_the_repriced_products(
    monkeypatch: pytest.MonkeyPatch, fan_out: list[tuple[uuid.UUID, list[uuid.UUID]]]
) -> None:
    tenant, repriced = uuid.uuid4(), [uuid.uuid4()]

    @dataclass
    class Change:
        product_id: uuid.UUID

    class FakeEngine:
        def __init__(self, _session: object) -> None: ...

        async def apply(self, _request: object) -> list[Change]:
            return [Change(p) for p in repriced]

    monkeypatch.setattr(pricing_tasks, "transaction", _no_db)
    monkeypatch.setattr(pricing_tasks, "PricingEngine", FakeEngine)
    assert pricing_tasks.recalculate_one.apply(args=[str(tenant)]).get(propagate=True) == 1
    assert fan_out == [(tenant, repriced)]


@pytest.mark.parametrize(
    ("before", "after", "pushed"),
    [
        (((1, 5),), ((1, 4),), True),  # stock moved
        (((1, 5),), ((1, 5),), False),  # nothing moved: no outbound call
    ],
)
def test_supplier_refresh_pushes_only_when_price_or_stock_moved(
    monkeypatch: pytest.MonkeyPatch,
    fan_out: list[tuple[uuid.UUID, list[uuid.UUID]]],
    before: tuple[Any, ...],
    after: tuple[Any, ...],
    pushed: bool,
) -> None:
    tenant, product_id = uuid.uuid4(), uuid.uuid4()
    snapshots = iter([before, after])

    @dataclass
    class Product:
        external_id: str = "123"
        import_ship_to_country: str | None = None
        ship_to_country: str | None = "GB"

    class FakeImport:
        def __init__(self, _session: object) -> None:
            self.products = self

        async def get_by_id(self, _id: uuid.UUID) -> Product:
            return Product()

        async def import_product(self, **_: Any) -> None: ...

    async def snapshot(_session: object, _id: uuid.UUID) -> tuple[Any, ...]:
        return next(snapshots)

    monkeypatch.setattr(products_tasks, "transaction", _no_db)
    monkeypatch.setattr(products_tasks, "ProductImportService", FakeImport)
    monkeypatch.setattr(products_tasks, "price_stock_snapshot", snapshot)
    assert products_tasks.sync_product.apply(args=[str(product_id), str(tenant)]).get(
        propagate=True
    )
    assert fan_out == ([(tenant, [product_id])] if pushed else [])
