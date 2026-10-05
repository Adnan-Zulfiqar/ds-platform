"""Celery tasks for Shopify channel sync."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterable
from typing import Any

from app.core.context import clear_context, set_tenant_id
from app.core.logging import get_logger
from app.database.session import transaction
from app.integrations.shopify.sync import ShopifySyncService
from app.models.store import StorePlatform
from app.repositories.shopify import ShopifyMaintenanceRepository, StoreListingRepository
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


@celery_app.task(base=BaseTask, bind=True, name="shopify.push_price_quantity")
def push_price_quantity(self: Any, tenant_id: str, product_id: str, **_: Any) -> dict[str, Any]:
    """Send one product's current price and stock to each of its Shopify
    listings. This is the Shopify leg of the channel fan-out; until it
    existed a supplier or pricing change reached eBay and WooCommerce but
    never Shopify.

    Idempotent: Shopify receives absolute values. A store with no listing for
    the product costs one indexed query and no outbound call.
    """
    set_tenant_id(uuid.UUID(tenant_id))
    try:

        async def _run() -> dict[str, Any]:
            async with transaction() as session:
                listings = await StoreListingRepository(session).list_for_product_on_platform(
                    uuid.UUID(product_id), StorePlatform.SHOPIFY
                )
                service = ShopifySyncService(session)
                pushed = 0
                for listing in listings:
                    await service.push_price(
                        store_id=listing.store_id, product_id=listing.product_id
                    )
                    await service.push_inventory(
                        store_id=listing.store_id, product_id=listing.product_id
                    )
                    pushed += 1
                return {"listings": len(listings), "pushed": pushed}

        return asyncio.run(_run())
    finally:
        clear_context()


def enqueue_price_quantity(tenant_id: uuid.UUID, product_ids: Iterable[uuid.UUID]) -> int:
    """Queue a push per product, after the change has committed. A broker
    outage is logged and swallowed, as for eBay: the edit itself is saved."""
    queued = 0
    for product_id in dict.fromkeys(product_ids):
        try:
            push_price_quantity.delay(str(tenant_id), str(product_id))
            queued += 1
        except Exception as exc:  # broker down: the edit is saved; see docstring
            logger.warning(
                "shopify_price_quantity_enqueue_failed",
                product_id=str(product_id),
                error=type(exc).__name__,
            )
    return queued


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
