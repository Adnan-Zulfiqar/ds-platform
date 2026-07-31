"""Analytics dashboard API schemas — real metrics only."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from app.schemas.base import CamelCaseModel


class AnalyticsSeriesPoint(CamelCaseModel):
    label: str
    revenue: Decimal
    orders: int
    profit: Decimal = Decimal("0")


class AnalyticsOrdersPoint(CamelCaseModel):
    label: str
    fulfilled: int
    pending: int
    cancelled: int


class AnalyticsTopProduct(CamelCaseModel):
    product_id: str
    title: str
    units: int
    revenue: Decimal | None = None


class AnalyticsRecentActivity(CamelCaseModel):
    kind: str
    title: str
    occurred_at: datetime
    href: str | None = None


class AnalyticsDashboard(CamelCaseModel):
    """Everything the dashboard needs in one payload.

    Computed from this platform's tables (and daily rollups when present).
    Nothing here is invented.
    """

    revenue: Decimal
    order_count: int
    product_count: int
    store_count: int
    connected_store_count: int
    inventory_units: int
    sync_runs_7d: int
    sync_failures_7d: int
    automation_runs_7d: int
    automation_failures_7d: int
    unread_notifications: int
    sales_series: list[AnalyticsSeriesPoint]
    orders_series: list[AnalyticsOrdersPoint]
    top_products: list[AnalyticsTopProduct]
    recent_activity: list[AnalyticsRecentActivity]
    period_start: date
    period_end: date
