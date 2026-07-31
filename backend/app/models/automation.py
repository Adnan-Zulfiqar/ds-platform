"""Automation rules and execution history.

Rules are configuration; execution always happens in a Celery task. An HTTP
handler may *enqueue* a run, never execute the rule body inline — that is what
keeps request latency independent of how many products a nightly job touches.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TenantScopedBase
from app.models.order import SyncRunStatus


class AutomationAction(StrEnum):
    """What a rule does when it fires.

    Each value maps to an existing service method — the automation engine is a
    scheduler and dispatcher, not a second implementation of sync/import/pricing.
    """

    SYNC_INVENTORY = "sync_inventory"
    UPDATE_PRICING = "update_pricing"
    REFRESH_ORDERS = "refresh_orders"
    ARCHIVE_COMPLETED_ORDERS = "archive_completed_orders"
    RETRY_FAILED_JOBS = "retry_failed_jobs"
    IMPORT_PRODUCT = "import_product"


class AutomationSchedule(StrEnum):
    """When a rule is eligible to run."""

    MANUAL = "manual"
    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"


class AutomationRule(TenantScopedBase):
    """A tenant-defined automation rule."""

    __tablename__ = "automation_rules"

    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_automation_rules_tenant_name"),
        Index("ix_automation_rules_tenant_active", "tenant_id", "is_active"),
    )

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[AutomationAction] = mapped_column(
        Enum(
            AutomationAction,
            name="automation_action",
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
    )
    schedule: Mapped[AutomationSchedule] = mapped_column(
        Enum(
            AutomationSchedule,
            name="automation_schedule",
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        default=AutomationSchedule.MANUAL,
    )
    store_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    #: Action-specific knobs (product id, since_days, rule id, …).
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class AutomationRun(TenantScopedBase):
    """One execution of an automation rule."""

    __tablename__ = "automation_runs"

    __table_args__ = (
        Index("ix_automation_runs_tenant_created", "tenant_id", "created_at"),
        Index("ix_automation_runs_tenant_rule", "tenant_id", "rule_id"),
    )

    rule_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("automation_rules.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[SyncRunStatus] = mapped_column(
        Enum(
            SyncRunStatus,
            name="sync_run_status",
            values_callable=lambda enum: [member.value for member in enum],
            create_constraint=False,
            create_type=False,
        ),
        nullable=False,
        default=SyncRunStatus.RUNNING,
    )
    trigger: Mapped[str] = mapped_column(String(32), nullable=False, default="scheduled")
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


__all__ = [
    "AutomationAction",
    "AutomationRule",
    "AutomationRun",
    "AutomationSchedule",
]
