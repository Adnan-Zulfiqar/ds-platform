"""Bulk pipeline preview orchestration (Phase 9 Stage 9).

Creates inactive pipeline candidates through ``ProductPipelineService.preview``.
Does not approve, activate, or publish. Durable progress lives on the run and
item rows; Celery delivery is at-least-once and is not merchant truth.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import ColumnElement, and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    PipelineBulkRunActiveError,
    ValidationError,
)
from app.models.pipeline_bulk import (
    PipelineBulkItemState,
    PipelineBulkRun,
    PipelineBulkRunItem,
    PipelineBulkRunStatus,
)
from app.models.product import ProductStatus
from app.repositories.pipeline_bulk import (
    PipelineBulkRunItemRepository,
    PipelineBulkRunRepository,
)
from app.repositories.product import ProductRepository
from app.repositories.store import StoreRepository
from app.services.base import BaseService
from app.services.product_pipeline import ProductPipelineService

MAX_PIPELINE_BULK_PRODUCTS = 50
MAX_PIPELINE_BULK_RECOVERIES = 3
STALE_AFTER = timedelta(minutes=20)

_ERROR_MESSAGE_MAX = 500
_FAILURE_REASON_MAX = 500


def pipeline_bulk_fingerprint(
    *,
    product_ids: Sequence[uuid.UUID],
    tone: str,
    store_id: uuid.UUID | None,
) -> str:
    """SHA-256 of the canonical payload. Duplicate ids do not change it."""
    payload = json.dumps(
        {
            "productIds": sorted({str(product_id) for product_id in product_ids}),
            "tone": tone,
            "storeId": str(store_id) if store_id is not None else None,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def stale_running_predicate(*, now: datetime | None = None) -> ColumnElement[bool]:
    """One definition for the reconciler sweep and the reclaim UPDATE."""
    cutoff = (now or datetime.now(UTC)) - STALE_AFTER
    return or_(
        PipelineBulkRun.heartbeat_at < cutoff,
        and_(
            PipelineBulkRun.heartbeat_at.is_(None),
            func.coalesce(PipelineBulkRun.started_at, PipelineBulkRun.created_at) < cutoff,
        ),
    )


class ClaimResult(StrEnum):
    CLAIMED = "claimed"
    RESUMED = "resumed"
    ALREADY_RUNNING = "already_running"
    NOT_CLAIMABLE = "not_claimable"
    UNKNOWN = "unknown"


class ItemStep(StrEnum):
    LOST = "lost"
    ATTEMPTED = "attempted"
    NO_PENDING = "no_pending"
    ALREADY_TERMINAL = "already_terminal"
    SUCCEEDED = "succeeded"
    SKIPPED = "skipped"
    FAILED = "failed"
    MISSING = "missing"
    STORE_NOT_FOUND = "store_not_found"


@dataclass(frozen=True, slots=True)
class PipelineBulkLease:
    run_id: uuid.UUID
    task_id: str | None
    token: uuid.UUID


@dataclass(frozen=True, slots=True)
class ClaimOutcome:
    result: ClaimResult
    lease: PipelineBulkLease | None = None


@dataclass(frozen=True, slots=True)
class AttemptOutcome:
    step: ItemStep
    item_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class LeaseObservation:
    run_id: uuid.UUID
    tenant_id: uuid.UUID
    heartbeat_at: datetime | None
    lease_token: uuid.UUID | None
    recovery_count: int


def _safe_text(value: str, *, limit: int) -> str:
    stripped = value.strip()
    if len(stripped) <= limit:
        return stripped
    return stripped[:limit]


class PipelineBulkRunService(BaseService):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.runs = PipelineBulkRunRepository(session)
        self.items = PipelineBulkRunItemRepository(session)
        self.products = ProductRepository(session)
        self.stores = StoreRepository(session)
        self.pipeline = ProductPipelineService(session)

    def _tenant_id(self) -> uuid.UUID:
        from app.core.context import require_tenant_id

        return require_tenant_id()

    async def create(
        self,
        *,
        product_ids: Sequence[uuid.UUID],
        idempotency_key: str,
        tone: str,
        store_id: uuid.UUID | None,
        actor_id: uuid.UUID | None,
    ) -> PipelineBulkRun:
        key = idempotency_key.strip()
        if not key:
            raise ValidationError("An idempotency key is required.")
        if len(product_ids) > MAX_PIPELINE_BULK_PRODUCTS:
            raise ValidationError(
                f"Select at most {MAX_PIPELINE_BULK_PRODUCTS} products in one run; "
                f"{len(product_ids)} were submitted."
            )
        ordered = list(dict.fromkeys(product_ids))
        if not ordered:
            raise ValidationError("Select at least one product.")

        if store_id is not None:
            await self.stores.get_by_id_or_raise(store_id)

        fingerprint = pipeline_bulk_fingerprint(product_ids=ordered, tone=tone, store_id=store_id)
        existing = await self._find_by_key(key)
        if existing is not None:
            if existing.request_fingerprint != fingerprint:
                raise ConflictError(
                    "That idempotency key was already used with a different request."
                )
            return existing

        run = PipelineBulkRun(
            tenant_id=self._tenant_id(),
            idempotency_key=key,
            request_fingerprint=fingerprint,
            status=PipelineBulkRunStatus.PENDING,
            tone=tone,
            store_id=store_id,
            requested_by_user_id=actor_id,
            selection={"productIds": [str(product_id) for product_id in ordered]},
            total_count=len(ordered),
        )
        self.session.add(run)
        try:
            # Persist the run before items so the composite run FK has a row to
            # reference. SQLAlchemy cannot infer insert order from a table-level
            # ForeignKeyConstraint alone, and a flush of both together raises
            # IntegrityError that would be misread as the active-run race.
            await self.flush()
            for submitted in ordered:
                self.session.add(
                    PipelineBulkRunItem(
                        tenant_id=run.tenant_id,
                        run_id=run.id,
                        submitted_product_id=submitted,
                        product_id=None,
                        candidate_version_id=None,
                        state=PipelineBulkItemState.PENDING,
                    )
                )
            await self.flush()
        except IntegrityError as exc:
            await self.session.rollback()
            concurrent = await self._find_by_key(key)
            if concurrent is not None:
                if concurrent.request_fingerprint != fingerprint:
                    raise ConflictError(
                        "That idempotency key was already used with a different request."
                    ) from exc
                return concurrent
            raise PipelineBulkRunActiveError() from exc
        return run

    async def get(self, run_id: uuid.UUID) -> PipelineBulkRun:
        found = await self.runs.get_by_id(run_id)
        if found is None:
            raise NotFoundError.for_resource("PipelineBulkRun", run_id)
        return found

    async def list_items(
        self, run_id: uuid.UUID, params: Any
    ) -> tuple[Sequence[PipelineBulkRunItem], int]:
        await self.get(run_id)
        return await self.items.list(params, filters={"run_id": run_id})

    async def mark_enqueued(self, run_id: uuid.UUID) -> None:
        run = await self.get(run_id)
        run.enqueued_at = datetime.now(UTC)
        await self.flush()

    async def claim(self, run_id: uuid.UUID, *, task_id: str | None) -> ClaimOutcome:
        run = await self._lock_row(run_id)
        if run is None:
            return ClaimOutcome(ClaimResult.UNKNOWN)
        now = datetime.now(UTC)
        token = uuid.uuid4()
        if run.status is PipelineBulkRunStatus.PENDING:
            run.status = PipelineBulkRunStatus.RUNNING
            run.claimed_by_task_id = task_id
            run.lease_token = token
            run.started_at = now
            run.heartbeat_at = now
            await self.flush()
            return ClaimOutcome(ClaimResult.CLAIMED, self._lease(run_id, task_id, token))
        if run.status is not PipelineBulkRunStatus.RUNNING:
            return ClaimOutcome(ClaimResult.NOT_CLAIMABLE)
        if task_id is None or run.claimed_by_task_id != task_id:
            return ClaimOutcome(ClaimResult.ALREADY_RUNNING)
        run.lease_token = token
        run.heartbeat_at = now
        await self.flush()
        return ClaimOutcome(ClaimResult.RESUMED, self._lease(run_id, task_id, token))

    def _lease(self, run_id: uuid.UUID, task_id: str | None, token: uuid.UUID) -> PipelineBulkLease:
        return PipelineBulkLease(run_id=run_id, task_id=task_id, token=token)

    async def _lock_owned(self, lease: PipelineBulkLease) -> PipelineBulkRun | None:
        """Fence: run row FOR UPDATE, then status running and matching lease.

        Hold the lock for the rest of this transaction. A reclaim UPDATE waits
        until commit, then sees a moved heartbeat and matches zero rows.
        """
        locked = await self._lock_row(lease.run_id)
        if locked is None:
            return None
        if locked.status is not PipelineBulkRunStatus.RUNNING:
            return None
        if locked.lease_token != lease.token:
            return None
        return locked

    async def _lock_row(self, run_id: uuid.UUID) -> PipelineBulkRun | None:
        return (
            (
                await self.session.execute(
                    select(PipelineBulkRun)
                    .where(PipelineBulkRun.tenant_id == self._tenant_id())
                    .where(PipelineBulkRun.id == run_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .first()
        )

    async def claim_attempt(self, run_id: uuid.UUID, *, lease: PipelineBulkLease) -> AttemptOutcome:
        """Txn A: durable attempt claim. No provider call. Item stays pending."""
        run = await self._lock_owned(lease)
        if run is None:
            return AttemptOutcome(ItemStep.LOST)
        item = await self.items.lock_next_pending(run_id)
        if item is None:
            return AttemptOutcome(ItemStep.NO_PENDING)
        if item.state is not PipelineBulkItemState.PENDING:
            return AttemptOutcome(ItemStep.ALREADY_TERMINAL, item.id)
        item.attempt_count += 1
        if item.started_at is None:
            item.started_at = datetime.now(UTC)
        run.heartbeat_at = datetime.now(UTC)
        await self.flush()
        return AttemptOutcome(ItemStep.ATTEMPTED, item.id)

    async def process_item_success(
        self,
        run_id: uuid.UUID,
        item_id: uuid.UUID,
        *,
        lease: PipelineBulkLease,
    ) -> ItemStep:
        """Txn B: success-only generate. Preview exceptions must be re-raised."""
        run = await self._lock_owned(lease)
        if run is None:
            return ItemStep.LOST
        item = await self.items.lock_by_id(item_id)
        if item is None or item.state is not PipelineBulkItemState.PENDING:
            return ItemStep.ALREADY_TERMINAL

        if run.store_id is not None:
            store = await self.stores.get_by_id(run.store_id)
            if store is None:
                return ItemStep.STORE_NOT_FOUND

        product = await self.products.get_by_id(item.submitted_product_id)
        if product is None:
            self._terminalize(
                run,
                item,
                state=PipelineBulkItemState.MISSING,
                error_code="product_not_found",
                error_message="Product was not found.",
            )
            await self.flush()
            return ItemStep.MISSING
        if product.status in (ProductStatus.ARCHIVED, ProductStatus.UNAVAILABLE):
            self._terminalize(
                run,
                item,
                state=PipelineBulkItemState.SKIPPED,
                product_id=product.id,
                error_code="product_not_eligible",
                error_message="Product is not eligible for pipeline generation.",
            )
            await self.flush()
            return ItemStep.SKIPPED

        product_id = product.id
        # Any preview exception must leave this transaction. The caller rolls
        # Txn B back — including FOR UPDATE — and classifies in a fresh fence.
        # Do not write counters here; BaseRepository.create may already have
        # rolled the session back on ConflictError.
        preview = await self.pipeline.preview(
            product_id,
            store_id=run.store_id,
            tone=run.tone,
            requested_by_user_id=run.requested_by_user_id,
        )

        self._terminalize(
            run,
            item,
            state=PipelineBulkItemState.SUCCEEDED,
            product_id=product_id,
            candidate_version_id=preview.candidate_version_id,
        )
        await self.flush()
        return ItemStep.SUCCEEDED

    async def classify_failure(
        self,
        run_id: uuid.UUID,
        item_id: uuid.UUID,
        *,
        lease: PipelineBulkLease,
        exc: BaseException,
    ) -> ItemStep:
        """Fresh fenced transaction after Txn B rolled back a preview exception."""
        run = await self._lock_owned(lease)
        if run is None:
            return ItemStep.LOST
        item = await self.items.lock_by_id(item_id)
        if item is None or item.state is not PipelineBulkItemState.PENDING:
            return ItemStep.ALREADY_TERMINAL

        resource = getattr(exc, "details", {}).get("resource") if hasattr(exc, "details") else None
        if isinstance(exc, NotFoundError) and resource == "Store":
            return ItemStep.STORE_NOT_FOUND
        if isinstance(exc, NotFoundError):
            self._terminalize(
                run,
                item,
                state=PipelineBulkItemState.MISSING,
                error_code="product_not_found",
                error_message=_safe_text(str(exc), limit=_ERROR_MESSAGE_MAX),
            )
            await self.flush()
            return ItemStep.MISSING

        code = getattr(exc, "code", None)
        error_code = code if isinstance(code, str) and code else "ai_error"
        self._terminalize(
            run,
            item,
            state=PipelineBulkItemState.FAILED,
            error_code=error_code,
            error_message=_safe_text(str(exc), limit=_ERROR_MESSAGE_MAX),
        )
        await self.flush()
        return ItemStep.FAILED

    async def fail_store_not_found(
        self, run_id: uuid.UUID, *, lease: PipelineBulkLease
    ) -> PipelineBulkRun | None:
        run = await self._lock_owned(lease)
        if run is None or run.status is not PipelineBulkRunStatus.RUNNING:
            return None
        await self._refresh_counts(run)
        run.status = PipelineBulkRunStatus.FAILED
        run.failure_reason = "Store was not found."
        run.finished_at = datetime.now(UTC)
        run.lease_token = None
        await self.flush()
        return run

    async def fail_provider_not_configured(
        self, run_id: uuid.UUID, *, lease: PipelineBulkLease
    ) -> PipelineBulkRun | None:
        run = await self._lock_owned(lease)
        if run is None or run.status is not PipelineBulkRunStatus.RUNNING:
            return None
        pending = (
            (
                await self.session.execute(
                    select(PipelineBulkRunItem)
                    .where(PipelineBulkRunItem.tenant_id == self._tenant_id())
                    .where(PipelineBulkRunItem.run_id == run_id)
                    .where(PipelineBulkRunItem.state == PipelineBulkItemState.PENDING)
                    .where(PipelineBulkRunItem.deleted_at.is_(None))
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )
        now = datetime.now(UTC)
        for item in pending:
            item.state = PipelineBulkItemState.FAILED
            item.error_code = "ai_provider_not_configured"
            item.error_message = "AI provider is not configured."
            item.finished_at = now
        await self._refresh_counts(run)
        run.status = PipelineBulkRunStatus.FAILED
        run.failure_reason = "AI provider is not configured."
        run.finished_at = now
        run.lease_token = None
        run.heartbeat_at = now
        await self.flush()
        return run

    async def finalize(
        self, run_id: uuid.UUID, *, lease: PipelineBulkLease
    ) -> PipelineBulkRun | None:
        run = await self._lock_owned(lease)
        if run is None:
            return None
        await self._refresh_counts(run)
        if run.status is PipelineBulkRunStatus.CANCELLED:
            run.lease_token = None
            await self.flush()
            return run
        if run.status is not PipelineBulkRunStatus.RUNNING:
            return None
        run.status = self._terminal_status(run)
        run.finished_at = datetime.now(UTC)
        run.lease_token = None
        await self.flush()
        return run

    async def fail(
        self, run_id: uuid.UUID, reason: str, *, lease: PipelineBulkLease
    ) -> PipelineBulkRun | None:
        run = await self._lock_owned(lease)
        if run is None or run.status is not PipelineBulkRunStatus.RUNNING:
            return None
        await self._refresh_counts(run)
        run.status = PipelineBulkRunStatus.FAILED
        run.failure_reason = _safe_text(reason, limit=_FAILURE_REASON_MAX)
        run.finished_at = datetime.now(UTC)
        run.lease_token = None
        await self.flush()
        return run

    async def cancel(self, run_id: uuid.UUID) -> PipelineBulkRun:
        run = await self._lock_row(run_id)
        if run is None:
            raise NotFoundError.for_resource("PipelineBulkRun", run_id)
        if run.status is PipelineBulkRunStatus.CANCELLED:
            return run
        if run.status not in (PipelineBulkRunStatus.PENDING, PipelineBulkRunStatus.RUNNING):
            raise ConflictError(f"A run that is {run.status.value} cannot be cancelled.")
        was_running = run.status is PipelineBulkRunStatus.RUNNING
        run.status = PipelineBulkRunStatus.CANCELLED
        run.finished_at = datetime.now(UTC)
        if was_running:
            run.failure_reason = "Cancelled while running. Candidates already generated were kept."
        await self._refresh_counts(run)
        await self.flush()
        return run

    async def observe(self, run_id: uuid.UUID) -> LeaseObservation | None:
        row = (
            (
                await self.session.execute(
                    select(
                        PipelineBulkRun.id,
                        PipelineBulkRun.tenant_id,
                        PipelineBulkRun.heartbeat_at,
                        PipelineBulkRun.lease_token,
                        PipelineBulkRun.recovery_count,
                    )
                    .where(PipelineBulkRun.tenant_id == self._tenant_id())
                    .where(PipelineBulkRun.id == run_id)
                )
            )
            .tuples()
            .first()
        )
        if row is None:
            return None
        return LeaseObservation(
            run_id=row[0],
            tenant_id=row[1],
            heartbeat_at=row[2],
            lease_token=row[3],
            recovery_count=row[4],
        )

    async def reclaim_stale(self, *, observed: LeaseObservation) -> bool:
        cleared = await self.session.execute(
            update(PipelineBulkRun)
            .where(PipelineBulkRun.id == observed.run_id)
            .where(PipelineBulkRun.tenant_id == self._tenant_id())
            .where(PipelineBulkRun.status == PipelineBulkRunStatus.RUNNING)
            .where(PipelineBulkRun.recovery_count == observed.recovery_count)
            .where(PipelineBulkRun.recovery_count < MAX_PIPELINE_BULK_RECOVERIES)
            .where(PipelineBulkRun.heartbeat_at.is_not_distinct_from(observed.heartbeat_at))
            .where(PipelineBulkRun.lease_token.is_not_distinct_from(observed.lease_token))
            .where(stale_running_predicate())
            .values(
                status=PipelineBulkRunStatus.PENDING,
                claimed_by_task_id=None,
                lease_token=None,
                heartbeat_at=None,
                enqueued_at=None,
                recovery_count=PipelineBulkRun.recovery_count + 1,
            )
            .returning(PipelineBulkRun.id)
            .execution_options(synchronize_session=False)
        )
        return cleared.scalars().first() is not None

    async def abandon_stale(self, *, observed: LeaseObservation) -> bool:
        parked = await self.session.execute(
            update(PipelineBulkRun)
            .where(PipelineBulkRun.id == observed.run_id)
            .where(PipelineBulkRun.tenant_id == self._tenant_id())
            .where(PipelineBulkRun.status == PipelineBulkRunStatus.RUNNING)
            .where(PipelineBulkRun.recovery_count == observed.recovery_count)
            .where(PipelineBulkRun.recovery_count >= MAX_PIPELINE_BULK_RECOVERIES)
            .where(PipelineBulkRun.heartbeat_at.is_not_distinct_from(observed.heartbeat_at))
            .where(PipelineBulkRun.lease_token.is_not_distinct_from(observed.lease_token))
            .where(stale_running_predicate())
            .values(
                status=PipelineBulkRunStatus.FAILED,
                lease_token=None,
                claimed_by_task_id=None,
                failure_reason="Abandoned after exceeding the recovery ceiling.",
                finished_at=datetime.now(UTC),
            )
            .returning(PipelineBulkRun.id)
            .execution_options(synchronize_session=False)
        )
        return parked.scalars().first() is not None

    def _terminalize(
        self,
        run: PipelineBulkRun,
        item: PipelineBulkRunItem,
        *,
        state: PipelineBulkItemState,
        product_id: uuid.UUID | None = None,
        candidate_version_id: uuid.UUID | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        item.state = state
        item.product_id = product_id
        item.candidate_version_id = candidate_version_id
        item.error_code = error_code
        item.error_message = error_message
        item.finished_at = datetime.now(UTC)
        run.processed_count += 1
        if state is PipelineBulkItemState.SUCCEEDED:
            run.succeeded_count += 1
        elif state is PipelineBulkItemState.FAILED:
            run.failed_count += 1
        elif state is PipelineBulkItemState.SKIPPED:
            run.skipped_count += 1
        elif state is PipelineBulkItemState.MISSING:
            run.missing_count += 1
        run.heartbeat_at = datetime.now(UTC)

    async def _refresh_counts(self, run: PipelineBulkRun) -> None:
        rows = (
            await self.session.execute(
                select(PipelineBulkRunItem.state, func.count())
                .where(PipelineBulkRunItem.tenant_id == self._tenant_id())
                .where(PipelineBulkRunItem.run_id == run.id)
                .where(PipelineBulkRunItem.deleted_at.is_(None))
                .group_by(PipelineBulkRunItem.state)
            )
        ).all()
        counts = {state: int(n) for state, n in rows}
        run.succeeded_count = counts.get(PipelineBulkItemState.SUCCEEDED, 0)
        run.failed_count = counts.get(PipelineBulkItemState.FAILED, 0)
        run.skipped_count = counts.get(PipelineBulkItemState.SKIPPED, 0)
        run.missing_count = counts.get(PipelineBulkItemState.MISSING, 0)
        run.processed_count = (
            run.succeeded_count + run.failed_count + run.skipped_count + run.missing_count
        )

    def _terminal_status(self, run: PipelineBulkRun) -> PipelineBulkRunStatus:
        if run.processed_count < run.total_count:
            return PipelineBulkRunStatus.FAILED
        if run.failed_count == 0 and run.missing_count == 0:
            return PipelineBulkRunStatus.COMPLETED
        if run.succeeded_count > 0:
            return PipelineBulkRunStatus.PARTIAL
        return PipelineBulkRunStatus.FAILED

    async def _find_by_key(self, key: str) -> PipelineBulkRun | None:
        query = (
            select(PipelineBulkRun)
            .where(PipelineBulkRun.tenant_id == self._tenant_id())
            .where(PipelineBulkRun.idempotency_key == key.strip())
            .where(PipelineBulkRun.deleted_at.is_(None))
        )
        return (await self.session.execute(query)).scalars().first()


__all__ = [
    "MAX_PIPELINE_BULK_PRODUCTS",
    "MAX_PIPELINE_BULK_RECOVERIES",
    "STALE_AFTER",
    "AttemptOutcome",
    "ClaimOutcome",
    "ClaimResult",
    "ItemStep",
    "LeaseObservation",
    "PipelineBulkLease",
    "PipelineBulkRunService",
    "pipeline_bulk_fingerprint",
    "stale_running_predicate",
]
