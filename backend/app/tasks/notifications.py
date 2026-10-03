"""Notification cleanup Celery tasks."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import sqlalchemy as sa

from app.core.context import clear_context, set_tenant_id
from app.database.session import transaction
from app.models.tenant import Tenant, TenantStatus
from app.services.notification_email import NotificationEmailService
from app.services.notification_service import NotificationService
from app.workers.base import BaseTask
from app.workers.celery_app import celery_app


async def _active_tenants() -> list[uuid.UUID]:
    async with transaction() as session:
        result = await session.execute(
            sa.select(Tenant.id).where(Tenant.status.in_((TenantStatus.ACTIVE, TenantStatus.TRIAL)))
        )
        return list(result.scalars().all())


async def _cleanup(tenant_id: uuid.UUID) -> int:
    set_tenant_id(tenant_id)
    try:
        async with transaction() as session:
            return await NotificationService(session).cleanup(older_than_days=90)
    finally:
        clear_context()


@celery_app.task(base=BaseTask, bind=True, name="cleanup.old_notifications")
def cleanup_old_notifications(self: Any, **_: Any) -> dict[str, Any]:
    tenant_ids = asyncio.run(_active_tenants())
    total = 0
    for tenant_id in tenant_ids:
        total += asyncio.run(_cleanup(tenant_id))
    return {"tenants": len(tenant_ids), "purged": total}


async def _deliver(tenant_id: uuid.UUID) -> int:
    set_tenant_id(tenant_id)
    try:
        async with transaction() as session:
            outcome = await NotificationEmailService(session).deliver_pending(limit=50)
            return outcome.sent
    finally:
        clear_context()


@celery_app.task(base=BaseTask, bind=True, name="notifications.send_emails")
def send_notification_emails(self: Any, **_: Any) -> dict[str, Any]:
    """Track E3: email pending notifications, workspace by workspace. Each
    workspace commits on its own, so one failing does not hold the others."""
    tenant_ids = asyncio.run(_active_tenants())
    sent = 0
    for tenant_id in tenant_ids:
        sent += asyncio.run(_deliver(tenant_id))
    return {"tenants": len(tenant_ids), "sent": sent}
