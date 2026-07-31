"""Store API schemas. Credentials never appear here."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import Field

from app.models.store import StorePlatform, StoreStatus
from app.schemas.base import CamelCaseModel


class StoreCreate(CamelCaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    platform: StorePlatform = StorePlatform.MANUAL
    storefront_url: str | None = Field(default=None, max_length=1024)
    external_store_id: str | None = Field(default=None, max_length=128)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    timezone: str = Field(default="UTC", max_length=64)
    settings: dict[str, Any] = Field(default_factory=dict)
    inventory_sync_enabled: bool = True
    pricing_sync_enabled: bool = True
    order_sync_enabled: bool = True
    #: Plain credentials accepted once, encrypted at rest, never returned.
    credentials: dict[str, str] | None = None


class StoreUpdate(CamelCaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    storefront_url: str | None = None
    status: StoreStatus | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    timezone: str | None = None
    settings: dict[str, Any] | None = None
    inventory_sync_enabled: bool | None = None
    pricing_sync_enabled: bool | None = None
    order_sync_enabled: bool | None = None
    credentials: dict[str, str] | None = None


class StoreRead(CamelCaseModel):
    id: uuid.UUID
    name: str
    slug: str
    platform: StorePlatform
    status: StoreStatus
    storefront_url: str | None
    external_store_id: str | None
    currency: str
    timezone: str
    settings: dict[str, Any]
    inventory_sync_enabled: bool
    pricing_sync_enabled: bool
    order_sync_enabled: bool
    last_sync_at: datetime | None
    last_activity_at: datetime | None
    last_error: str | None
    health_score: int
    created_at: datetime
    updated_at: datetime


class StoreStatisticsRead(CamelCaseModel):
    total_stores: int
    by_status: dict[str, int]
    connected: int
    with_errors: int
    product_count: int
    last_activity_at: datetime | None
