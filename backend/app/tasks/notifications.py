"""Notification cleanup Celery tasks."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy import event

from app.core.context import clear_context, set_tenant_id
from app.core.logging import get_logger
from app.database.session import transaction
from app.models.notification import NotificationKind
from app.models.tenant import Tenant, TenantStatus
from app.repositories.notification import NotificationRepository
from app.services.notification_email import NotificationEmailService
from app.services.notification_service import NotificationService
from app.workers.base import BaseTask
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


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


async def _broadcast_one(tenant_id: uuid.UUID, broadcast_id: str, title: str, body: str) -> bool:
    """One workspace's copy. Idempotent: ``task_acks_late`` can run the task
    twice, and a second run finds the first copy by its broadcast id."""
    set_tenant_id(tenant_id)
    try:
        async with transaction() as session:
            notifications = NotificationRepository(session)
            if await notifications.has_payload("broadcast_id", broadcast_id):
                return False
            await notifications.create(
                kind=NotificationKind.INFO,
                title=title,
                body=body,
                payload={"broadcast_id": broadcast_id},
            )
            return True
    finally:
        clear_context()


@celery_app.task(base=BaseTask, bind=True, name="notifications.broadcast")
def broadcast(self: Any, broadcast_id: str, title: str, body: str, **_: Any) -> dict[str, Any]:
    """An operator's message to every active workspace (D-019). Each
    workspace commits on its own, so one failing does not stop the rest."""
    tenant_ids = asyncio.run(_active_tenants())
    created = 0
    for tenant_id in tenant_ids:
        if asyncio.run(_broadcast_one(tenant_id, broadcast_id, title, body)):
            created += 1
    return {"tenants": len(tenant_ids), "created": created}


def queue_broadcast_after_commit(session: Any, broadcast_id: str, title: str, body: str) -> None:
    """Queue the broadcast once the request's transaction (which holds its
    audit row) has committed; a rollback sends nothing."""
    sync = session.sync_session

    def on_commit(_session: object) -> None:
        try:
            broadcast.delay(broadcast_id, title, body)
        except Exception as exc:  # broker down: the audit row says it was asked
            logger.warning("broadcast_enqueue_failed", error=type(exc).__name__)

    event.listen(sync, "after_commit", on_commit, once=True)
