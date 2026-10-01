"""Data access for pipeline bulk runs, items and cancellation requests.

Every tenant-owned query is built on ``_base_query`` (review finding A-2:
the service no longer assembles SQL). Two deliberately unscoped helpers sit
at the bottom, each answering one question for code that runs before any
tenant context exists — same shape as ``RuleApplicationTenantLookup``:

* ``PipelineBulkRunTenantLookup`` — which tenant owns a run id.
* ``PipelineBulkRunSweep`` — the reconciler's cross-tenant sweep, returning
  ids and tenant ids only, never a row a request path could render.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ColumnElement, and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import require_tenant_id
from app.models.pipeline_bulk import (
    PipelineBulkItemState,
    PipelineBulkRun,
    PipelineBulkRunCancelRequest,
    PipelineBulkRunItem,
    PipelineBulkRunStatus,
)
from app.repositories.base import TenantScopedRepository

#: PostgreSQL SQLSTATE for ``FOR UPDATE NOWAIT`` finding the row locked.
_LOCK_NOT_AVAILABLE = "55P03"


class RunLocked(Exception):
    """The run row is held by a worker mid-item; the caller must not wait."""


@dataclass(frozen=True, slots=True)
class RunLeaseRow:
    """Lease-relevant columns only, as observed by one read."""

    run_id: uuid.UUID
    tenant_id: uuid.UUID
    heartbeat_at: datetime | None
    lease_token: uuid.UUID | None
    recovery_count: int


def _is_lock_not_available(exc: DBAPIError) -> bool:
    original = getattr(exc, "orig", None)
    for attribute in ("sqlstate", "pgcode"):
        if getattr(original, attribute, None) == _LOCK_NOT_AVAILABLE:
            return True
    diag = getattr(original, "diag", None)
    return getattr(diag, "sqlstate", None) == _LOCK_NOT_AVAILABLE


def stale_running_predicate(*, cutoff: datetime) -> ColumnElement[bool]:
    """One definition for the reconciler sweep and the reclaim UPDATE."""
    return or_(
        PipelineBulkRun.heartbeat_at < cutoff,
        and_(
            PipelineBulkRun.heartbeat_at.is_(None),
            func.coalesce(PipelineBulkRun.started_at, PipelineBulkRun.created_at) < cutoff,
        ),
    )


class PipelineBulkRunRepository(TenantScopedRepository[PipelineBulkRun]):
    sortable_fields = frozenset({"created_at", "updated_at", "status", "finished_at"})

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, PipelineBulkRun)

    async def lock(self, run_id: uuid.UUID, *, nowait: bool = False) -> PipelineBulkRun | None:
        """This tenant's run, ``FOR UPDATE`` for the rest of the transaction.

        ``nowait`` raises :class:`RunLocked` instead of queueing behind a
        worker that holds the row across a whole item. Callers using it must
        wrap the call in a savepoint: PostgreSQL aborts the transaction on
        ``55P03``.

        ``FOR NO KEY UPDATE``, not ``FOR UPDATE``: it still serialises every
        writer of the run (reclaim's UPDATE, cancel, finalize all conflict
        with it), but not the ``KEY SHARE`` lock a foreign-key check takes.
        A cancellation request row references this run; with ``FOR UPDATE``
        inserting it would wait out the worker's whole item — the very wait
        finding H-2 removes. No code changes a run's key columns.
        """
        query = (
            self._base_query()
            .where(PipelineBulkRun.id == run_id)
            .with_for_update(nowait=nowait, key_share=True)
            .execution_options(populate_existing=True)
        )
        try:
            return (await self.session.execute(query)).scalars().first()
        except DBAPIError as exc:
            if nowait and _is_lock_not_available(exc):
                raise RunLocked() from exc
            raise

    async def find_by_idempotency_key(self, key: str) -> PipelineBulkRun | None:
        query = self._base_query().where(PipelineBulkRun.idempotency_key == key)
        return (await self.session.execute(query)).scalars().first()

    async def observe_lease(self, run_id: uuid.UUID) -> RunLeaseRow | None:
        tenant_where = self._base_query().whereclause
        query = select(
            PipelineBulkRun.id,
            PipelineBulkRun.tenant_id,
            PipelineBulkRun.heartbeat_at,
            PipelineBulkRun.lease_token,
            PipelineBulkRun.recovery_count,
        ).where(PipelineBulkRun.id == run_id)
        if tenant_where is not None:
            query = query.where(tenant_where)
        row = (await self.session.execute(query)).tuples().first()
        if row is None:
            return None
        return RunLeaseRow(*row)

    async def conditional_transition(
        self,
        *,
        observed: RunLeaseRow,
        cutoff: datetime,
        below_recovery_ceiling: int | None,
        at_or_above_recovery_ceiling: int | None,
        values: dict[str, object],
    ) -> bool:
        """Owner-conditional UPDATE for reclaim / abandon.

        Matches only when status, recovery count, heartbeat and lease token
        are exactly what the sweep observed and the run is still stale — so
        a live worker that heart-beat in between wins.
        """
        statement = (
            update(PipelineBulkRun)
            .where(PipelineBulkRun.id == observed.run_id)
            .where(PipelineBulkRun.tenant_id == require_tenant_id())
            .where(PipelineBulkRun.deleted_at.is_(None))
            .where(PipelineBulkRun.status == PipelineBulkRunStatus.RUNNING)
            .where(PipelineBulkRun.recovery_count == observed.recovery_count)
            .where(PipelineBulkRun.heartbeat_at.is_not_distinct_from(observed.heartbeat_at))
            .where(PipelineBulkRun.lease_token.is_not_distinct_from(observed.lease_token))
            .where(stale_running_predicate(cutoff=cutoff))
            .values(**values)
            .returning(PipelineBulkRun.id)
            .execution_options(synchronize_session=False)
        )
        if below_recovery_ceiling is not None:
            statement = statement.where(PipelineBulkRun.recovery_count < below_recovery_ceiling)
        if at_or_above_recovery_ceiling is not None:
            statement = statement.where(
                PipelineBulkRun.recovery_count >= at_or_above_recovery_ceiling
            )
        return (await self.session.execute(statement)).scalars().first() is not None


class PipelineBulkRunItemRepository(TenantScopedRepository[PipelineBulkRunItem]):
    sortable_fields = frozenset({"created_at", "finished_at", "state"})

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, PipelineBulkRunItem)

    async def lock_next_pending(self, run_id: uuid.UUID) -> PipelineBulkRunItem | None:
        """Next unprocessed item, held for the rest of this transaction.

        Ordered by first-seen ``created_at`` so retries walk the snapshot, not
        a live catalogue. ``populate_existing`` so a cached pending copy cannot
        hide a terminal state another transaction already committed.
        """
        query = (
            self._base_query()
            .where(PipelineBulkRunItem.run_id == run_id)
            .where(PipelineBulkRunItem.state == PipelineBulkItemState.PENDING)
            .order_by(PipelineBulkRunItem.created_at.asc(), PipelineBulkRunItem.id.asc())
            .with_for_update()
            .limit(1)
            .execution_options(populate_existing=True)
        )
        return (await self.session.execute(query)).scalars().first()

    async def lock_by_id(self, item_id: uuid.UUID) -> PipelineBulkRunItem | None:
        query = (
            self._base_query()
            .where(PipelineBulkRunItem.id == item_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return (await self.session.execute(query)).scalars().first()

    async def lock_pending(self, run_id: uuid.UUID) -> list[PipelineBulkRunItem]:
        query = (
            self._base_query()
            .where(PipelineBulkRunItem.run_id == run_id)
            .where(PipelineBulkRunItem.state == PipelineBulkItemState.PENDING)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return list((await self.session.execute(query)).scalars().all())

    async def count_by_state(self, run_id: uuid.UUID) -> dict[PipelineBulkItemState, int]:
        tenant_where = self._base_query().whereclause
        query = select(PipelineBulkRunItem.state, func.count()).where(
            PipelineBulkRunItem.run_id == run_id
        )
        if tenant_where is not None:
            query = query.where(tenant_where)
        rows = (await self.session.execute(query.group_by(PipelineBulkRunItem.state))).all()
        return {state: int(count) for state, count in rows}


class PipelineBulkRunCancelRequestRepository(TenantScopedRepository[PipelineBulkRunCancelRequest]):
    """Cancellation asked for while a worker held the run row (finding H-2)."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, PipelineBulkRunCancelRequest)

    async def request(self, *, run_id: uuid.UUID, requested_by_user_id: uuid.UUID | None) -> None:
        """Record the request once. Idempotent: a second click is a no-op.

        Does not touch the run row, so it never waits on the worker's lock.
        """
        statement = (
            pg_insert(PipelineBulkRunCancelRequest)
            .values(
                id=uuid.uuid4(),
                tenant_id=require_tenant_id(),
                run_id=run_id,
                requested_by_user_id=requested_by_user_id,
            )
            .on_conflict_do_nothing(constraint="uq_pipeline_bulk_run_cancel_requests_tenant_run")
        )
        await self.session.execute(statement)

    async def requested_at(self, run_id: uuid.UUID) -> datetime | None:
        query = self._base_query().where(PipelineBulkRunCancelRequest.run_id == run_id)
        found = (await self.session.execute(query)).scalars().first()
        return found.created_at if found is not None else None


