"""Daily analytics aggregation."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import sqlalchemy as sa

from app.core.context import clear_context, set_tenant_id
from app.database.session import transaction
from app.models.tenant import Tenant, TenantStatus
from app.services.analytics_service import AnalyticsService
from app.workers.base import BaseTask
from app.workers.celery_app import celery_app


async def _active_tenants() -> list[uuid.UUID]:
    async with transaction() as session:
        result = await session.execute(
            sa.select(Tenant.id).where(Tenant.status.in_((TenantStatus.ACTIVE, TenantStatus.TRIAL)))
        )
        return list(result.scalars().all())


async def _aggregate(tenant_id: uuid.UUID) -> None:
    set_tenant_id(tenant_id)
    try:
        async with transaction() as session:
            await AnalyticsService(session).aggregate_day()
    finally:
        clear_context()


@celery_app.task(base=BaseTask, bind=True, name="analytics.aggregate")
def aggregate(self: Any, **_: Any) -> dict[str, Any]:
    tenant_ids = asyncio.run(_active_tenants())
    for tenant_id in tenant_ids:
        aggregate_one.delay(str(tenant_id))
    return {"enqueued": len(tenant_ids)}


@celery_app.task(base=BaseTask, bind=True, name="analytics.aggregate_one")
def aggregate_one(self: Any, tenant_id: str, **_: Any) -> bool:
    asyncio.run(_aggregate(uuid.UUID(tenant_id)))
    return True
