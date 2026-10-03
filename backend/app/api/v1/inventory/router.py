"""Inventory synchronisation endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import event

from app.api.deps import DbSession, RequireAdmin, RequireViewer
from app.core.context import require_tenant_id
from app.models.order import SyncTrigger
from app.schemas.common import ListQueryParams, Page, list_query_params
from app.schemas.inventory import (
    InventoryChangeRead,
    InventoryProductRead,
    InventorySyncRequest,
    InventorySyncRunRead,
)
from app.services.inventory_sync import InventorySyncService
from app.services.product_import import ProductImportService
from app.tasks.integrations.ebay import enqueue_price_quantity

router = APIRouter(prefix="/inventory", tags=["inventory"])


@router.get("", response_model=Page[InventoryProductRead])
async def list_inventory(
    session: DbSession,
    params: Annotated[ListQueryParams, Depends(list_query_params)],
    _authorized: RequireViewer,
    store_id: Annotated[uuid.UUID | None, Query(alias="storeId")] = None,
) -> Page[InventoryProductRead]:
    filters: dict[str, object] = {}
    if store_id is not None:
        filters["store_id"] = store_id
    products, total = await ProductImportService(session).products.list(
        params, filters=filters or None
    )
    items = [
        InventoryProductRead(
            id=p.id,
            title=p.title,
            external_id=p.external_id,
            store_id=p.store_id,
            stock_quantity=p.stock_quantity,
            sell_price=str(p.sell_price) if p.sell_price is not None else None,
            cost_price_min=str(p.cost_price_min) if p.cost_price_min is not None else None,
            currency=p.currency,
            last_synced_at=p.last_synced_at,
            last_sync_error=p.last_sync_error,
            status=p.status.value,
        )
        for p in products
    ]
    return Page[InventoryProductRead].build(
        items=items, page=params.page, size=params.size, total_items=total
    )


@router.post("/sync", response_model=InventorySyncRunRead, status_code=status.HTTP_201_CREATED)
async def sync_inventory(
    session: DbSession,
    principal: RequireAdmin,
    payload: InventorySyncRequest | None = None,
) -> InventorySyncRunRead:
    body = payload or InventorySyncRequest()
    service = InventorySyncService(session)
    run = await service.sync(
        store_id=body.store_id,
        product_id=body.product_id,
        trigger=SyncTrigger.MANUAL,
        requested_by_user_id=principal.user_id,
    )
    changed = list(service.changed_product_ids)
    if changed:
        # EBAY-C4: push moved stock to eBay once this request has committed.
        tenant_id = require_tenant_id()

        def on_commit(_session: object) -> None:
            enqueue_price_quantity(tenant_id, changed)

        event.listen(session.sync_session, "after_commit", on_commit, once=True)
    return InventorySyncRunRead.model_validate(run)


@router.get("/sync-runs", response_model=Page[InventorySyncRunRead])
async def list_sync_runs(
    session: DbSession,
    params: Annotated[ListQueryParams, Depends(list_query_params)],
    _authorized: RequireViewer,
) -> Page[InventorySyncRunRead]:
    runs, total = await InventorySyncService(session).list_runs(params)
    return Page[InventorySyncRunRead].build(
        items=[InventorySyncRunRead.model_validate(r) for r in runs],
        page=params.page,
        size=params.size,
        total_items=total,
    )


@router.get("/changes", response_model=Page[InventoryChangeRead])
async def list_changes(
    session: DbSession,
    params: Annotated[ListQueryParams, Depends(list_query_params)],
    _authorized: RequireViewer,
) -> Page[InventoryChangeRead]:
    rows, total = await InventorySyncService(session).list_changes(params)
    return Page[InventoryChangeRead].build(
        items=[InventoryChangeRead.model_validate(r) for r in rows],
        page=params.page,
        size=params.size,
        total_items=total,
    )
