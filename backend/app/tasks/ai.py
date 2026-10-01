"""Celery tasks for bulk pipeline preview runs (Phase 9 Stage 9).

Payload is one run id. Tenant authority is the durable row, not ``_context``.

Time limits (review finding H-3). RabbitMQ closes a channel whose delivery
stays unacknowledged past ``consumer_timeout`` (30 minutes by default), and
``task_acks_late`` acknowledges only on completion. The hard limit therefore
stays under 30 minutes. A run that needs longer is not failed: near the soft
limit the task hands the run back to pending under its fence and publishes a
continuation, so progress, start time and the retry budget carry over.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

from celery.exceptions import SoftTimeLimitExceeded

from app.ai.exceptions import AIProviderNotConfiguredError
from app.ai.factory import get_ai_provider
from app.core.config import settings
from app.core.context import clear_context, get_request_id, set_request_id, set_tenant_id
from app.core.exceptions import ConflictError
from app.core.logging import get_logger
from app.database.session import dispose_engine, transaction
from app.repositories.pipeline_bulk import PipelineBulkRunSweep, PipelineBulkRunTenantLookup
from app.services.pipeline_bulk import (
    MAX_PIPELINE_BULK_PRODUCTS,
    MAX_PIPELINE_BULK_RECOVERIES,
    STALE_AFTER,
    ClaimResult,
    ItemStep,
    LeaseObservation,
    PipelineBulkLease,
    PipelineBulkRunService,
)
from app.workers.base import BaseTask, enqueue
from app.workers.celery_app import celery_app

logger = get_logger(__name__)

_T = TypeVar("_T")

_SUPERSEDED = "superseded"
_RECONCILE_LIMIT = 50
PENDING_GRACE = timedelta(minutes=2)
#: Finding H-1: once the sweep has republished a pending run, it waits this
#: long before sending it again, instead of every five-minute tick.
REPUBLISH_AFTER = timedelta(minutes=15)
SAFE_FAILURE_REASON = "Task exhausted retries"
_MAX_ITEMS = MAX_PIPELINE_BULK_PRODUCTS
#: Under RabbitMQ's default 30-minute ``consumer_timeout`` (finding H-3).
SOFT_TIME_LIMIT_SECONDS = 1500
TIME_LIMIT_SECONDS = 1680


def _run(coro: Coroutine[Any, Any, _T]) -> _T:
    """Execute async work from a sync Celery task, then dispose the engine."""

    async def with_cleanup() -> _T:
        try:
            return await coro
        finally:
            await dispose_engine()

    return asyncio.run(with_cleanup())


def publish_pipeline_bulk_run(run_id: uuid.UUID) -> bool:
    """Publish after the run row is durable. Failure is recovered by the sweep."""
    try:
        enqueue(process_pipeline_bulk_run, run_id=str(run_id))
    except Exception as exc:
        logger.error(
            "pipeline_bulk_publish_failed",
            run_id=str(run_id),
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return False
    logger.info("pipeline_bulk_published", run_id=str(run_id))
    return True


async def _resolve_tenant(run_id: uuid.UUID) -> uuid.UUID | None:
    async with transaction() as session:
        return await PipelineBulkRunTenantLookup(session).tenant_for(run_id)


class _PipelineBulkLeaseHolder:
    """Carries the lease and progress out of processing for the task wrapper."""

    __slots__ = ("lease", "processed")

    def __init__(self) -> None:
        self.lease: PipelineBulkLease | None = None
        self.processed = 0


async def _process_pipeline_bulk_run(
    run_id: uuid.UUID, task_id: str | None, holder: _PipelineBulkLeaseHolder
) -> dict[str, Any]:
    request_id = get_request_id()
    clear_context()
    tenant_id = await _resolve_tenant(run_id)
    if tenant_id is None:
        logger.warning("pipeline_bulk_run_missing", run_id=str(run_id))
        return {"status": ClaimResult.UNKNOWN.value, "processed": 0}
    set_tenant_id(tenant_id)
    if request_id is not None:
        set_request_id(request_id)
    try:
        async with transaction() as session:
            claim = await PipelineBulkRunService(session).claim(run_id, task_id=task_id)

        if claim.lease is None:
            logger.info(
                "pipeline_bulk_not_claimed",
                run_id=str(run_id),
                reason=claim.result.value,
            )
            return {"status": claim.result.value, "processed": 0}

        lease = claim.lease
        holder.lease = lease

        try:
            get_ai_provider(settings)
        except AIProviderNotConfiguredError:
            async with transaction() as session:
                marked = await PipelineBulkRunService(session).fail_provider_not_configured(
                    run_id, lease=lease
                )
            holder.lease = None
            if marked is None:
                return {"status": _SUPERSEDED, "processed": 0}
            return {"status": marked.status.value, "processed": marked.processed_count}

        for _ in range(_MAX_ITEMS + 1):
            async with transaction() as session:
                attempt = await PipelineBulkRunService(session).claim_attempt(run_id, lease=lease)
            if attempt.step is ItemStep.LOST:
                holder.lease = None
                return {"status": _SUPERSEDED, "processed": holder.processed}
            if attempt.step is ItemStep.CANCELLED:
                holder.lease = None
                logger.info("pipeline_bulk_cancel_honoured", run_id=str(run_id))
                return {"status": "cancelled", "processed": holder.processed}
            if attempt.step is ItemStep.FAILED:
                # Out of attempts; failed on its own, move to the next item.
                holder.processed += 1
                continue
            if attempt.step is ItemStep.NO_PENDING or attempt.item_id is None:
                break

            try:
                async with transaction() as session:
                    step = await PipelineBulkRunService(session).process_item_success(
                        run_id, attempt.item_id, lease=lease
                    )
            except (ConflictError, SoftTimeLimitExceeded):
                # Concurrency: retry the task (bounded per item by the attempt
                # cap). Time limit: the task wrapper yields the run.
                raise
            except Exception as exc:
                # Finding H-5: whatever the preview raised belongs to this
                # item. Record it and carry on with the others; only a failure
                # to *record* it (e.g. the database is down) escapes to retry.
                logger.warning(
                    "pipeline_bulk_item_failed",
                    run_id=str(run_id),
                    item_id=str(attempt.item_id),
                    error_type=type(exc).__name__,
                )
                async with transaction() as session:
                    classified = await PipelineBulkRunService(session).classify_failure(
                        run_id, attempt.item_id, lease=lease, exc=exc
                    )
                if classified is ItemStep.LOST:
                    holder.lease = None
                    return {"status": _SUPERSEDED, "processed": holder.processed}
                if classified is ItemStep.STORE_NOT_FOUND:
                    return await _fail_store_not_found(run_id, lease, holder)
                holder.processed += 1
                continue

            if step is ItemStep.LOST:
                holder.lease = None
                return {"status": _SUPERSEDED, "processed": holder.processed}
            if step is ItemStep.STORE_NOT_FOUND:
                return await _fail_store_not_found(run_id, lease, holder)
            if step in (ItemStep.SUCCEEDED, ItemStep.SKIPPED, ItemStep.MISSING):
                holder.processed += 1

        async with transaction() as session:
            finished = await PipelineBulkRunService(session).finalize(run_id, lease=lease)
        holder.lease = None
        if finished is None:
            return {"status": _SUPERSEDED, "processed": holder.processed}
        result = {
            "status": finished.status.value,
            "processed": finished.processed_count,
            "succeeded": finished.succeeded_count,
            "failed": finished.failed_count,
            "skipped": finished.skipped_count,
            "missing": finished.missing_count,
        }
        logger.info("pipeline_bulk_finished", run_id=str(run_id), **result)
        return result
    finally:
        clear_context()


async def _fail_store_not_found(
    run_id: uuid.UUID, lease: PipelineBulkLease, holder: _PipelineBulkLeaseHolder
) -> dict[str, Any]:
    async with transaction() as session:
        failed = await PipelineBulkRunService(session).fail_store_not_found(run_id, lease=lease)
    holder.lease = None
    if failed is None:
        return {"status": _SUPERSEDED, "processed": holder.processed}
    return {"status": failed.status.value, "processed": failed.processed_count}


async def _with_run_tenant(run_id: uuid.UUID, work: Any) -> Any:
    tenant_id = await _resolve_tenant(run_id)
    if tenant_id is None:
        return None
    set_tenant_id(tenant_id)
    try:
        return await work()
    finally:
        clear_context()


async def _mark_pipeline_bulk_failed(
    run_id: uuid.UUID, reason: str, lease: PipelineBulkLease | None
) -> bool:
    if lease is None:
        logger.warning(
            "pipeline_bulk_failure_not_recorded",
            run_id=str(run_id),
            reason="worker did not hold the lease",
        )
        return False

    async def work() -> bool:
        async with transaction() as session:
            marked = await PipelineBulkRunService(session).fail(run_id, reason, lease=lease)
        if marked is None:
            logger.warning(
                "pipeline_bulk_failure_not_recorded",
                run_id=str(run_id),
                reason="the lease was no longer current",
            )
            return False
        return True

    return bool(await _with_run_tenant(run_id, work))


async def _yield_pipeline_bulk_run(run_id: uuid.UUID, lease: PipelineBulkLease | None) -> bool:
    """Return the run to pending under the fence; the caller republishes."""
    if lease is None:
        return False

    async def work() -> bool:
        async with transaction() as session:
            return await PipelineBulkRunService(session).yield_for_continuation(run_id, lease=lease)

    return bool(await _with_run_tenant(run_id, work))


@celery_app.task(
    base=BaseTask,
    bind=True,
    name="ai.process_pipeline_bulk_run",
    soft_time_limit=SOFT_TIME_LIMIT_SECONDS,
    time_limit=TIME_LIMIT_SECONDS,
)
def process_pipeline_bulk_run(self: Any, run_id: str, **_: Any) -> dict[str, Any]:
    identifier = uuid.UUID(run_id)
    task_id = getattr(self.request, "id", None)
    holder = _PipelineBulkLeaseHolder()
    try:
        return _run(_process_pipeline_bulk_run(identifier, task_id, holder))
    except SoftTimeLimitExceeded:
        if holder.processed > 0 and _run(_yield_pipeline_bulk_run(identifier, holder.lease)):
            # Progress was made: continue in a fresh message, not a retry.
            publish_pipeline_bulk_run(identifier)
            logger.info("pipeline_bulk_yielded", run_id=run_id, processed=holder.processed)
            return {"status": "yielded", "processed": holder.processed}
        # No item finished inside a whole time slice: that is a failure, and
        # it spends the retry budget like any other.
        return _retry_or_fail(self, identifier, holder, SoftTimeLimitExceeded())
    except Exception as exc:
        return _retry_or_fail(self, identifier, holder, exc)


def _retry_or_fail(
    task: Any, identifier: uuid.UUID, holder: _PipelineBulkLeaseHolder, exc: Exception
) -> dict[str, Any]:
    if task.request.retries >= task.max_retries:
        logger.error(
            "pipeline_bulk_exhausted_retries",
            run_id=str(identifier),
            error=str(exc),
        )
        recorded = _run(_mark_pipeline_bulk_failed(identifier, SAFE_FAILURE_REASON, holder.lease))
        return {"status": "failed" if recorded else _SUPERSEDED, "processed": 0}
    raise exc


async def _stale_runs() -> list[LeaseObservation]:
    async with transaction() as session:
        return await PipelineBulkRunSweep(session).stale_running(
            cutoff=datetime.now(UTC) - STALE_AFTER, limit=_RECONCILE_LIMIT
        )


async def _unpublished_runs() -> list[tuple[uuid.UUID, uuid.UUID]]:
    now = datetime.now(UTC)
    async with transaction() as session:
        return await PipelineBulkRunSweep(session).pending_to_republish(
            created_before=now - PENDING_GRACE,
            enqueued_before=now - REPUBLISH_AFTER,
            limit=_RECONCILE_LIMIT,
        )


async def _republish(run_id: uuid.UUID, tenant_id: uuid.UUID) -> str:
    set_tenant_id(tenant_id)
    try:
        if not publish_pipeline_bulk_run(run_id):
            return "skipped"
        async with transaction() as session:
            await PipelineBulkRunService(session).mark_enqueued(run_id)
        logger.warning("pipeline_bulk_republished", run_id=str(run_id))
        return "republished"
    finally:
        clear_context()


async def _reconcile_one(observed: LeaseObservation) -> str:
    set_tenant_id(observed.tenant_id)
    try:
        if observed.recovery_count >= MAX_PIPELINE_BULK_RECOVERIES:
            async with transaction() as session:
                parked = await PipelineBulkRunService(session).abandon_stale(observed=observed)
            return "abandoned" if parked else "healthy"

        async with transaction() as session:
            reclaimed = await PipelineBulkRunService(session).reclaim_stale(observed=observed)
        if not reclaimed:
            return "healthy"

        logger.warning(
            "pipeline_bulk_reclaimed",
            run_id=str(observed.run_id),
            recovery_count=observed.recovery_count + 1,
        )
        if not publish_pipeline_bulk_run(observed.run_id):
            return "recovered_unpublished"
        async with transaction() as session:
            await PipelineBulkRunService(session).mark_enqueued(observed.run_id)
        return "requeued"
    finally:
        clear_context()


async def _reconcile() -> dict[str, int]:
    unpublished = await _unpublished_runs()
    stale = await _stale_runs()
    counts = {
        "unpublished": 0,
        "requeued": 0,
        "abandoned": 0,
        "healthy": 0,
        "skipped": 0,
        "recovered_unpublished": 0,
        "republished": 0,
    }
    for run_id, tenant_id in unpublished:
        outcome = await _republish(run_id, tenant_id)
        counts[outcome] = counts.get(outcome, 0) + 1
        if outcome == "republished":
            counts["unpublished"] += 1
    for observed in stale:
        outcome = await _reconcile_one(observed)
        counts[outcome] = counts.get(outcome, 0) + 1
    return counts


@celery_app.task(base=BaseTask, bind=True, name="ai.reconcile_pipeline_bulk_runs")
def reconcile_pipeline_bulk_runs(self: Any, **_: Any) -> dict[str, int]:
    return _run(_reconcile())


__all__ = [
    "PENDING_GRACE",
    "REPUBLISH_AFTER",
    "SAFE_FAILURE_REASON",
    "SOFT_TIME_LIMIT_SECONDS",
    "TIME_LIMIT_SECONDS",
    "process_pipeline_bulk_run",
    "publish_pipeline_bulk_run",
    "reconcile_pipeline_bulk_runs",
]
