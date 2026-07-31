"""In-app notification data access."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification
from app.repositories.base import TenantScopedRepository


class NotificationRepository(TenantScopedRepository[Notification]):
    sortable_fields = frozenset({"created_at", "updated_at", "read_at"})
    searchable_fields = frozenset({"title", "body"})

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Notification)

    async def unread_count(self, *, user_id: uuid.UUID | None = None) -> int:
        where_clause = self._base_query().whereclause
        query = (
            select(func.count(Notification.id))
            .select_from(Notification)
            .where(Notification.is_read.is_(False))
        )
        if where_clause is not None:
            query = query.where(where_clause)
        if user_id is not None:
            query = query.where(
                (Notification.user_id.is_(None)) | (Notification.user_id == user_id)
            )
        return int((await self.session.execute(query)).scalar_one())

    async def mark_read(self, notification_id: uuid.UUID, *, read_at: datetime) -> Notification:
        notification = await self.get_by_id_or_raise(notification_id)
        notification.is_read = True
        notification.read_at = read_at
        await self.session.flush()
        return notification

    async def mark_all_read(self, *, read_at: datetime, user_id: uuid.UUID | None = None) -> int:
        """Mark unread rows read. Returns how many rows changed."""
        stmt = (
            update(Notification)
            .where(Notification.is_read.is_(False), Notification.deleted_at.is_(None))
            .values(is_read=True, read_at=read_at)
        )
        # Tenant predicate must be applied explicitly on bulk UPDATE — the
        # repository base query is SELECT-shaped and cannot be reused here.
        from app.core.context import require_tenant_id

        stmt = stmt.where(Notification.tenant_id == require_tenant_id())
        if user_id is not None:
            stmt = stmt.where((Notification.user_id.is_(None)) | (Notification.user_id == user_id))
        result = await self.session.execute(stmt)
        await self.session.flush()
        return int(getattr(result, "rowcount", 0) or 0)

    async def delete_older_than(self, *, cutoff: datetime) -> int:
        """Soft-delete notifications older than cutoff. Returns count touched."""
        from app.core.context import require_tenant_id

        stmt = (
            update(Notification)
            .where(
                Notification.tenant_id == require_tenant_id(),
                Notification.deleted_at.is_(None),
                Notification.created_at < cutoff,
            )
            .values(deleted_at=cutoff)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return int(getattr(result, "rowcount", 0) or 0)
