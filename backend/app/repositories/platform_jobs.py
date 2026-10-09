"""Failed and stuck background work across workspaces (Admin Control Center
phase 7, D-019).

Unscoped by design, and on the CLAUDE.md §4 closed list: "which jobs are
failing right now, anywhere" is the question an operator's jobs page
answers, and no single workspace can. It returns **job fields only**: kind,
id, workspace, status, timings and the job's own error text. It never
returns a product, an order, a buyer or a credential. Any action on a job
goes through the workspace's own routes (``platform_workspace(...,
write=True)``), so it needs a support session for that workspace.

Celery itself keeps no job table (``task_failed_permanently`` is a log
line), so this reads the run tables the application already writes.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import ColumnElement, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.automation import AutomationRun
from app.models.inventory import InventorySyncRun
from app.models.order import OrderSyncRun, SyncRunStatus
from app.models.pipeline_bulk import PipelineBulkRun, PipelineBulkRunStatus
from app.models.product import ImportStatus, ProductImport
from app.models.rule_application import ApplicationStatus, RuleApplication
from app.models.supplier_order import SupplierOrder
from app.models.tenant import Tenant

JobKind = Literal[
    "order_sync",
    "inventory_sync",
    "product_import",
    "pipeline_run",
    "rule_application",
    "supplier_order",
    "automation_run",
]
JobState = Literal["failed", "stuck"]

JOB_KINDS: tuple[JobKind, ...] = (
    "order_sync",
    "inventory_sync",
    "product_import",
    "pipeline_run",
    "rule_application",
    "supplier_order",
    "automation_run",
)

#: A sync, import or automation run still ``running`` after this long has
#: stopped; order and inventory syncs have no sweep of their own.
RUN_STUCK_AFTER = timedelta(minutes=30)
#: Matches ``services.pipeline_bulk.STALE_AFTER``.
PIPELINE_STUCK_AFTER = timedelta(minutes=20)
#: Matches ``services.rule_application.STALE_AFTER``.
RULES_STUCK_AFTER = timedelta(minutes=15)
#: Matches ``SupplierOrderRepository.stale_queued``.
SUPPLIER_STUCK_AFTER = timedelta(minutes=10)
#: How far back "failed" looks.
FAILED_WINDOW = timedelta(days=7)
MAX_PAGE = 100


@dataclass(frozen=True, slots=True)
class JobRow:
    kind: str
    id: uuid.UUID
    tenant_id: uuid.UUID
    tenant_name: str
    status: str
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class _Spec:
    model: Any
    failed: Callable[[], ColumnElement[bool]]
    stuck: Callable[[datetime], ColumnElement[bool]]
    error: Callable[[], Any]
    started: Callable[[], Any]
    finished: Callable[[], Any]


def _error(code: Any, message: Any) -> Any:
    return func.concat_ws(": ", code, message)


_SPECS: dict[str, _Spec] = {
    "order_sync": _Spec(
        model=OrderSyncRun,
        failed=lambda: OrderSyncRun.status == SyncRunStatus.FAILED,
        stuck=lambda now: (
            (OrderSyncRun.status == SyncRunStatus.RUNNING)
            & (OrderSyncRun.started_at < now - RUN_STUCK_AFTER)
        ),
        error=lambda: _error(OrderSyncRun.error_code, OrderSyncRun.error_message),
        started=lambda: OrderSyncRun.started_at,
        finished=lambda: OrderSyncRun.finished_at,
    ),
    "inventory_sync": _Spec(
        model=InventorySyncRun,
        failed=lambda: InventorySyncRun.status == SyncRunStatus.FAILED,
        stuck=lambda now: (
            (InventorySyncRun.status == SyncRunStatus.RUNNING)
            & (InventorySyncRun.started_at < now - RUN_STUCK_AFTER)
        ),
        error=lambda: _error(InventorySyncRun.error_code, InventorySyncRun.error_message),
        started=lambda: InventorySyncRun.started_at,
        finished=lambda: InventorySyncRun.finished_at,
    ),
    "product_import": _Spec(
        model=ProductImport,
        failed=lambda: ProductImport.status == ImportStatus.FAILED,
        stuck=lambda now: (
            (ProductImport.status == ImportStatus.RUNNING)
            & (ProductImport.started_at < now - RUN_STUCK_AFTER)
        ),
        error=lambda: _error(ProductImport.error_code, ProductImport.error_message),
        started=lambda: ProductImport.started_at,
        finished=lambda: ProductImport.finished_at,
    ),
    "pipeline_run": _Spec(
        model=PipelineBulkRun,
        failed=lambda: PipelineBulkRun.status.in_(
            [PipelineBulkRunStatus.FAILED, PipelineBulkRunStatus.PARTIAL]
        ),
        stuck=lambda now: (
            (PipelineBulkRun.status == PipelineBulkRunStatus.RUNNING)
            & (
                func.coalesce(PipelineBulkRun.heartbeat_at, PipelineBulkRun.started_at)
                < now - PIPELINE_STUCK_AFTER
            )
        ),
        error=lambda: PipelineBulkRun.failure_reason,
        started=lambda: PipelineBulkRun.started_at,
        finished=lambda: PipelineBulkRun.finished_at,
    ),
    "rule_application": _Spec(
        model=RuleApplication,
        failed=lambda: RuleApplication.status.in_(
            [ApplicationStatus.FAILED, ApplicationStatus.PARTIAL]
        ),
        stuck=lambda now: (
            (RuleApplication.status == ApplicationStatus.RUNNING)
            & (
                func.coalesce(RuleApplication.heartbeat_at, RuleApplication.started_at)
                < now - RULES_STUCK_AFTER
            )
        ),
        error=lambda: RuleApplication.failure_reason,
        started=lambda: RuleApplication.started_at,
        finished=lambda: RuleApplication.finished_at,
    ),
    "supplier_order": _Spec(
        model=SupplierOrder,
        failed=lambda: SupplierOrder.status == "failed",
        # ``placing`` with no answer, or ``queued`` and never picked up.
        stuck=lambda now: (
            SupplierOrder.status.in_(["placing", "queued"])
            & (SupplierOrder.updated_at < now - SUPPLIER_STUCK_AFTER)
        ),
        error=lambda: _error(SupplierOrder.error_code, SupplierOrder.error_message),
        started=lambda: SupplierOrder.created_at,
        finished=lambda: SupplierOrder.placed_at,
    ),
    "automation_run": _Spec(
        model=AutomationRun,
        failed=lambda: AutomationRun.status == SyncRunStatus.FAILED,
        stuck=lambda now: (
            (AutomationRun.status == SyncRunStatus.RUNNING)
            & (AutomationRun.started_at < now - RUN_STUCK_AFTER)
        ),
        error=lambda: AutomationRun.error_message,
        started=lambda: AutomationRun.started_at,
        finished=lambda: AutomationRun.finished_at,
    ),
}


class PlatformJobsMonitor:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _where(self, spec: _Spec, state: JobState, now: datetime) -> ColumnElement[bool]:
        # ``spec.model`` is typed Any (one spec per model); the result is a
        # boolean clause either way.
        live: ColumnElement[bool] = spec.model.deleted_at.is_(None)
        if state == "stuck":
            return live & spec.stuck(now)
        recent: ColumnElement[bool] = spec.model.created_at >= now - FAILED_WINDOW
        return live & spec.failed() & recent

    async def page(
        self,
        *,
        kind: JobKind,
        state: JobState,
        page: int,
        size: int,
        tenant_id: uuid.UUID | None = None,
    ) -> tuple[list[JobRow], int]:
        spec = _SPECS[kind]
        now = datetime.now(UTC)
        size = max(1, min(size, MAX_PAGE))
        where = self._where(spec, state, now)
        if tenant_id is not None:
            where = where & (spec.model.tenant_id == tenant_id)
        total = int(
            (
                await self.session.execute(
                    select(func.count()).select_from(spec.model).where(where)
                )
            ).scalar_one()
        )
        query = (
            select(
                literal(kind),
                spec.model.id,
                spec.model.tenant_id,
                Tenant.name,
                spec.model.status,
                spec.error(),
                spec.started(),
                spec.finished(),
                spec.model.created_at,
            )
            .join(Tenant, Tenant.id == spec.model.tenant_id)
            .where(where)
            .order_by(spec.model.created_at.desc(), spec.model.id.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
        rows = [
            JobRow(
                kind=str(k),
                id=i,
                tenant_id=t,
                tenant_name=n,
                status=str(getattr(st, "value", st)),
                error=(str(err)[:500] if err else None),
                started_at=sa,
                finished_at=fa,
                created_at=ca,
            )
            for k, i, t, n, st, err, sa, fa, ca in (await self.session.execute(query)).all()
        ]
        return rows, total

    async def summary(self) -> dict[str, dict[str, int]]:
        """Counts per kind: failed in the window, and stuck now."""
        now = datetime.now(UTC)
        out: dict[str, dict[str, int]] = {}
        for kind, spec in _SPECS.items():
            counts: dict[str, int] = {}
            states: tuple[JobState, ...] = ("failed", "stuck")
            for state in states:
                counts[state] = int(
                    (
                        await self.session.execute(
                            select(func.count())
                            .select_from(spec.model)
                            .where(self._where(spec, state, now))
                        )
                    ).scalar_one()
                )
            out[kind] = counts
        return out


__all__ = [
    "FAILED_WINDOW",
    "JOB_KINDS",
    "RUN_STUCK_AFTER",
    "JobKind",
    "JobRow",
    "JobState",
    "PlatformJobsMonitor",
]
