"""Sales-channel store management.

Credentials are encrypted at rest with the same Fernet stack as AliExpress
tokens. Responses never include credentials — not even masked.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import decrypt, encrypt
from app.core.exceptions import ConflictError, ValidationError
from app.models.store import Store, StorePlatform, StoreStatus
from app.repositories.product import ProductRepository
from app.repositories.store import StoreRepository
from app.schemas.common import ListQueryParams
from app.schemas.store import StoreCreate, StoreStatisticsRead, StoreUpdate
from app.services.base import BaseService


class StoreService(BaseService):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.stores = StoreRepository(session)
        self.products = ProductRepository(session)

    async def list(self, params: ListQueryParams) -> tuple[list[Store], int]:
        rows, total = await self.stores.list(params)
        return list(rows), total

    async def get(self, store_id: uuid.UUID) -> Store:
        return await self.stores.get_by_id_or_raise(store_id)

    async def create(
        self, payload: StoreCreate, *, connected_by_user_id: uuid.UUID | None
    ) -> Store:
        if await self.stores.get_by_slug(payload.slug) is not None:
            raise ConflictError("A store with this slug already exists.")

        encrypted: str | None = None
        status = StoreStatus.PENDING
        if payload.credentials:
            encrypted = encrypt(json.dumps(payload.credentials))
            status = StoreStatus.CONNECTED

        return await self.stores.create(
            name=payload.name,
            slug=payload.slug,
            platform=payload.platform,
            status=status,
            storefront_url=payload.storefront_url,
            external_store_id=payload.external_store_id,
            encrypted_credentials=encrypted,
            currency=payload.currency.upper(),
            timezone=payload.timezone,
            settings=payload.settings,
            inventory_sync_enabled=payload.inventory_sync_enabled,
            pricing_sync_enabled=payload.pricing_sync_enabled,
            order_sync_enabled=payload.order_sync_enabled,
            connected_by_user_id=connected_by_user_id,
            last_activity_at=datetime.now(UTC) if status is StoreStatus.CONNECTED else None,
            health_score=100 if status is StoreStatus.CONNECTED else 50,
        )

    async def update(self, store_id: uuid.UUID, payload: StoreUpdate) -> Store:
        store = await self.stores.get_by_id_or_raise(store_id)
        data = payload.model_dump(exclude_unset=True)
        credentials = data.pop("credentials", None)
        for field, value in data.items():
            if field == "currency" and isinstance(value, str):
                value = value.upper()
            setattr(store, field, value)
        if credentials is not None:
            if not isinstance(credentials, dict):
                raise ValidationError("Credentials must be an object of string values.")
            store.encrypted_credentials = encrypt(json.dumps(credentials))
            if store.status in (StoreStatus.PENDING, StoreStatus.DISCONNECTED):
                store.status = StoreStatus.CONNECTED
            store.last_activity_at = datetime.now(UTC)
        await self.session.flush()
        return store

    async def delete(self, store_id: uuid.UUID) -> None:
        store = await self.stores.get_by_id_or_raise(store_id)
        await self.stores.soft_delete(store)

    async def statistics(self) -> StoreStatisticsRead:
        by_status = await self.stores.count_by_status()
        total = sum(by_status.values())
        connected = by_status.get(StoreStatus.CONNECTED.value, 0)
        errors = by_status.get(StoreStatus.ERROR.value, 0)
        # Product count across all stores: count products with any store_id set.
        from sqlalchemy import func, select

        from app.models.product import Product

        where = self.products._base_query().whereclause
        query = (
            select(func.count(Product.id)).select_from(Product).where(Product.store_id.is_not(None))
        )
        if where is not None:
            query = query.where(where)
        product_count = int((await self.session.execute(query)).scalar_one())

        from app.schemas.common import SortDirection

        latest_rows, _ = await self.stores.list(
            ListQueryParams(
                page=1,
                size=1,
                sort_by="last_activity_at",
                sort_dir=SortDirection.DESC,
            )
        )
        last_activity = latest_rows[0].last_activity_at if latest_rows else None

        return StoreStatisticsRead(
            total_stores=total,
            by_status=by_status,
            connected=connected,
            with_errors=errors,
            product_count=product_count,
            last_activity_at=last_activity,
        )

    async def health(self, store_id: uuid.UUID) -> dict[str, Any]:
        store = await self.stores.get_by_id_or_raise(store_id)
        has_credentials = bool(store.encrypted_credentials)
        return {
            "storeId": str(store.id),
            "status": store.status.value,
            "healthScore": store.health_score,
            "hasCredentials": has_credentials,
            "lastSyncAt": store.last_sync_at,
            "lastActivityAt": store.last_activity_at,
            "lastError": store.last_error,
            "inventorySyncEnabled": store.inventory_sync_enabled,
            "pricingSyncEnabled": store.pricing_sync_enabled,
            "orderSyncEnabled": store.order_sync_enabled,
        }

    async def mark_synced(self, store_id: uuid.UUID, *, error: str | None = None) -> None:
        store = await self.stores.get_by_id_or_raise(store_id)
        now = datetime.now(UTC)
        store.last_sync_at = now
        store.last_activity_at = now
        if error:
            store.last_error = error[:1024]
            store.status = StoreStatus.ERROR
            store.health_score = max(0, store.health_score - 10)
        else:
            store.last_error = None
            if store.status is StoreStatus.SYNCING:
                store.status = StoreStatus.CONNECTED
            store.health_score = min(100, store.health_score + 5)
        await self.session.flush()

    def decrypt_credentials(self, store: Store) -> dict[str, str] | None:
        """Decrypt for outbound calls only — never for API responses."""
        if not store.encrypted_credentials:
            return None
        raw = decrypt(store.encrypted_credentials)
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        return {str(k): str(v) for k, v in data.items()}


# Silence unused import lint if platform enum is only used via schema
_ = StorePlatform
