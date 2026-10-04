"""Billing background tasks (Track E6b): one free trial per store."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import event

from app.core.context import clear_context, require_tenant_id, set_tenant_id
from app.core.logging import get_logger
from app.database.session import transaction
from app.repositories.billing import TenantSubscriptionRepository, TrialFingerprintRegistry
from app.workers.base import BaseTask
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


def store_fingerprint(platform: str, identity: str) -> str:
    """One-way: the registry never holds a shop domain or a seller id."""
    return hashlib.sha256(f"{platform}:{identity.strip().lower()}".encode()).hexdigest()


async def _claim(tenant_id: uuid.UUID, fingerprint: str) -> bool:
    """True when this workspace's trial was ended because the store had
    already given a trial to another workspace."""
    async with transaction() as session:
        if not await TrialFingerprintRegistry(session).claim(fingerprint, tenant_id):
            return False
        set_tenant_id(tenant_id)
        try:
            from app.services.billing import BillingService

            service = BillingService(session)
            row = await service._row(lock=True)
            now = datetime.now(UTC)
            if row.status in {"active", "trialing", "past_due"} or row.trial_ends_at <= now:
                return False  # paying, or the trial is already over
            await TenantSubscriptionRepository(session).update(row, trial_ends_at=now)
            logger.info("billing_trial_ended_store_reused", tenant_id=str(tenant_id))
            return True
        finally:
            clear_context()


@celery_app.task(base=BaseTask, bind=True, name="billing.claim_trial")
def claim_trial(self: Any, tenant_id: str, fingerprint: str, **_: Any) -> dict[str, Any]:
    """Idempotent: claiming the same fingerprint twice changes nothing."""
    ended = asyncio.run(_claim(uuid.UUID(tenant_id), fingerprint))
    return {"trial_ended": ended}


def claim_trial_after_commit(session: Any, *, platform: str, identity: str) -> None:
    """Queue the claim once the store connection has committed."""
    if not identity:
        return
    tenant_id = str(require_tenant_id())
    fingerprint = store_fingerprint(platform, identity)

    def on_commit(_session: object) -> None:
        try:
            claim_trial.delay(tenant_id, fingerprint)
        except Exception as exc:  # broker down: the connection itself is saved
            logger.warning("billing_claim_trial_enqueue_failed", error=type(exc).__name__)

    event.listen(session.sync_session, "after_commit", on_commit, once=True)


__all__ = ["claim_trial", "claim_trial_after_commit", "store_fingerprint"]
