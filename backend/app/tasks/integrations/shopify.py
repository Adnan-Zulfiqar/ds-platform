"""Celery tasks for Shopify channel sync."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from app.core.context import clear_context, set_tenant_id
from app.core.logging import get_logger
from app.database.session import transaction
from app.integrations.shopify.sync import ShopifySyncService
from app.repositories.shopify import ShopifyMaintenanceRepository
from app.workers.base import BaseTask
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


async def _connected_pairs() -> list[tuple[uuid.UUID, uuid.UUID]]:
    async with transaction() as session:
        rows = await ShopifyMaintenanceRepository(session).list_connected()
        return [(row.tenant_id, row.store_id) for row in rows]


@celery_app.task(base=BaseTask, bind=True, name="shopify.sync_orders_all")
def sync_orders_all(self: Any, **_: Any) -> dict[str, Any]:
    pairs = asyncio.run(_connected_pairs())
    for tenant_id, store_id in pairs:
        sync_orders_one.delay(str(tenant_id), str(store_id))
    return {"queued": len(pairs)}


@celery_app.task(base=BaseTask, bind=True, name="shopify.sync_orders_one")
def sync_orders_one(self: Any, tenant_id: str, store_id: str, **_: Any) -> dict[str, Any]:
    set_tenant_id(uuid.UUID(tenant_id))
    try:

        async def _run() -> dict[str, Any]:
            async with transaction() as session:
                return await ShopifySyncService(session).import_orders(store_id=uuid.UUID(store_id))

        return asyncio.run(_run())
    finally:
        clear_context()


@celery_app.task(base=BaseTask, bind=True, name="shopify.publish_product")
def publish_product(
    self: Any, tenant_id: str, store_id: str, product_id: str, **_: Any
) -> dict[str, Any]:
    set_tenant_id(uuid.UUID(tenant_id))
    try:

        async def _run() -> dict[str, Any]:
            async with transaction() as session:
                return await ShopifySyncService(session).publish_product(
                    store_id=uuid.UUID(store_id),
                    product_id=uuid.UUID(product_id),
                )

        return asyncio.run(_run())
    finally:
        clear_context()


@celery_app.task(base=BaseTask, bind=True, name="shopify.push_inventory")
def push_inventory(
    self: Any, tenant_id: str, store_id: str, product_id: str, **_: Any
) -> dict[str, Any]:
    set_tenant_id(uuid.UUID(tenant_id))
    try:

        async def _run() -> dict[str, Any]:
            async with transaction() as session:
                return await ShopifySyncService(session).push_inventory(
                    store_id=uuid.UUID(store_id),
                    product_id=uuid.UUID(product_id),
                )

        return asyncio.run(_run())
    finally:
        clear_context()


@celery_app.task(base=BaseTask, bind=True, name="shopify.push_price")
def push_price(
    self: Any, tenant_id: str, store_id: str, product_id: str, **_: Any
) -> dict[str, Any]:
    set_tenant_id(uuid.UUID(tenant_id))
    try:

        async def _run() -> dict[str, Any]:
            async with transaction() as session:
                return await ShopifySyncService(session).push_price(
                    store_id=uuid.UUID(store_id),
                    product_id=uuid.UUID(product_id),
                )

        return asyncio.run(_run())
    finally:
        clear_context()
