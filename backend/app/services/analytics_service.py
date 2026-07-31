"""Real analytics for the dashboard.

Aggregates from orders, products, stores, sync runs, automation runs, and
notifications. Daily rollups are written by the Celery aggregator so the
dashboard does not re-scan months of rows on every load once history grows.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.core.context import require_tenant_id
from app.models.automation import AutomationRun
from app.models.inventory import InventorySyncRun
from app.models.order import FulfillmentStatus, Order, OrderSyncRun, SyncRunStatus
from app.models.product import Product
from app.models.store import Store, StoreStatus
from app.repositories.analytics import AnalyticsDailyRepository
from app.repositories.notification import NotificationRepository
from app.schemas.analytics import (
    AnalyticsDashboard,
    AnalyticsOrdersPoint,
    AnalyticsRecentActivity,
    AnalyticsSeriesPoint,
    AnalyticsTopProduct,
)
from app.services.base import BaseService


class AnalyticsService(BaseService):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.daily = AnalyticsDailyRepository(session)
        self.notifications = NotificationRepository(session)

    async def dashboard(self, *, period_days: int = 30) -> AnalyticsDashboard:
        end = date.today()
        start = end - timedelta(days=period_days - 1)
        since = datetime.combine(start, datetime.min.time(), tzinfo=UTC)
        week_ago = datetime.now(UTC) - timedelta(days=7)

        order_where = self._tenant_where(Order)
        product_where = self._tenant_where(Product)
        store_where = self._tenant_where(Store)

        revenue = await self._scalar(
            select(func.coalesce(func.sum(Order.total_amount), 0))
            .select_from(Order)
            .where(Order.external_created_at >= since),
            order_where,
        )
        order_count = await self._scalar(
            select(func.count(Order.id))
            .select_from(Order)
            .where(Order.external_created_at >= since),
            order_where,
        )
        product_count = await self._scalar(
            select(func.count(Product.id)).select_from(Product), product_where
        )
        inventory_units = await self._scalar(
            select(func.coalesce(func.sum(Product.stock_quantity), 0)).select_from(Product),
            product_where,
        )
        store_count = await self._scalar(
            select(func.count(Store.id)).select_from(Store), store_where
        )
        connected_store_count = await self._scalar(
            select(func.count(Store.id))
            .select_from(Store)
            .where(Store.status == StoreStatus.CONNECTED),
            store_where,
        )

        sync_runs_7d = await self._count_runs(OrderSyncRun, week_ago) + await self._count_runs(
            InventorySyncRun, week_ago
        )
        sync_failures_7d = await self._count_runs(
            OrderSyncRun, week_ago, status=SyncRunStatus.FAILED
        ) + await self._count_runs(InventorySyncRun, week_ago, status=SyncRunStatus.FAILED)
        automation_runs_7d = await self._count_runs(AutomationRun, week_ago)
        automation_failures_7d = await self._count_runs(
            AutomationRun, week_ago, status=SyncRunStatus.FAILED
        )

        sales_series = await self._sales_series(start, end)
        orders_series = await self._orders_series(start, end)
        top_products = await self._top_products(since)
        recent = await self._recent_activity()
        unread = await self.notifications.unread_count()

        return AnalyticsDashboard(
            revenue=Decimal(str(revenue)),
            order_count=int(order_count),
            product_count=int(product_count),
            store_count=int(store_count),
            connected_store_count=int(connected_store_count),
            inventory_units=int(inventory_units),
            sync_runs_7d=sync_runs_7d,
            sync_failures_7d=sync_failures_7d,
            automation_runs_7d=automation_runs_7d,
            automation_failures_7d=automation_failures_7d,
            unread_notifications=unread,
            sales_series=sales_series,
            orders_series=orders_series,
            top_products=top_products,
            recent_activity=recent,
            period_start=start,
            period_end=end,
        )

    async def aggregate_day(self, *, day: date | None = None) -> None:
        """Upsert today's (or the given day's) tenant-wide rollup."""
        target = day or date.today()
        start = datetime.combine(target, datetime.min.time(), tzinfo=UTC)
        end = start + timedelta(days=1)

        order_where = self._tenant_where(Order)
        product_where = self._tenant_where(Product)

        revenue = await self._scalar(
            select(func.coalesce(func.sum(Order.total_amount), 0))
            .select_from(Order)
            .where(Order.external_created_at >= start, Order.external_created_at < end),
            order_where,
        )
        order_count = await self._scalar(
            select(func.count(Order.id))
            .select_from(Order)
            .where(Order.external_created_at >= start, Order.external_created_at < end),
            order_where,
        )
        product_count = await self._scalar(
            select(func.count(Product.id)).select_from(Product), product_where
        )
        inventory_units = await self._scalar(
            select(func.coalesce(func.sum(Product.stock_quantity), 0)).select_from(Product),
            product_where,
        )
        sync_runs = await self._count_runs(OrderSyncRun, start) + await self._count_runs(
            InventorySyncRun, start
        )
        sync_failures = await self._count_runs(
            OrderSyncRun, start, status=SyncRunStatus.FAILED
        ) + await self._count_runs(InventorySyncRun, start, status=SyncRunStatus.FAILED)
        automation_runs = await self._count_runs(AutomationRun, start)
        automation_failures = await self._count_runs(
            AutomationRun, start, status=SyncRunStatus.FAILED
        )

        existing = await self.daily.get_for_day(day=target, store_id=None)
        values = {
            "revenue": Decimal(str(revenue)),
            "order_count": int(order_count),
            "product_count": int(product_count),
            "inventory_units": int(inventory_units),
            "sync_runs": sync_runs,
            "sync_failures": sync_failures,
            "automation_runs": automation_runs,
            "automation_failures": automation_failures,
        }
        if existing is None:
            await self.daily.create(day=target, store_id=None, **values)
        else:
            for key, value in values.items():
                setattr(existing, key, value)
            await self.session.flush()

    async def _sales_series(self, start: date, end: date) -> list[AnalyticsSeriesPoint]:
        rollups = await self.daily.list_between(start=start, end=end, store_id=None)
        if rollups:
            return [
                AnalyticsSeriesPoint(
                    label=row.day.isoformat(),
                    revenue=row.revenue,
                    orders=row.order_count,
                    profit=Decimal("0"),
                )
                for row in rollups
            ]
        # Fall back to live daily buckets when no rollups exist yet.
        points: list[AnalyticsSeriesPoint] = []
        cursor = start
        while cursor <= end:
            day_start = datetime.combine(cursor, datetime.min.time(), tzinfo=UTC)
            day_end = day_start + timedelta(days=1)
            revenue = await self._scalar(
                select(func.coalesce(func.sum(Order.total_amount), 0))
                .select_from(Order)
                .where(
                    Order.external_created_at >= day_start,
                    Order.external_created_at < day_end,
                ),
                self._tenant_where(Order),
            )
            orders = await self._scalar(
                select(func.count(Order.id))
                .select_from(Order)
                .where(
                    Order.external_created_at >= day_start,
                    Order.external_created_at < day_end,
                ),
                self._tenant_where(Order),
            )
            points.append(
                AnalyticsSeriesPoint(
                    label=cursor.isoformat(),
                    revenue=Decimal(str(revenue)),
                    orders=int(orders),
                )
            )
            cursor += timedelta(days=1)
        return points

    async def _orders_series(self, start: date, end: date) -> list[AnalyticsOrdersPoint]:
        since = datetime.combine(start, datetime.min.time(), tzinfo=UTC)
        where = self._tenant_where(Order)
        query = (
            select(
                func.count(Order.id).filter(
                    Order.fulfillment_status == FulfillmentStatus.DELIVERED
                ),
                func.count(Order.id).filter(
                    Order.fulfillment_status.in_(
                        (
                            FulfillmentStatus.PENDING,
                            FulfillmentStatus.AWAITING_PAYMENT,
                            FulfillmentStatus.PAID,
                            FulfillmentStatus.PROCESSING,
                            FulfillmentStatus.FULFILLED,
                            FulfillmentStatus.SHIPPED,
                        )
                    )
                ),
                func.count(Order.id).filter(
                    Order.fulfillment_status.in_(
                        (FulfillmentStatus.CANCELLED, FulfillmentStatus.REFUNDED)
                    )
                ),
            )
            .select_from(Order)
            .where(Order.external_created_at >= since)
        )
        if where is not None:
            query = query.where(where)
        fulfilled, pending, cancelled = (await self.session.execute(query)).one()
        return [
            AnalyticsOrdersPoint(
                label=f"{start.isoformat()}-{end.isoformat()}",
                fulfilled=int(fulfilled or 0),
                pending=int(pending or 0),
                cancelled=int(cancelled or 0),
            )
        ]

    async def _top_products(self, since: datetime) -> list[AnalyticsTopProduct]:
        # Without order-line aggregation of units sold per product id mapping,
        # rank catalogue products by order_count reputation from the supplier
        # and stock activity — honest about what we can measure today.
        where = self._tenant_where(Product)
        query = (
            select(Product)
            .order_by(Product.order_count.desc().nulls_last(), Product.stock_quantity.desc())
            .limit(5)
        )
        if where is not None:
            query = query.where(where)
        result = await self.session.execute(query)
        products = list(result.scalars().all())
        return [
            AnalyticsTopProduct(
                product_id=str(p.id),
                title=p.title,
                units=int(p.order_count or 0),
                revenue=None,
            )
            for p in products
        ]

    async def _recent_activity(self) -> list[AnalyticsRecentActivity]:
        from app.schemas.common import ListQueryParams

        rows, _ = await self.notifications.list(ListQueryParams(page=1, size=8))
        return [
            AnalyticsRecentActivity(
                kind=n.kind.value,
                title=n.title,
                occurred_at=n.created_at,
                href=n.href,
            )
            for n in rows
        ]

    def _tenant_where(self, model: Any) -> ColumnElement[bool]:
        """Tenant + soft-delete predicate for aggregate queries."""
        clause: ColumnElement[bool] = model.tenant_id == require_tenant_id()
        if hasattr(model, "deleted_at"):
            clause = clause & (model.deleted_at.is_(None))
        return clause

    async def _scalar(self, query: Select[Any], where_clause: ColumnElement[bool] | None) -> Any:
        if where_clause is not None:
            query = query.where(where_clause)
        return (await self.session.execute(query)).scalar_one()

    async def _count_runs(
        self,
        model: Any,
        since: datetime,
        *,
        status: SyncRunStatus | None = None,
    ) -> int:
        where = self._tenant_where(model)
        query = (
            select(func.count(model.id)).select_from(model).where(model.created_at >= since, where)
        )
        if status is not None:
            query = query.where(model.status == status)
        return int((await self.session.execute(query)).scalar_one())
