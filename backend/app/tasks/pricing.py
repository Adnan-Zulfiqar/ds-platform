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
    ApplicationLease,
    BatchOutcome,
    ClaimResult,
    LeaseObservation,
    RuleApplicationService,
    stale_running_predicate,
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


#: What a worker reports when its run was taken from it. Not a failure: the
#: new owner's outcome is the run's outcome, and this worker wrote nothing.
_SUPERSEDED = "superseded"


class _LeaseHolder:
    """Carries the lease out of ``_apply`` for the task's error path.

    The exception handler lives in the Celery task, one layer above the code
    that obtains ownership, and it must be able to tell "this run is mine and
    it failed" from "this run stopped being mine". Without the lease up there
    the handler can only guess, and guessing wrong marks another worker's
    healthy run as failed.
    """

    __slots__ = ("lease",)

    def __init__(self) -> None:
        self.lease: ApplicationLease | None = None


async def _apply(
    application_id: uuid.UUID, task_id: str | None, holder: _LeaseHolder
) -> dict[str, Any]:
    tenant_id = await _resolve_tenant(application_id)
    if tenant_id is None:
        # A message can outlive its row. Nothing to do, and nothing to retry.
        logger.warning("rule_application_missing", application_id=str(application_id))
        return {"status": ClaimResult.UNKNOWN.value, "batches": 0}

    set_tenant_id(tenant_id)
    try:
        async with transaction() as session:
            claim = await RuleApplicationService(session).claim(application_id, task_id=task_id)

        if claim.lease is None:
            # Duplicate delivery, another worker's run, a cancellation, or an
            # application that already finished. All four are no-ops.
            logger.info(
                "rule_application_not_claimed",
                application_id=str(application_id),
                reason=claim.result.value,
            )
            return {"status": claim.result.value, "batches": 0}

        lease = claim.lease
        holder.lease = lease

        batches = 0
        while batches < _MAX_BATCHES:
            async with transaction() as session:
                step = await RuleApplicationService(session).run_next_batch(
                    application_id, lease=lease
                )
            if step is BatchOutcome.LOST:
                # Reclaimed while this worker was between batches. It wrote
                # nothing in that transaction and must write nothing now --
                # including no failure, no heartbeat and no final status.
                holder.lease = None
                logger.warning(
                    "rule_application_ownership_lost",
                    application_id=str(application_id),
                    task_id=task_id,
                    batches=batches,
                )
                return {"status": _SUPERSEDED, "batches": batches}
            batches += 1
            if step is not BatchOutcome.MORE:
                break

        async with transaction() as session:
            application = await RuleApplicationService(session).finalize(
                application_id, lease=lease
            )
            if application is None:
                holder.lease = None
                logger.warning(
                    "rule_application_ownership_lost",
                    application_id=str(application_id),
                    task_id=task_id,
                    batches=batches,
                )
                return {"status": _SUPERSEDED, "batches": batches}
            result = {
                "status": application.status.value,
                "batches": batches,
                "applied": application.applied_count,
                "skipped": application.skipped_count,
                "review": application.review_count,
                "failed": application.failed_count,
            }
        # The run is closed out; there is nothing left for an error path to
        # mark failed even if something raises on the way out.
        holder.lease = None
        logger.info("rule_application_finished", application_id=str(application_id), **result)
        return result
    finally:
        clear_context()


async def _mark_failed(
    application_id: uuid.UUID, reason: str, lease: ApplicationLease | None
) -> bool:
    """Record a worker's failure -- but only if the worker still owns the run.

    Returns whether the failure was actually recorded, so the task reports
    what happened rather than what it attempted: a worker that was replaced
    and then fell over did not fail the run, and saying it did would be the
    same lie in the return value that the write itself is prevented from
    telling.

    ``lease is None`` covers both "never got ownership" and "lost it", and in
    both cases the honest action is to write nothing: a run this worker does
    not own is either waiting to be claimed by someone else or already being
    processed by them, and stamping `failed` on it would destroy healthy work.
    ``fail`` re-checks under a row lock as well, so a lease that goes stale
    between here and the write is caught by the database rather than by
    timing.
    """
    if lease is None:
        logger.warning(
            "rule_application_failure_not_recorded",
            application_id=str(application_id),
            reason="worker did not hold the lease",
        )
        return False
    tenant_id = await _resolve_tenant(application_id)
    if tenant_id is None:
        return False
    set_tenant_id(tenant_id)
    try:
        async with transaction() as session:
            marked = await RuleApplicationService(session).fail(application_id, reason, lease=lease)
        if marked is None:
            logger.warning(
                "rule_application_failure_not_recorded",
                application_id=str(application_id),
                reason="the lease was no longer current",
            )
            return False
        return True
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
    holder = _LeaseHolder()
    try:
        return _run(_apply(identifier, task_id, holder))
    except Exception as exc:
        if self.request.retries >= self.max_retries:
            logger.error(
                "rule_application_exhausted_retries",
                application_id=application_id,
                error=str(exc),
            )
            # Owner-conditional. A worker whose run was reclaimed mid-flight
            # raises here just as readily as one that genuinely failed, and
            # the difference is the lease.
            recorded = _run(_mark_failed(identifier, f"{type(exc).__name__}: {exc}", holder.lease))
            return {"status": "failed" if recorded else _SUPERSEDED, "batches": 0}
        raise


# ---------------------------------------------------------------------------
# Reconciling runs whose worker died (M3A-4B)
# ---------------------------------------------------------------------------


