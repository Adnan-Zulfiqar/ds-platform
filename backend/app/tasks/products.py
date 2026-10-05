"""Catalogue synchronisation tasks.

Refreshing a product's price, stock and variants from its supplier. This is the
inventory and price synchronisation foundation: the mechanism exists and runs,
but nothing schedules it yet.

**Follows the Phase 3 task pattern exactly** — see
``app.tasks.integrations.aliexpress``. No new queue abstraction is introduced,
because the existing one already handles the two hard parts: binding tenant
context per unit of work, and giving each unit its own event loop and session so
one failure cannot abort a sweep.

Refreshing *is* importing again. ``ProductImportService.import_product`` is
idempotent and preserves a product's status, so the sync task reuses it rather
than growing a parallel implementation that could drift.

> **Never executed.** No broker is available on the development machine —
> RabbitMQ is not running and no worker has been started — so these tasks are
> registered and unit-tested but have never run under Celery. That is recorded
> in ``TECHNICAL_DEBT.md`` rather than implied to work.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import clear_context, set_tenant_id
from app.core.logging import get_logger
from app.database.session import transaction
from app.integrations.aliexpress.exceptions import AliExpressError
from app.models.product import Product, ProductStatus, ProductVariant
from app.services.product_import import ProductImportService
from app.tasks.integrations import channels
from app.workers.base import BaseTask
from app.workers.celery_app import celery_app

logger = get_logger(__name__)

#: How stale a product must be before a sweep refreshes it.
#:
#: Twelve hours is a starting point, not a measured value. Supplier prices and
#: stock move on their own schedule, and the right interval is whatever keeps a
#: listing from being sold at a price that no longer exists. It is a constant
#: rather than a setting until there is evidence for a number worth configuring.
_STALE_AFTER = timedelta(hours=12)

#: Ceiling on one sweep. A tenant with ten thousand products must not enqueue
#: ten thousand supplier calls in one pass — the outbound limiter would shed
#: most of them, and the retries would arrive together.
_SWEEP_LIMIT = 200


async def _sync_one(product_id: uuid.UUID, tenant_id: uuid.UUID) -> bool:
    """Refresh a single product inside its own transaction.

    Tenant context is bound here rather than at the sweep level, because the
    sweep runs on no tenant's behalf while each product belongs to exactly one.
    The ``finally`` clears it: worker processes reuse threads, and a leaked
    tenant id would scope the next product's queries to the wrong customer.
    """
    set_tenant_id(tenant_id)
    try:
        async with transaction() as session:
            service = ProductImportService(session)
            product = await service.products.get_by_id(product_id)

            if product is None:
                # Deleted, or belonging to a different tenant than the sweep
                # recorded. Acting on a mismatch would be acting across a
                # boundary, so it is skipped rather than reconciled.
                logger.info("product_sync_skipped", product_id=str(product_id))
                return False

            before = await price_stock_snapshot(session, product_id)
            await service.import_product(
                external_id=product.external_id,
                ship_to_country=product.import_ship_to_country or product.ship_to_country,
            )
            after = await price_stock_snapshot(session, product_id)
        # Committed. Only a real movement is pushed: the sweep refreshes every
        # stale product, and a push per unchanged product would be an
        # outbound call per listing every twelve hours for nothing.
        if after != before:
            channels.enqueue_price_quantity(tenant_id, [product_id])
        return True
    finally:
        clear_context()


async def price_stock_snapshot(
    session: AsyncSession, product_id: uuid.UUID
) -> tuple[tuple[Any, ...], ...]:
    """What a channel would receive for this product: the product's own
    price and stock, then each live variant's. Two indexed reads."""
    product = (
        await session.execute(
            sa.select(Product.sell_price, Product.stock_quantity).where(Product.id == product_id)
        )
    ).one_or_none()
    variants = (
        await session.execute(
            sa.select(
                ProductVariant.external_variant_id,
                ProductVariant.sell_price,
                ProductVariant.list_price,
                ProductVariant.stock_quantity,
                ProductVariant.is_enabled,
            )
            .where(ProductVariant.product_id == product_id, ProductVariant.deleted_at.is_(None))
            .order_by(ProductVariant.external_variant_id)
        )
    ).all()
    return (tuple(product) if product else (), *(tuple(row) for row in variants))


