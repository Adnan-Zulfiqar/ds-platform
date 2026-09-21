"""Durable record of one bulk pipeline preview run (Phase 9 Stage 9).

Bulk AI work outlives a request and can be delivered more than once. Progress
therefore lives on these rows, not in Celery's result backend. Pricing's
``RuleApplication`` taught the guarantees; this is a separate table because
the item vocabulary and cost bound are different.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TenantScopedBase


class PipelineBulkRunStatus(StrEnum):
    """Where a bulk pipeline run got to.

    ``partial`` is first-class: a 48-success / 2-failure run is not
    ``completed``. ``failed`` covers run-level abort as well as all-items
    processed with zero successes.
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PipelineBulkItemState(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    MISSING = "missing"


class PipelineBulkRun(TenantScopedBase):
    """One accepted request to generate inactive pipeline preview candidates."""

    __tablename__ = "pipeline_bulk_runs"

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_pipeline_bulk_runs_tenant_idempotency",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_pipeline_bulk_runs_tenant_id_id"),
        Index(
            "uq_pipeline_bulk_runs_tenant_active",
            "tenant_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'running')"),
        ),
        Index("ix_pipeline_bulk_runs_tenant_created", "tenant_id", "created_at"),
        Index("ix_pipeline_bulk_runs_tenant_status", "tenant_id", "status"),
        Index(
            "ix_pipeline_bulk_runs_pending_created",
            "created_at",
            postgresql_where=text("status = 'pending'"),
        ),
        Index(
            "ix_pipeline_bulk_runs_running_heartbeat",
            "heartbeat_at",
            postgresql_where=text("status = 'running'"),
        ),
        ForeignKeyConstraint(
            ["store_id"],
            ["stores.id"],
            ondelete="RESTRICT",
            name="fk_pipeline_bulk_runs_store",
        ),
    )

    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[PipelineBulkRunStatus] = mapped_column(
        Enum(
            PipelineBulkRunStatus,
            name="pipeline_bulk_run_status",
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        default=PipelineBulkRunStatus.PENDING,
        server_default=PipelineBulkRunStatus.PENDING.value,
    )
    tone: Mapped[str] = mapped_column(String(64), nullable=False)
    store_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    selection: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    claimed_by_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_token: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recovery_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    enqueued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    total_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    succeeded_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    missing_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)


class PipelineBulkRunItem(TenantScopedBase):
    """One submitted product id inside a bulk pipeline run.

    ``product_id`` is NULL at create for every item so mixed own + foreign
    ids cannot violate the composite product FK, and so a 404 on start
    cannot enumerate which UUIDs exist.
    """

    __tablename__ = "pipeline_bulk_run_items"

    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "submitted_product_id",
            name="uq_pipeline_bulk_run_items_run_product",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["pipeline_bulk_runs.tenant_id", "pipeline_bulk_runs.id"],
            ondelete="CASCADE",
            name="fk_pipeline_bulk_run_items_run_tenant",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["products.tenant_id", "products.id"],
            ondelete="RESTRICT",
            name="fk_pipeline_bulk_run_items_product_tenant",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "candidate_version_id"],
            ["product_versions.tenant_id", "product_versions.id"],
            ondelete="RESTRICT",
            name="fk_pipeline_bulk_run_items_version_tenant",
        ),
        CheckConstraint(
            "(state = 'succeeded' AND candidate_version_id IS NOT NULL) "
            "OR (state <> 'succeeded' AND candidate_version_id IS NULL)",
            name="succeeded_version",
        ),
        Index("ix_pipeline_bulk_run_items_tenant_run", "tenant_id", "run_id"),
        Index("ix_pipeline_bulk_run_items_run_state", "run_id", "state"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    submitted_product_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    product_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    candidate_version_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    state: Mapped[PipelineBulkItemState] = mapped_column(
        Enum(
            PipelineBulkItemState,
            name="pipeline_bulk_item_state",
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        default=PipelineBulkItemState.PENDING,
        server_default=PipelineBulkItemState.PENDING.value,
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


__all__ = [
    "PipelineBulkItemState",
    "PipelineBulkRun",
    "PipelineBulkRunItem",
    "PipelineBulkRunStatus",
]
