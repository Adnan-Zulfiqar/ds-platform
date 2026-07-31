"""Create and manage in-app notifications."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification, NotificationKind
from app.repositories.notification import NotificationRepository
from app.schemas.common import ListQueryParams
from app.services.base import BaseService


class NotificationService(BaseService):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.notifications = NotificationRepository(session)

    async def notify(
        self,
        *,
        kind: NotificationKind,
        title: str,
        body: str = "",
        href: str | None = None,
        payload: dict[str, Any] | None = None,
        user_id: uuid.UUID | None = None,
    ) -> Notification:
        return await self.notifications.create(
            kind=kind,
            title=title,
            body=body,
            href=href,
            payload=payload or {},
            user_id=user_id,
            is_read=False,
        )

    async def list(
        self, params: ListQueryParams, *, unread_only: bool = False
    ) -> tuple[list[Notification], int]:
        filters: dict[str, object] | None = {"is_read": False} if unread_only else None
        rows, total = await self.notifications.list(params, filters=filters)
        return list(rows), total

    async def unread_count(self, *, user_id: uuid.UUID | None = None) -> int:
        return await self.notifications.unread_count(user_id=user_id)

    async def mark_read(self, notification_id: uuid.UUID) -> Notification:
        return await self.notifications.mark_read(notification_id, read_at=datetime.now(UTC))

    async def mark_all_read(self, *, user_id: uuid.UUID | None = None) -> int:
        return await self.notifications.mark_all_read(read_at=datetime.now(UTC), user_id=user_id)

    async def cleanup(self, *, older_than_days: int = 90) -> int:
        cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
        return await self.notifications.delete_older_than(cutoff=cutoff)
