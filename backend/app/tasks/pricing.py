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
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

import sqlalchemy as sa

from app.core.context import clear_context, set_tenant_id
from app.core.logging import get_logger
from app.database.session import dispose_engine, transaction
from app.models.rule_application import ApplicationStatus, RuleApplication
from app.models.tenant import Tenant, TenantStatus
from app.repositories.global_rules import RuleApplicationTenantLookup
from app.schemas.pricing import PricingApplyRequest
from app.services.pricing_engine import PricingEngine
from app.services.rule_application import (
    MAX_RECOVERIES,
    STALE_AFTER,
    ClaimResult,
    RuleApplicationService,
)
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

#: Stale runs reconciled per sweep. A bound, not a target: if hundreds are
#: stale something is badly wrong, and requeuing them all at once would turn a
#: worker outage into a thundering herd on recovery.
_RECONCILE_LIMIT = 50

#: How long a `pending` run may sit before the sweep republishes it. Long
#: enough that the ordinary path -- publish immediately after commit -- is
#: never raced, short enough that a merchant is not left watching a queue that
#: nothing is listening to.
PENDING_GRACE = timedelta(minutes=2)


def _run(coro: Coroutine[Any, Any, _T]) -> _T:
    """Execute an async unit of work from a synchronous Celery task.

    A worker process has no running event loop, so ``asyncio.run`` is the
    correct call there. It is behind a named function rather than inline so
    that the integration suite -- which *does* have a running loop, and a
    transaction it must stay inside -- can drive these tasks through their
    real entry points instead of testing the service underneath and calling
    the queue verified.

    **The engine is disposed before the loop closes.** ``asyncio.run`` builds
    a fresh loop per invocation, while ``app.database.session.engine`` is a
    module-level singleton whose pooled connections are bound to the loop that
    opened them. The second task in a worker therefore picks a connection
    belonging to a loop that no longer exists and fails with
    ``'NoneType' object has no attribute 'send'`` -- which reads as a database
    error and is not one. Disposing here hands the next invocation an empty
    pool. The cost is one connection setup per task, which is nothing beside a
    task that opens a transaction per batch anyway.

    Found by running a real worker against a real broker: the first message
    succeeded and every message after it retried.
    """

    async def with_cleanup() -> _T:
        try:
            return await coro
        finally:
            await dispose_engine()

    return asyncio.run(with_cleanup())


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