@celery_app.task(
    base=BaseTask,
    bind=True,
    name="products.sync_one",
    soft_time_limit=120,
    time_limit=180,
)
def sync_product(self: Any, product_id: str, tenant_id: str, **_: Any) -> bool:
    """Refresh one product from its supplier.

    Idempotent, which ``task_acks_late`` requires: running it twice is
    indistinguishable from running it once, because the underlying import
    upserts on ``(tenant_id, source, external_id)``.

    A product that has been delisted upstream marks itself unavailable rather
    than failing the task. Delisting is normal catalogue churn, not an error,
    and retrying it three times with backoff would be three wasted supplier
    calls per dead product.
    """
    try:
        return asyncio.run(_sync_one(uuid.UUID(product_id), uuid.UUID(tenant_id)))
    except AliExpressError as exc:
        if not exc.retryable:
            logger.warning(
                "product_sync_permanent_failure",
                product_id=product_id,
                error=type(exc).__name__,
            )
            asyncio.run(_mark_unavailable(uuid.UUID(product_id), uuid.UUID(tenant_id), str(exc)))
            return False
        raise


async def _mark_unavailable(product_id: uuid.UUID, tenant_id: uuid.UUID, reason: str) -> None:
    """Record that a product can no longer be fetched.

    The product is kept. A tenant may have it listed on a sales channel, and
    deleting the row would leave that listing pointing at nothing — a visible
    status is far easier to act on than a missing record.
    """
    set_tenant_id(tenant_id)
    try:
        async with transaction() as session:
            service = ProductImportService(session)
            product = await service.products.get_by_id(product_id)
            if product is None:
                return
            product.status = ProductStatus.UNAVAILABLE
            product.last_sync_error = reason[:1024]
            product.last_synced_at = datetime.now(UTC)
    finally:
        clear_context()


async def _find_stale(limit: int) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Find products due a refresh, across every tenant.

    **Deliberately unscoped**, like the connection sweep it mirrors: it runs on
    no tenant's behalf. The ids are materialised before the session closes so no
    detached ORM instance escapes the transaction, and each is re-read under its
    own tenant context by ``_sync_one``.
    """
    cutoff = datetime.now(UTC) - _STALE_AFTER

    async with transaction() as session:
        result = await session.execute(
            sa.select(Product.id, Product.tenant_id)
            .where(
                Product.deleted_at.is_(None),
                Product.status != ProductStatus.ARCHIVED,
                sa.or_(Product.last_synced_at.is_(None), Product.last_synced_at < cutoff),
            )
            .order_by(Product.last_synced_at.asc().nullsfirst())
            .limit(limit)
        )
        return [(row[0], row[1]) for row in result.all()]


@celery_app.task(
    base=BaseTask,
    bind=True,
    name="products.sweep_stale",
    soft_time_limit=300,
    time_limit=360,
)
def sweep_stale_products(self: Any, limit: int = _SWEEP_LIMIT, **_: Any) -> int:
    """Queue a refresh for every product whose data has gone stale.

    Fans out rather than syncing inline: one slow supplier response must not
    hold the sweep, and a per-product task gets its own retry budget.

    Archived products are excluded — nobody is selling them, so spending a
    supplier call on them is waste.
    """
    stale = asyncio.run(_find_stale(limit))

    for product_id, tenant_id in stale:
        sync_product.delay(product_id=str(product_id), tenant_id=str(tenant_id))

    logger.info("product_sweep_queued", count=len(stale))
    return len(stale)


__all__ = ["sweep_stale_products", "sync_product"]
