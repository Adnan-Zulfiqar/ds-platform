"""Inventory synchronisation.

Reuses ``ProductImportService.import_product`` so stock refresh cannot drift
from catalogue import. Deltas are recorded only when the quantity actually
changes — a no-op re-fetch must not flood the history table.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError
from app.core.logging import get_logger
from app.integrations.aliexpress.exceptions import AliExpressError
from app.models.inventory import InventoryChangeReason, InventorySyncRun
from app.models.notification import NotificationKind
from app.models.order import SyncRunStatus, SyncTrigger
from app.models.product import Product
from app.repositories.inventory import InventoryChangeRepository, InventorySyncRunRepository
from app.repositories.product import ProductRepository
from app.schemas.common import ListQueryParams
from app.services.base import BaseService
from app.services.notification_service import NotificationService
from app.services.product_import import ProductImportService

logger = get_logger(__name__)


class InventorySyncService(BaseService):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.runs = InventorySyncRunRepository(session)
        self.changes = InventoryChangeRepository(session)
        self.products = ProductRepository(session)
        self.imports = ProductImportService(session)
        self.notifications = NotificationService(session)
        #: Products whose stock moved in the last ``sync`` — EBAY-C4 pushes
        #: these to eBay once the caller's transaction has committed.
        self.changed_product_ids: list[uuid.UUID] = []

    async def sync(
        self,
        *,
        store_id: uuid.UUID | None = None,
        product_id: uuid.UUID | None = None,
        trigger: SyncTrigger = SyncTrigger.MANUAL,
        requested_by_user_id: uuid.UUID | None = None,
        limit: int = 200,
    ) -> InventorySyncRun:
        running = await self._find_running()
        if running is not None:
            raise ConflictError("An inventory sync is already running.")

        now = datetime.now(UTC)
        run = await self.runs.create(
            store_id=store_id,
            product_id=product_id,
            trigger=trigger,
            status=SyncRunStatus.RUNNING,
            started_at=now,
            requested_by_user_id=requested_by_user_id,
        )
        await self.session.flush()

        try:
            targets = await self._targets(store_id=store_id, product_id=product_id, limit=limit)
            seen = 0
            changed = 0
            for product in targets:
                before = product.stock_quantity
                try:
                    refreshed = await self.imports.import_product(
                        external_id=product.external_id,
                        requested_by_user_id=requested_by_user_id,
                        ship_to_country=(product.import_ship_to_country or product.ship_to_country),
                    )
                except AliExpressError as exc:
                    logger.warning(
                        "inventory_sync_product_failed",
                        product_id=str(product.id),
                        error=type(exc).__name__,
                    )
                    product.last_sync_error = str(exc)[:1024]
                    continue

                seen += 1
                if refreshed.stock_quantity != before:
                    await self.changes.create(
                        sync_run_id=run.id,
                        product_id=refreshed.id,
                        store_id=store_id or refreshed.store_id,
                        previous_quantity=before,
                        new_quantity=refreshed.stock_quantity,
                        reason=InventoryChangeReason.SUPPLIER_SYNC,
                    )
                    changed += 1
                    self.changed_product_ids.append(refreshed.id)

            run.products_seen = seen
            run.products_changed = changed
            run.status = SyncRunStatus.SUCCEEDED
            run.finished_at = datetime.now(UTC)
            await self.session.flush()

            if changed:
                await self.notifications.notify(
                    kind=NotificationKind.INVENTORY_CHANGED,
                    title=f"Inventory changed on {changed} product(s)",
                    body=f"Synced {seen} product(s); {changed} stock level(s) moved.",
                    href="/inventory",
                    payload={"seen": seen, "changed": changed, "runId": str(run.id)},
                )
            return run
        except Exception as exc:
            run.status = SyncRunStatus.FAILED
            run.error_code = type(exc).__name__
            run.error_message = str(exc)[:1024]
            run.finished_at = datetime.now(UTC)
            await self.session.flush()
            await self.notifications.notify(
                kind=NotificationKind.SYNC_FAILED,
                title="Inventory sync failed",
                body=run.error_message or "Unknown error",
                href="/inventory",
                payload={"runId": str(run.id)},
            )
            raise

    async def list_runs(self, params: ListQueryParams) -> tuple[list[InventorySyncRun], int]:
        rows, total = await self.runs.list(params)
        return list(rows), total

    async def list_changes(self, params: ListQueryParams) -> tuple[list[Any], int]:
        rows, total = await self.changes.list(params)
        return list(rows), total

    async def _targets(
        self,
        *,
        store_id: uuid.UUID | None,
        product_id: uuid.UUID | None,
        limit: int,
    ) -> list[Product]:
        if product_id is not None:
            product = await self.products.get_by_id_or_raise(product_id)
            return [product]
        return await self.products.list_for_inventory(store_id=store_id, limit=limit)

    async def _find_running(self) -> InventorySyncRun | None:
        rows, _ = await self.runs.list(
            ListQueryParams(page=1, size=1),
            filters={"status": SyncRunStatus.RUNNING},
        )
        return rows[0] if rows else None