def publish_rule_application(application_id: uuid.UUID) -> bool:
    """Publish one application to the broker. Synchronous, and safe to fail.

    Called from a SQLAlchemy ``after_commit`` hook, which is the only moment
    that genuinely means "the row is durable now".

    An earlier version ran this as a FastAPI background task on the assumption
    that yield-dependency teardown -- and therefore the commit -- happened
    first. It does not: Starlette awaits background tasks inside the response
    call, which is still within the dependency's scope. The result was a
    worker looking up a row its own transaction had not yet written, a
    `NotFoundError` escaping after the response had started, and the whole
    request rolling back -- a `202` handed out for an application that never
    existed. Found by running it against a real worker.

    Failure here is deliberately not fatal and deliberately not reported to
    the caller: the row is committed, and ``reconcile_applications`` republishes
    any `pending` run the broker never accepted. That sweep covers strictly
    more than an inline error path could -- a broker outage, a process killed
    between commit and publish, a message lost in transit -- because it looks
    at the durable state rather than at what one request happened to observe.
    """
    try:
        enqueue(apply_rules_to_drafts, application_id=str(application_id))
    except Exception as exc:
        logger.error(
            "rule_application_publish_failed",
            application_id=str(application_id),
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return False
    logger.info("rule_application_published", application_id=str(application_id))
    return True


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


# ---------------------------------------------------------------------------
# Reconciling runs whose worker died (M3A-4B)
# ---------------------------------------------------------------------------


async def _stale_applications() -> list[tuple[uuid.UUID, uuid.UUID, int]]:
    """Runs still `running` whose heartbeat has gone quiet.

    Read unscoped on purpose -- the sweep runs on no tenant's behalf, exactly
    like ``products.sweep_stale`` -- and returns the tenant alongside each id
    so every action taken afterwards is bound to the tenant recorded on the
    row rather than to anything a caller supplied.
    """
    cutoff = datetime.now(UTC) - STALE_AFTER
    async with transaction() as session:
        rows = await session.execute(
            sa.select(
                RuleApplication.id,
                RuleApplication.tenant_id,
                RuleApplication.recovery_count,
            )
            .where(RuleApplication.status == ApplicationStatus.RUNNING)
            .where(RuleApplication.heartbeat_at < cutoff)
            .order_by(RuleApplication.heartbeat_at.asc())
            .limit(_RECONCILE_LIMIT)
        )
        return [(row[0], row[1], row[2]) for row in rows.all()]


async def _unpublished_applications() -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Accepted runs the broker never took.

    This is the other half of the dual-write problem: the row commits and then
    the publish fails, or the process dies between the two. Rather than trying
    to make those two writes atomic -- which they cannot be -- the state is
    reconciled afterwards. Republishing is safe: a duplicate message finds the
    run no longer `pending` and does nothing.

    The grace period keeps this from racing the normal path, where the message
    is published moments after the commit.
    """
    cutoff = datetime.now(UTC) - PENDING_GRACE
    async with transaction() as session:
        rows = await session.execute(
            sa.select(RuleApplication.id, RuleApplication.tenant_id)
            .where(RuleApplication.status == ApplicationStatus.PENDING)
            .where(RuleApplication.created_at < cutoff)
            .order_by(RuleApplication.created_at.asc())
            .limit(_RECONCILE_LIMIT)
        )
        return [(row[0], row[1]) for row in rows.all()]


async def _republish(application_id: uuid.UUID, tenant_id: uuid.UUID) -> str:
    """Re-publish a pending run and record that it needed rescuing."""
    set_tenant_id(tenant_id)
    try:
        if not publish_rule_application(application_id):
            return "skipped"
        async with transaction() as session:
            await RuleApplicationService(session).mark_enqueued(application_id)
        logger.warning("rule_application_republished", application_id=str(application_id))
        return "republished"
    finally:
        clear_context()


async def _reconcile_one(application_id: uuid.UUID, tenant_id: uuid.UUID, recoveries: int) -> str:
    """Reclaim one abandoned run, or park it if it has failed too often."""
    set_tenant_id(tenant_id)
    try:
        if recoveries >= MAX_RECOVERIES:
            async with transaction() as session:
                parked = await RuleApplicationService(session).abandon_stale(application_id)
            return "abandoned" if parked else "skipped"

        async with transaction() as session:
            reclaimed = await RuleApplicationService(session).reclaim_stale(
                application_id, task_id=None
            )
        if not reclaimed:
            # The heartbeat moved between the sweep and the write: the worker
            # is alive after all. Nothing to do, and nothing was taken from it.
            return "healthy"

        # Re-queued rather than executed here: the reconciler's job is to
        # notice, not to become a second execution path with its own timeouts
        # and its own bugs. The run resumes from the durable cursor, so the
        # batches that already committed are not repeated.
        enqueue(apply_rules_to_drafts, application_id=str(application_id))
        logger.warning(
            "rule_application_reclaimed",
            application_id=str(application_id),
            recovery_count=recoveries + 1,
        )
        return "requeued"
    finally:
        clear_context()


@celery_app.task(base=BaseTask, bind=True, name="pricing.reconcile_applications")
def reconcile_applications(self: Any, **_: Any) -> dict[str, int]:
    """Recover bulk applications abandoned by a crashed worker.

    A worker killed mid-run leaves its application `running` forever: nothing
    finishes it, and the merchant watches a progress bar that will never move.
    ``task_acks_late`` redelivers the *message*, but a worker that died between
    batches may never have had its message redelivered at all -- and a run
    cancelled at the broker has no message left to redeliver.

    The heartbeat is what makes "abandoned" a fact rather than a guess, and the
    reclaim is a conditional UPDATE against that heartbeat, so a worker that is
    merely slow keeps its run: this task's write simply matches no row.

    It also republishes `pending` runs the broker never accepted, which is the
    other half of the same problem: the row and the message cannot be written
    atomically, so the gap is reconciled afterwards rather than pretended away.

    Cancelled and finished runs are never touched -- only `running` and
    `pending` rows are eligible -- and resuming is safe because the durable
    cursor and the per-item uniqueness constraint already prevent a re-entered
    run from repricing anything twice.
    """
    outcomes = {
        "requeued": 0,
        "abandoned": 0,
        "healthy": 0,
        "skipped": 0,
        "republished": 0,
    }
    for application_id, tenant_id, recoveries in _run(_stale_applications()):
        result = _run(_reconcile_one(application_id, tenant_id, recoveries))
        outcomes[result] = outcomes.get(result, 0) + 1
    for application_id, tenant_id in _run(_unpublished_applications()):
        result = _run(_republish(application_id, tenant_id))
        outcomes[result] = outcomes.get(result, 0) + 1
    if outcomes["requeued"] or outcomes["abandoned"] or outcomes["republished"]:
        logger.warning("rule_application_reconcile_summary", **outcomes)
    return outcomes
