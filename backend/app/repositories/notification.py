"""In-app notification data access."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import require_tenant_id
from app.models.notification import Notification, NotificationEmailPreference
from app.repositories.base import TenantScopedRepository


class NotificationRepository(TenantScopedRepository[Notification]):
    sortable_fields = frozenset({"created_at", "updated_at", "read_at"})
    searchable_fields = frozenset({"title", "body"})

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Notification)

    async def has_payload(self, key: str, value: str) -> bool:
        """Whether this workspace already has a notification carrying
        ``payload[key] == value``: how a re-run broadcast stays idempotent."""
        # Deleted copies count too: a merchant who dismissed a broadcast
        # must not get it again from a re-run. Still this tenant only.
        query = (
            select(Notification.id)
            .where(
                Notification.tenant_id == require_tenant_id(),
                Notification.payload[key].astext == value,
            )
            .limit(1)
        )
        return (await self.session.execute(query)).first() is not None

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

    async def pending_email(self, *, limit: int) -> list[Notification]:
        """This tenant's notifications waiting to be emailed, oldest first.

        ``SKIP LOCKED``: two sweeps running at once take different rows, so a
        notification is never mailed twice by a race.
        """
        result = await self.session.execute(
            self._base_query()
            .where(Notification.email_status == "pending")
            .order_by(Notification.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list(result.scalars().all())


class NotificationEmailPreferenceRepository(TenantScopedRepository[NotificationEmailPreference]):
    """Per-user email opt-ins (Track E3)."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, NotificationEmailPreference)

    async def for_users(self, user_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
        if not user_ids:
            return {}
        result = await self.session.execute(
            self._base_query().where(NotificationEmailPreference.user_id.in_(user_ids))
        )
        return {row.user_id: list(row.kinds or []) for row in result.scalars().all()}

    async def get_for_user(self, user_id: uuid.UUID) -> NotificationEmailPreference | None:
        result = await self.session.execute(
            self._base_query().where(NotificationEmailPreference.user_id == user_id)
        )
        return result.scalar_one_or_none()
