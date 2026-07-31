"""Inventory synchronisation Celery tasks."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import sqlalchemy as sa

from app.core.context import clear_context, set_tenant_id
from app.core.logging import get_logger
from app.database.session import transaction
from app.models.integration import AliExpressConnection, IntegrationStatus
from app.models.order import SyncTrigger
from app.services.inventory_sync import InventorySyncService
from app.workers.base import BaseTask
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


async def _tenant_ids() -> list[uuid.UUID]:
    async with transaction() as session:
        result = await session.execute(
            sa.select(AliExpressConnection.tenant_id).where(
                AliExpressConnection.status == IntegrationStatus.CONNECTED
            )
        )
        return list(result.scalars().all())


async def _sync_tenant(tenant_id: uuid.UUID) -> dict[str, Any]:
    set_tenant_id(tenant_id)
    try:
        async with transaction() as session:
            run = await InventorySyncService(session).sync(trigger=SyncTrigger.SCHEDULED)
            return {
                "tenant_id": str(tenant_id),
                "seen": run.products_seen,
                "changed": run.products_changed,
                "status": run.status.value,
            }
    finally:
        clear_context()


@celery_app.task(base=BaseTask, bind=True, name="inventory.sync")
def sync_inventory(self: Any, **_: Any) -> dict[str, Any]:
    """Fan out inventory sync to every connected tenant."""
    tenant_ids = asyncio.run(_tenant_ids())
    for tenant_id in tenant_ids:
        sync_inventory_one.delay(str(tenant_id))
    return {"enqueued": len(tenant_ids)}


@celery_app.task(base=BaseTask, bind=True, name="inventory.sync_one")
def sync_inventory_one(self: Any, tenant_id: str, **_: Any) -> dict[str, Any]:
    return asyncio.run(_sync_tenant(uuid.UUID(tenant_id)))
