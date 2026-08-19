"""Pricing Celery tasks: scheduled recalculation, and confirmed rule application.

Extends the existing pricing task module rather than adding a second one. That
is not only tidiness -- ``celery_app.conf.imports`` already lists this module,
so a task defined here is registered in every worker without touching the
broker configuration. A task the worker never imports fails at call time with
"unregistered task", which is a confusing error to debug for a feature that
otherwise looks wired.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Coroutine
from typing import Any, TypeVar

import sqlalchemy as sa

from app.core.context import clear_context, set_tenant_id
from app.core.logging import get_logger
from app.database.session import transaction
from app.models.tenant import Tenant, TenantStatus
from app.repositories.global_rules import RuleApplicationTenantLookup
from app.schemas.pricing import PricingApplyRequest
from app.services.pricing_engine import PricingEngine
from app.services.rule_application import ClaimResult, RuleApplicationService
from app.workers.base import BaseTask, enqueue
from app.workers.celery_app import celery_app

logger = get_logger(__name__)

_T = TypeVar("_T")

#: Stop a runaway run rather than loop forever. At ``APPLICATION_BATCH_SIZE``
#: products per batch this is an order of magnitude above the largest
#: selection the API accepts, so reaching it means a bug in the cursor, not a
#: large catalogue -- and a bounded loop turns that bug into a logged failure
#: instead of a worker pinned at 100%.
_MAX_BATCHES = 1_000


def _run(coro: Coroutine[Any, Any, _T]) -> _T:
    """Execute an async unit of work from a synchronous Celery task.

    A worker process has no running event loop, so ``asyncio.run`` is the
    correct call there. It is behind a named function rather than inline so
    that the integration suite -- which *does* have a running loop, and a
    transaction it must stay inside -- can drive these tasks through their
    real entry points instead of testing the service underneath and calling
    the queue verified.
    """
    return asyncio.run(coro)


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


# ---------------------------------------------------------------------------
# Confirmed application of global rules to drafts (M3A-3)
# ---------------------------------------------------------------------------


async def dispatch_rule_application(application_id: uuid.UUID, tenant_id: uuid.UUID) -> bool:
    """Hand a committed application to the queue, and record what happened.

    Called *after* the request transaction commits, never inside it. The
    ordering matters in one direction only: a worker that picks up the message
    before the row is visible would find nothing and give up, whereas a row
    that exists a moment before its message does is simply a `pending` run
    about to start.
    """
    set_tenant_id(tenant_id)
    try:
        try:
            enqueue(apply_rules_to_drafts, application_id=str(application_id))
        except Exception as exc:
            # The broker is unreachable. The row is already committed, so it
            # cannot be rolled back -- it is marked failed instead, because a
            # run left `pending` forever is indistinguishable from one that is
            # merely queued behind a busy worker.
            logger.error(
                "rule_application_enqueue_failed",
                application_id=str(application_id),
                error=str(exc),
                error_type=type(exc).__name__,
            )
            async with transaction() as session:
                await RuleApplicationService(session).mark_enqueue_failed(
                    application_id, type(exc).__name__
                )
            return False

        async with transaction() as session:
            await RuleApplicationService(session).mark_enqueued(application_id)
        logger.info("rule_application_enqueued", application_id=str(application_id))
        return True
    finally:
        clear_context()


async def _resolve_tenant(application_id: uuid.UUID) -> uuid.UUID | None:
    """Read the owning tenant from the row, not from the message.

    See :class:`RuleApplicationTenantLookup` -- taking the tenant from the
    payload would let a forged or stale message run one tenant's rules over
    another's catalogue.
    """
    async with transaction() as session:
        return await RuleApplicationTenantLookup(session).tenant_for(application_id)


async def _apply(application_id: uuid.UUID, task_id: str | None) -> dict[str, Any]:
    tenant_id = await _resolve_tenant(application_id)
    if tenant_id is None:
        # A message can outlive its row. Nothing to do, and nothing to retry.
        logger.warning("rule_application_missing", application_id=str(application_id))
        return {"status": ClaimResult.UNKNOWN.value, "batches": 0}

    set_tenant_id(tenant_id)
    try:
        async with transaction() as session:
            claim = await RuleApplicationService(session).claim(application_id, task_id=task_id)

        if claim not in (ClaimResult.CLAIMED, ClaimResult.RESUMED):
            # Duplicate delivery, another worker's run, a cancellation, or an
            # application that already finished. All four are no-ops.
            logger.info(
                "rule_application_not_claimed",
                application_id=str(application_id),
                reason=claim.value,
            )
            return {"status": claim.value, "batches": 0}

        batches = 0
        while batches < _MAX_BATCHES:
            async with transaction() as session:
                more = await RuleApplicationService(session).run_next_batch(application_id)
            batches += 1
            if not more:
                break

        async with transaction() as session:
            application = await RuleApplicationService(session).finalize(application_id)
            result = {
                "status": application.status.value,
                "batches": batches,
                "applied": application.applied_count,
                "skipped": application.skipped_count,
                "review": application.review_count,
                "failed": application.failed_count,
            }
        logger.info("rule_application_finished", application_id=str(application_id), **result)
        return result
    finally:
        clear_context()


async def _mark_failed(application_id: uuid.UUID, reason: str) -> None:
    tenant_id = await _resolve_tenant(application_id)
    if tenant_id is None:
        return
    set_tenant_id(tenant_id)
    try:
        async with transaction() as session:
            await RuleApplicationService(session).fail(application_id, reason)
    finally:
        clear_context()


@celery_app.task(
    base=BaseTask,
    bind=True,
    name="pricing.apply_rules_to_drafts",
    soft_time_limit=1_500,
    time_limit=1_800,
)
def apply_rules_to_drafts(self: Any, application_id: str, **_: Any) -> dict[str, Any]:
    """Apply confirmed global rules to the drafts an application selected.

    **The payload is one id.** No product list, no rule snapshot, no merchant
    details reach the broker -- everything the run needs is read back from the
    row, which also means a message that sat in a queue over a deploy still
    executes the version of the rules the merchant confirmed against rather
    than a stale copy carried in the message.

    **Idempotent**, as ``task_acks_late`` requires. A second delivery of the
    same message finds the run no longer `pending` and does nothing; a *retry*
    of the same task resumes from the durable cursor, so drafts already
    repriced are not repriced again.

    A run that exhausts its retries is recorded as `failed` with the reason,
    keeping the results of every batch that did commit -- reporting zero would
    send the merchant looking for changes the catalogue already has.
    """
    identifier = uuid.UUID(application_id)
    task_id = getattr(self.request, "id", None)
    try:
        return _run(_apply(identifier, task_id))
    except Exception as exc:
        if self.request.retries >= self.max_retries:
            logger.error(
                "rule_application_exhausted_retries",
                application_id=application_id,
                error=str(exc),
            )
            _run(_mark_failed(identifier, f"{type(exc).__name__}: {exc}"))
            return {"status": "failed", "batches": 0}
        raise
