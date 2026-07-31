"""Pricing recalculation Celery tasks."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import sqlalchemy as sa

from app.core.context import clear_context, set_tenant_id
from app.database.session import transaction
from app.models.tenant import Tenant, TenantStatus
from app.schemas.pricing import PricingApplyRequest
from app.services.pricing_engine import PricingEngine
from app.workers.base import BaseTask
from app.workers.celery_app import celery_app


async def _active_tenants() -> list[uuid.UUID]:
    async with transaction() as session:
        result = await session.execute(
            sa.select(Tenant.id).where(Tenant.status.in_((TenantStatus.ACTIVE, TenantStatus.TRIAL)))
        )
        return list(result.scalars().all())


async def _recalculate(tenant_id: uuid.UUID) -> int:
    set_tenant_id(tenant_id)
    try:
        async with transaction() as session:
            changes = await PricingEngine(session).apply(PricingApplyRequest())
            return len(changes)
    finally:
        clear_context()


@celery_app.task(base=BaseTask, bind=True, name="pricing.recalculate")
def recalculate(self: Any, **_: Any) -> dict[str, Any]:
    tenant_ids = asyncio.run(_active_tenants())
    for tenant_id in tenant_ids:
        recalculate_one.delay(str(tenant_id))
    return {"enqueued": len(tenant_ids)}


@celery_app.task(base=BaseTask, bind=True, name="pricing.recalculate_one")
def recalculate_one(self: Any, tenant_id: str, **_: Any) -> int:
    return asyncio.run(_recalculate(uuid.UUID(tenant_id)))
