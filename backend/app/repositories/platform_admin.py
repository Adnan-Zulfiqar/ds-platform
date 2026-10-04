"""Platform operators and their audit trail (Track E5, decision D-015).

Unscoped, like :class:`TenantRepository`, because these tables hold no tenant
data and sit above the tenancy boundary. Reachable only from the platform
service, which is reachable only behind ``RequirePlatformAdmin`` or the
platform login (both 404 unless ``PLATFORM_ADMIN_ALLOWED_CIDRS`` is set).
Listed in CLAUDE.md §4.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inventory import InventorySyncRun
from app.models.notification import Notification
from app.models.order import OrderSyncRun, SyncRunStatus
from app.models.platform_admin import PlatformAdmin, PlatformAdminAudit
from app.models.shopify import ListingSyncStatus, StoreListing
from app.models.store import Store, StoreStatus
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories.base import BaseRepository


class PlatformAdminRepository(BaseRepository[PlatformAdmin]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, PlatformAdmin)

    async def get_by_email(self, email: str) -> PlatformAdmin | None:
        query = self._base_query().where(PlatformAdmin.email == email.strip().lower())
        return (await self.session.execute(query)).scalar_one_or_none()

    async def get_for_update(self, admin_id: uuid.UUID) -> PlatformAdmin | None:
        """Locked, so two logins with one code cannot both pass the replay
        check."""
        query = self._base_query().where(PlatformAdmin.id == admin_id).with_for_update()
        return (await self.session.execute(query)).scalar_one_or_none()


class PlatformAdminAuditRepository:
    """Append and read only. There is deliberately no update or delete."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def append(
        self,
        *,
        action: str,
        admin_id: uuid.UUID | None,
        client_ip: str | None,
        target_tenant_id: uuid.UUID | None = None,
        detail: dict[str, Any] | None = None,
    ) -> PlatformAdminAudit:
        row = PlatformAdminAudit(
            action=action,
            admin_id=admin_id,
            client_ip=(client_ip or "")[:64] or None,
            target_tenant_id=target_tenant_id,
            detail=detail or {},
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def recent(self, *, limit: int = 100) -> list[PlatformAdminAudit]:
        query = (
            select(PlatformAdminAudit)
            .order_by(PlatformAdminAudit.created_at.desc())
            .limit(min(limit, 500))
        )
        return list((await self.session.execute(query)).scalars().all())


@dataclass(frozen=True, slots=True)
class TenantDirectoryRow:
    """What an operator sees about a workspace: identity, state and sizes.
    Never a product, an order, a buyer or a credential."""

    id: uuid.UUID
    name: str
    slug: str
    status: str
    is_active: bool
    created_at: datetime
    users: int
    connected_stores: int


@dataclass(frozen=True, slots=True)
class TenantHealth:
    """Counts only (E5c). What is failing, not what the failing thing is."""

    tenant_id: uuid.UUID
    window_hours: int
    failed_order_syncs: int
    failed_inventory_syncs: int
    listings_in_error: int
    failed_notification_emails: int


class PlatformTenantDirectory:
    """Every workspace, with counts (Track E5b, D-015).

    The one request-path exception to CLAUDE.md §4, reachable only behind
    ``RequirePlatformAdmin``. It reads ``tenants`` plus two aggregate counts
    and returns :class:`TenantDirectoryRow`, so no tenant-owned row can leave
    through it.
    """

    MAX_PAGE = 100

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def page(
        self,
        *,
        page: int,
        size: int,
        q: str | None = None,
        tenant_id: uuid.UUID | None = None,
    ) -> tuple[list[TenantDirectoryRow], int]:
        size = max(1, min(size, self.MAX_PAGE))
        users = (
            select(func.count(User.id))
            .where(User.tenant_id == Tenant.id, User.deleted_at.is_(None))
            .scalar_subquery()
        )
        stores = (
            select(func.count(Store.id))
            .where(
                Store.tenant_id == Tenant.id,
                Store.deleted_at.is_(None),
                Store.status == StoreStatus.CONNECTED,
            )
            .scalar_subquery()
        )
        where: list[ColumnElement[bool]] = [Tenant.deleted_at.is_(None)]
        if tenant_id is not None:
            where.append(Tenant.id == tenant_id)
        if q and q.strip():
            # Bound parameter, never interpolated; escape LIKE wildcards.
            needle = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where.append(
                or_(
                    Tenant.name.ilike(f"%{needle}%", escape="\\"),
                    Tenant.slug.ilike(f"%{needle}%", escape="\\"),
                )
            )
        total = int(
            (await self.session.execute(select(func.count(Tenant.id)).where(*where))).scalar_one()
        )
        rows = await self.session.execute(
            select(
                Tenant.id,
                Tenant.name,
                Tenant.slug,
                Tenant.status,
                Tenant.is_active,
                Tenant.created_at,
                users.label("users"),
                stores.label("stores"),
            )
            .where(*where)
            .order_by(Tenant.created_at.desc())
            .offset((max(page, 1) - 1) * size)
            .limit(size)
        )
        return [
            TenantDirectoryRow(
                id=r.id,
                name=r.name,
                slug=r.slug,
                status=str(getattr(r.status, "value", r.status)),
                is_active=r.is_active,
                created_at=r.created_at,
                users=int(r.users or 0),
                connected_stores=int(r.stores or 0),
            )
            for r in rows
        ], total

    async def health(self, tenant_id: uuid.UUID, *, window_hours: int = 24) -> TenantHealth:
        """Operational counts for one workspace. Every query is a ``count``
        filtered by ``tenant_id`` and returns a number, never a row."""
        since = datetime.now(UTC) - timedelta(hours=window_hours)

        async def count(query: Any) -> int:
            return int((await self.session.execute(query)).scalar_one())

        return TenantHealth(
            tenant_id=tenant_id,
            window_hours=window_hours,
            failed_order_syncs=await count(
                select(func.count(OrderSyncRun.id)).where(
                    OrderSyncRun.tenant_id == tenant_id,
                    OrderSyncRun.status == SyncRunStatus.FAILED,
                    OrderSyncRun.created_at >= since,
                )
            ),
            failed_inventory_syncs=await count(
                select(func.count(InventorySyncRun.id)).where(
                    InventorySyncRun.tenant_id == tenant_id,
                    InventorySyncRun.status == SyncRunStatus.FAILED,
                    InventorySyncRun.created_at >= since,
                )
            ),
            listings_in_error=await count(
                select(func.count(StoreListing.id)).where(
                    StoreListing.tenant_id == tenant_id,
                    StoreListing.deleted_at.is_(None),
                    StoreListing.status == ListingSyncStatus.ERROR,
                )
            ),
            failed_notification_emails=await count(
                select(func.count(Notification.id)).where(
                    Notification.tenant_id == tenant_id,
                    Notification.email_status == "failed",
                    Notification.created_at >= since,
                )
            ),
        )


__all__ = [
    "PlatformAdminAuditRepository",
    "PlatformAdminRepository",
    "PlatformTenantDirectory",
    "TenantDirectoryRow",
    "TenantHealth",
]
