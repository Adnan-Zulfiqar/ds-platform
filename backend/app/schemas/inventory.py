"""Inventory sync API schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.models.inventory import InventoryChangeReason
from app.models.order import SyncRunStatus, SyncTrigger
from app.schemas.base import CamelCaseModel


class InventorySyncRequest(CamelCaseModel):
    store_id: uuid.UUID | None = None
    product_id: uuid.UUID | None = None


class InventorySyncRunRead(CamelCaseModel):
    id: uuid.UUID
    store_id: uuid.UUID | None
    product_id: uuid.UUID | None
    trigger: SyncTrigger
    status: SyncRunStatus
    products_seen: int
    products_changed: int
    error_code: str | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class InventoryChangeRead(CamelCaseModel):
    id: uuid.UUID
    sync_run_id: uuid.UUID | None
    product_id: uuid.UUID
    variant_id: uuid.UUID | None
    store_id: uuid.UUID | None
    previous_quantity: int
    new_quantity: int
    reason: InventoryChangeReason
    note: str | None
    created_at: datetime


class InventoryProductRead(CamelCaseModel):
    id: uuid.UUID
    title: str
    external_id: str
    store_id: uuid.UUID | None
    stock_quantity: int
    sell_price: str | None = None
    cost_price_min: str | None = None
    currency: str | None
    last_synced_at: datetime | None
    last_sync_error: str | None
    status: str


class InventorySyncRequestBody(InventorySyncRequest):
    """Alias kept for router clarity."""

    trigger_note: str | None = Field(default=None, max_length=255)