async def _stale_applications() -> list[LeaseObservation]:
    """Runs still `running` whose heartbeat has gone quiet.

    Read unscoped on purpose -- the sweep runs on no tenant's behalf, exactly
    like ``products.sweep_stale`` -- and returns the tenant alongside each id
    so every action taken afterwards is bound to the tenant recorded on the
    row rather than to anything a caller supplied.

    The heartbeat and lease token come back with each row so the write that
    follows can be conditional on exactly what was seen here. Reading them and
    then writing unconditionally is the classic time-of-check bug, and in this
    case it would steal a run from a worker that woke up in between.

    ``stale_running_predicate`` is shared with the reclaim itself, so the two
    cannot drift into a sweep that keeps selecting rows the write keeps
    refusing.
    """
    async with transaction() as session:
        rows = await session.execute(
            sa.select(
                RuleApplication.id,
                RuleApplication.tenant_id,
                RuleApplication.heartbeat_at,
                RuleApplication.lease_token,
                RuleApplication.recovery_count,
            )
            .where(RuleApplication.status == ApplicationStatus.RUNNING)
            .where(stale_running_predicate())
            .order_by(RuleApplication.heartbeat_at.asc().nullsfirst())
            .limit(_RECONCILE_LIMIT)
        )
        return [
            LeaseObservation(
                application_id=row[0],
                tenant_id=row[1],
                heartbeat_at=row[2],
                lease_token=row[3],
                recovery_count=row[4],
            )
            for row in rows.all()
        ]


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


async def _reconcile_one(observed: LeaseObservation) -> str:
    """Return one abandoned run to the queue, or park it if it keeps dying.

    The sequence matters and was wrong once, so it is spelled out:

    1. **`running` → `pending`, unowned**, conditional on the exact state the
       sweep observed. Committed in its own transaction.
    2. **Publish, after that commit.** A worker that receives the message
       before the row is `pending` would find it `running` under someone else
       and refuse it -- which is precisely what the previous version did, on
       every recovery, silently.
    3. The new worker runs the ordinary `pending -> running` claim and mints a
       lease against its own real Celery task id. Nothing here fabricates one.

    If the publish fails the run stays `pending` with no ``enqueued_at``, which
    is exactly the state ``_unpublished_applications`` exists to repair. That
    is bounded -- one message per sweep, and a duplicate finds the run already
    claimed and does nothing -- so a broker outage delays recovery rather than
    losing it.
    """
    set_tenant_id(observed.tenant_id)
    try:
        if observed.recovery_count >= MAX_RECOVERIES:
            async with transaction() as session:
                parked = await RuleApplicationService(session).abandon_stale(observed=observed)
            return "abandoned" if parked else "healthy"

        async with transaction() as session:
            reclaimed = await RuleApplicationService(session).reclaim_stale(observed=observed)
        if not reclaimed:
            # The heartbeat moved between the sweep and the write, or another
            # reconciler got there first. Either way nothing was taken from a
            # live worker, which is the property this is here to preserve.
            return "healthy"

        logger.warning(
            "rule_application_reclaimed",
            application_id=str(observed.application_id),
            recovery_count=observed.recovery_count + 1,
        )

        # Re-queued rather than executed here: the reconciler's job is to
        # notice, not to become a second execution path with its own timeouts
        # and its own bugs. The run resumes from the durable cursor, so the
        # batches that already committed are not repeated.
        if not publish_rule_application(observed.application_id):
            return "recovered_unpublished"
        async with transaction() as session:
            await RuleApplicationService(session).mark_enqueued(observed.application_id)
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
    reclaim is a conditional UPDATE against the exact heartbeat and lease token
    the sweep read, so a worker that is merely slow keeps its run: this task's
    write simply matches no row.

    **Recovery returns a run to `pending`**, unowned, and publishes a fresh
    message afterwards. It does not appoint a new owner itself, because it has
    no real Celery task id to appoint one with -- and an earlier version that
    tried left every recovered run `running` with a NULL owner, so the message
    it published could never claim it. Going back through `pending` means the
    ordinary claim, with the new delivery's own id and a new lease, is what
    takes the run.

    It also republishes `pending` runs the broker never accepted, which is the
    other half of the same problem: the row and the message cannot be written
    atomically, so the gap is reconciled afterwards rather than pretended away.

    Cancelled and finished runs are never touched -- only `running` and
    `pending` rows are eligible -- and resuming is safe because the durable
    cursor, the per-batch lease check and the per-item uniqueness constraint
    together prevent a re-entered run from repricing anything twice.
    """
    outcomes = {
        "requeued": 0,
        "abandoned": 0,
        "healthy": 0,
        "skipped": 0,
        "republished": 0,
        # Reclaimed to `pending` but the broker would not take the message.
        # Counted separately because it is the one outcome that leaves work
        # waiting on the next sweep rather than resolved by this one.
        "recovered_unpublished": 0,
    }
    for observed in _run(_stale_applications()):
        result = _run(_reconcile_one(observed))
        outcomes[result] = outcomes.get(result, 0) + 1
    for application_id, tenant_id in _run(_unpublished_applications()):
        result = _run(_republish(application_id, tenant_id))
        outcomes[result] = outcomes.get(result, 0) + 1
    if (
        outcomes["requeued"]
        or outcomes["abandoned"]
        or outcomes["republished"]
        or outcomes["recovered_unpublished"]
    ):
        logger.warning("rule_application_reconcile_summary", **outcomes)
    return outcomes
