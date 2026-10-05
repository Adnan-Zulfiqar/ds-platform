"""``InventorySyncService`` had no direct test (2026-10-04 analysis).

Real Postgres; the supplier call is the only thing faked. The properties:
a stock move is recorded once and queued for the channels, a no-op refresh
records nothing, one failing product does not fail the run, a run that
fails outright is marked and reported, and two syncs cannot overlap.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import set_tenant_id
from app.core.exceptions import ConflictError
from app.integrations.aliexpress.exceptions import AliExpressError
from app.models.inventory import InventoryChange, InventoryChangeReason
from app.models.notification import Notification, NotificationKind
from app.models.order import SyncRunStatus, SyncTrigger
from app.models.product import Product, ProductSource, ProductStatus
from app.services.inventory_sync import InventorySyncService
from tests.integration.test_ebay_c1_api import register

pytestmark = pytest.mark.integration


async def workspace(client: AsyncClient) -> uuid.UUID:
    owner = await register(client)
    tenant_id = uuid.UUID(str(owner["identity"]["tenant"]["id"]))
    set_tenant_id(tenant_id)
    return tenant_id


async def product(db_session: AsyncSession, tenant_id: uuid.UUID, external_id: str) -> Product:
    row = Product(
        tenant_id=tenant_id,
        source=ProductSource.ALIEXPRESS,
        external_id=external_id,
        title=f"Item {external_id}",
        status=ProductStatus.ACTIVE,
        sell_price=Decimal("10"),
        stock_quantity=5,
    )
    db_session.add(row)
    await db_session.flush()
    return row


def fake_supplier(
    db_session: AsyncSession, tenant_id: uuid.UUID, *, stock: dict[str, int], broken: set[str]
) -> Any:
    """Stands in for ``ProductImportService.import_product``: sets the new
    stock the supplier reports, or fails the way the client would."""

    async def import_product(*, external_id: str, **_: Any) -> Product:
        if external_id in broken:
            raise AliExpressError("supplier timeout")
        row = (
            await db_session.execute(
                sa.select(Product).where(
                    Product.tenant_id == tenant_id, Product.external_id == external_id
                )
            )
        ).scalar_one()
        row.stock_quantity = stock[external_id]
        await db_session.flush()
        return row

    return import_product


async def notifications(db_session: AsyncSession, tenant_id: uuid.UUID) -> list[Notification]:
    rows = await db_session.execute(
        sa.select(Notification).where(Notification.tenant_id == tenant_id)
    )
    return list(rows.scalars().all())


async def test_a_sweep_records_each_move_once_and_skips_a_failing_product(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = await workspace(client)
    moved = await product(db_session, tenant_id, "100")
    same = await product(db_session, tenant_id, "200")
    broken = await product(db_session, tenant_id, "300")
    service = InventorySyncService(db_session)
    monkeypatch.setattr(
        service.imports,
        "import_product",
        fake_supplier(db_session, tenant_id, stock={"100": 0, "200": 5}, broken={"300"}),
    )

    run = await service.sync(trigger=SyncTrigger.SCHEDULED)

    assert run.status is SyncRunStatus.SUCCEEDED
    assert (run.products_seen, run.products_changed) == (2, 1)
    assert service.changed_product_ids == [moved.id]
    changes = (await db_session.execute(sa.select(InventoryChange))).scalars().all()
    assert [(c.product_id, c.previous_quantity, c.new_quantity, c.reason) for c in changes] == [
        (moved.id, 5, 0, InventoryChangeReason.SUPPLIER_SYNC)
    ]
    assert same.stock_quantity == 5
    assert broken.last_sync_error == "supplier timeout"
    [note] = await notifications(db_session, tenant_id)
    assert note.kind is NotificationKind.INVENTORY_CHANGED
    assert note.payload["changed"] == 1 and note.payload["seen"] == 2


async def test_a_refresh_that_changes_nothing_records_nothing(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = await workspace(client)
    await product(db_session, tenant_id, "100")
    service = InventorySyncService(db_session)
    monkeypatch.setattr(
        service.imports,
        "import_product",
        fake_supplier(db_session, tenant_id, stock={"100": 5}, broken=set()),
    )

    run = await service.sync()

    assert (run.products_seen, run.products_changed) == (1, 0)
    assert service.changed_product_ids == []
    assert (await db_session.execute(sa.select(InventoryChange))).scalars().all() == []
    assert await notifications(db_session, tenant_id) == []


async def test_a_run_that_fails_outright_is_marked_reported_and_raised(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = await workspace(client)
    await product(db_session, tenant_id, "100")
    service = InventorySyncService(db_session)

    async def explode(**_: Any) -> list[Product]:
        raise RuntimeError("database went away")

    monkeypatch.setattr(service, "_targets", explode)

    with pytest.raises(RuntimeError):
        await service.sync()

    [run] = (await db_session.execute(sa.select(service.runs.model))).scalars().all()
    assert run.status is SyncRunStatus.FAILED
    assert (run.error_code, run.error_message) == ("RuntimeError", "database went away")
    assert run.finished_at is not None
    [note] = await notifications(db_session, tenant_id)
    assert note.kind is NotificationKind.SYNC_FAILED


async def test_two_syncs_cannot_overlap(client: AsyncClient, db_session: AsyncSession) -> None:
    await workspace(client)
    service = InventorySyncService(db_session)
    await service.runs.create(trigger=SyncTrigger.MANUAL, status=SyncRunStatus.RUNNING)
    await db_session.flush()

    with pytest.raises(ConflictError):
        await service.sync()