class PipelineBulkRunTenantLookup:
    """Resolves which tenant owns a run id, before context exists.

    Deliberately unscoped, and deliberately **not** a repository: it takes no
    base class, exposes exactly one question, and returns a tenant id and
    nothing else. Taking the tenant from a Celery payload would let a forged
    or stale message run one tenant's bulk job over another's catalogue.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def tenant_for(self, run_id: uuid.UUID) -> uuid.UUID | None:
        query = select(PipelineBulkRun.tenant_id).where(PipelineBulkRun.id == run_id)
        return (await self.session.execute(query)).scalars().first()


class PipelineBulkRunSweep:
    """The reconciler's cross-tenant view: ids and lease columns, nothing else.

    Unscoped by design — the sweep is what *finds* the tenants to act on.
    Each action it leads to then runs under that run's own tenant context
    through the scoped repository above.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def stale_running(self, *, cutoff: datetime, limit: int) -> list[RunLeaseRow]:
        rows = await self.session.execute(
            select(
                PipelineBulkRun.id,
                PipelineBulkRun.tenant_id,
                PipelineBulkRun.heartbeat_at,
                PipelineBulkRun.lease_token,
                PipelineBulkRun.recovery_count,
            )
            .where(PipelineBulkRun.deleted_at.is_(None))
            .where(PipelineBulkRun.status == PipelineBulkRunStatus.RUNNING)
            .where(stale_running_predicate(cutoff=cutoff))
            .order_by(PipelineBulkRun.heartbeat_at.asc().nullsfirst())
            .limit(limit)
        )
        return [RunLeaseRow(*row) for row in rows.tuples().all()]

    async def pending_to_republish(
        self, *, created_before: datetime, enqueued_before: datetime, limit: int
    ) -> list[tuple[uuid.UUID, uuid.UUID]]:
        """Pending runs past the grace period whose last publish is not recent.

        ``enqueued_at`` is stamped each time the sweep republishes, so a run
        stuck behind a busy queue is re-sent at most once per
        ``enqueued_before`` window rather than on every tick (finding H-1).
        """
        rows = await self.session.execute(
            select(PipelineBulkRun.id, PipelineBulkRun.tenant_id)
            .where(PipelineBulkRun.deleted_at.is_(None))
            .where(PipelineBulkRun.status == PipelineBulkRunStatus.PENDING)
            .where(PipelineBulkRun.created_at < created_before)
            .where(
                or_(
                    PipelineBulkRun.enqueued_at.is_(None),
                    PipelineBulkRun.enqueued_at < enqueued_before,
                )
            )
            .order_by(PipelineBulkRun.created_at.asc())
            .limit(limit)
        )
        return [(row[0], row[1]) for row in rows.tuples().all()]


__all__ = [
    "PipelineBulkRunCancelRequestRepository",
    "PipelineBulkRunItemRepository",
    "PipelineBulkRunRepository",
    "PipelineBulkRunSweep",
    "PipelineBulkRunTenantLookup",
    "RunLeaseRow",
    "RunLocked",
    "stale_running_predicate",
]
