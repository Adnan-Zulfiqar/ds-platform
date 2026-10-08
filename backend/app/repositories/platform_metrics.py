"""Platform-wide counts for the operator dashboard (Admin Control Center, D-019).

Unscoped by design: the dashboard answers "how is the whole platform doing",
which no single tenant can. It is on the CLAUDE.md §4 closed list and returns
**counts and dates only**: never a tenant-owned row, never a name, never an
error message. Anything about one workspace goes through that workspace's
own tenant-scoped repositories instead (see ``app.api.deps.platform_workspace``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import TenantSubscription
from app.models.inventory import InventorySyncRun
from app.models.notification import Notification
from app.models.order import Order, OrderSyncRun, SyncRunStatus
from app.models.pipeline_bulk import PipelineBulkRun, PipelineBulkRunStatus
from app.models.platform_admin import PlatformAdminAudit, PlatformAdminSession
from app.models.product import ImportStatus, Product, ProductImport
from app.models.shopify import ListingSyncStatus, StoreListing
from app.models.store import Store
from app.models.supplier_order import SupplierOrder
from app.models.tenant import Tenant
from app.models.user import User

#: A sync run still ``running`` after this long has stopped. Order and
#: inventory syncs have no sweep of their own, and a stuck run blocks every
#: later sync of the same workspace (``ConflictError`` on start).
SYNC_STUCK_AFTER = timedelta(minutes=30)
#: Matches ``services.pipeline_bulk.STALE_AFTER``.
PIPELINE_STUCK_AFTER = timedelta(minutes=20)


@dataclass(frozen=True, slots=True)
class DailyCount:
    day: date
    count: int


@dataclass(slots=True)
class PlatformSnapshot:
    generated_at: datetime
    tenants_by_status: dict[str, int] = field(default_factory=dict)
    tenants_new_7d: int = 0
    tenants_new_30d: int = 0
    users_active: int = 0
    users_new_7d: int = 0
    stores_by_status: dict[str, int] = field(default_factory=dict)
    stores_by_platform: dict[str, int] = field(default_factory=dict)
    products_total: int = 0
    listings_by_status: dict[str, int] = field(default_factory=dict)
    orders_24h: int = 0
    orders_7d: int = 0
    subscriptions_by_plan: dict[str, int] = field(default_factory=dict)
    subscriptions_by_status: dict[str, int] = field(default_factory=dict)
    trials_ending_7d: int = 0
    failed_24h: dict[str, int] = field(default_factory=dict)
    stuck: dict[str, int] = field(default_factory=dict)
    operator_sessions_open: int = 0
    security_failures_24h: int = 0
    signups_30d: list[DailyCount] = field(default_factory=list)
    orders_14d: list[DailyCount] = field(default_factory=list)


class PlatformMetrics:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _scalar(self, query: Any) -> int:
        return int((await self.session.execute(query)).scalar_one() or 0)

    async def _grouped(self, column: Any, *where: Any) -> dict[str, int]:
        query = select(column, func.count()).where(*where).group_by(column)
        rows = (await self.session.execute(query)).all()
        return {str(getattr(key, "value", key) or "none"): int(n) for key, n in rows}

    async def _daily(self, created: Any, since: datetime, *where: Any) -> list[DailyCount]:
        day = cast(func.timezone("UTC", created), Date)
        query = (
            select(day, func.count()).where(created >= since, *where).group_by(day).order_by(day)
        )
        found = {d: int(n) for d, n in (await self.session.execute(query)).all()}
        start = since.date()
        days = (datetime.now(UTC).date() - start).days + 1
        # Every day appears, zero included, so a chart never hides a gap.
        return [
            DailyCount(day=start + timedelta(days=i), count=found.get(start + timedelta(days=i), 0))
            for i in range(days)
        ]

    async def snapshot(self) -> PlatformSnapshot:
        now = datetime.now(UTC)
        day, week, month = (
            now - timedelta(days=1),
            now - timedelta(days=7),
            now - timedelta(days=30),
        )
        live_tenant = Tenant.deleted_at.is_(None)
        snap = PlatformSnapshot(generated_at=now)

        snap.tenants_by_status = await self._grouped(Tenant.status, live_tenant)
        snap.tenants_new_7d = await self._scalar(
            select(func.count()).select_from(Tenant).where(live_tenant, Tenant.created_at >= week)
        )
        snap.tenants_new_30d = await self._scalar(
            select(func.count()).select_from(Tenant).where(live_tenant, Tenant.created_at >= month)
        )
        live_user = User.deleted_at.is_(None)
        snap.users_active = await self._scalar(
            select(func.count()).select_from(User).where(live_user, User.is_active.is_(True))
        )
        snap.users_new_7d = await self._scalar(
            select(func.count()).select_from(User).where(live_user, User.created_at >= week)
        )
        live_store = Store.deleted_at.is_(None)
        snap.stores_by_status = await self._grouped(Store.status, live_store)
        snap.stores_by_platform = await self._grouped(Store.platform, live_store)
        snap.products_total = await self._scalar(
            select(func.count()).select_from(Product).where(Product.deleted_at.is_(None))
        )
        snap.listings_by_status = await self._grouped(
            StoreListing.status, StoreListing.deleted_at.is_(None)
        )
        live_order = Order.deleted_at.is_(None)
        snap.orders_24h = await self._scalar(
            select(func.count()).select_from(Order).where(live_order, Order.created_at >= day)
        )
        snap.orders_7d = await self._scalar(
            select(func.count()).select_from(Order).where(live_order, Order.created_at >= week)
        )
        live_sub = TenantSubscription.deleted_at.is_(None)
        snap.subscriptions_by_plan = await self._grouped(TenantSubscription.plan, live_sub)
        snap.subscriptions_by_status = await self._grouped(TenantSubscription.status, live_sub)
        snap.trials_ending_7d = await self._scalar(
            select(func.count())
            .select_from(TenantSubscription)
            .where(
                live_sub,
                TenantSubscription.plan.is_(None),
                TenantSubscription.trial_ends_at >= now,
                TenantSubscription.trial_ends_at < now + timedelta(days=7),
            )
        )

        snap.failed_24h = {
            "order_syncs": await self._count_since(
                OrderSyncRun, OrderSyncRun.status == SyncRunStatus.FAILED, day
            ),
            "inventory_syncs": await self._count_since(
                InventorySyncRun, InventorySyncRun.status == SyncRunStatus.FAILED, day
            ),
            "product_imports": await self._count_since(
                ProductImport, ProductImport.status == ImportStatus.FAILED, day
            ),
            "pipeline_runs": await self._count_since(
                PipelineBulkRun, PipelineBulkRun.status == PipelineBulkRunStatus.FAILED, day
            ),
            "supplier_orders": await self._count_since(
                SupplierOrder, SupplierOrder.status == "failed", day
            ),
            "notification_emails": await self._count_since(
                Notification, Notification.email_status == "failed", day
            ),
        }
        snap.listings_by_status.setdefault(ListingSyncStatus.ERROR.value, 0)
        snap.stuck = {
            "order_syncs": await self._scalar(
                select(func.count())
                .select_from(OrderSyncRun)
                .where(
                    OrderSyncRun.status == SyncRunStatus.RUNNING,
                    OrderSyncRun.started_at < now - SYNC_STUCK_AFTER,
                )
            ),
            "inventory_syncs": await self._scalar(
                select(func.count())
                .select_from(InventorySyncRun)
                .where(
                    InventorySyncRun.status == SyncRunStatus.RUNNING,
                    InventorySyncRun.started_at < now - SYNC_STUCK_AFTER,
                )
            ),
            "pipeline_runs": await self._scalar(
                select(func.count())
                .select_from(PipelineBulkRun)
                .where(
                    PipelineBulkRun.status == PipelineBulkRunStatus.RUNNING,
                    func.coalesce(PipelineBulkRun.heartbeat_at, PipelineBulkRun.started_at)
                    < now - PIPELINE_STUCK_AFTER,
                )
            ),
        }
        snap.operator_sessions_open = await self._scalar(
            select(func.count())
            .select_from(PlatformAdminSession)
            .where(PlatformAdminSession.revoked_at.is_(None), PlatformAdminSession.expires_at > now)
        )
        snap.security_failures_24h = await self._scalar(
            select(func.count())
            .select_from(PlatformAdminAudit)
            .where(PlatformAdminAudit.outcome == "failure", PlatformAdminAudit.created_at >= day)
        )
        snap.signups_30d = await self._daily(Tenant.created_at, month, live_tenant)
        snap.orders_14d = await self._daily(Order.created_at, now - timedelta(days=13), live_order)
        return snap

    async def _count_since(self, model: Any, condition: Any, since: datetime) -> int:
        return await self._scalar(
            select(func.count()).select_from(model).where(condition, model.created_at >= since)
        )


__all__ = [
    "PIPELINE_STUCK_AFTER",
    "SYNC_STUCK_AFTER",
    "DailyCount",
    "PlatformMetrics",
    "PlatformSnapshot",
    "database_answers",
    "migration_revision",
]


async def migration_revision(session: AsyncSession) -> str | None:
    """The schema revision this database is at, for the dashboard's system
    panel. ``None`` if the table is missing (a database built without
    Alembic, which only tests do)."""
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    try:
        async with session.begin_nested():
            row = (await session.execute(text("SELECT version_num FROM alembic_version"))).first()
    except DBAPIError:
        return None
    return str(row[0]) if row else None


async def database_answers(session: AsyncSession) -> bool:
    """On the request's own connection: the one actually serving the
    dashboard, rather than a second connection from the pool."""
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    try:
        async with session.begin_nested():
            await session.execute(text("SELECT 1"))
    except DBAPIError:
        return False
    return True
