"""Daily analytics rollups.

Computed by ``analytics.aggregate`` from live tables and served by the
dashboard. Keeping a rollup means the dashboard does not re-scan every order
row on every page load once a tenant has months of history.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Index, Integer, Numeric, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TenantScopedBase

_MONEY = Numeric(16, 4)


class AnalyticsDaily(TenantScopedBase):
    """One day's aggregated metrics for a tenant (optionally per store)."""

    __tablename__ = "analytics_daily"

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "day",
            "store_id",
            name="uq_analytics_daily_tenant_day_store",
        ),
        Index("ix_analytics_daily_tenant_day", "tenant_id", "day"),
    )

    day: Mapped[date] = mapped_column(Date, nullable=False)
    store_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="CASCADE"),
        nullable=True,
    )
    revenue: Mapped[Decimal] = mapped_column(_MONEY, nullable=False, default=0)
    order_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    product_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    inventory_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sync_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sync_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    automation_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    automation_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


__all__ = ["AnalyticsDaily"]
