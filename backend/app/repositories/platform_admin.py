"""Platform operators and their audit trail (Track E5, decision D-015).

Unscoped, like :class:`TenantRepository`, because these tables hold no tenant
data and sit above the tenancy boundary. Reachable only from the platform
service, which is reachable only behind ``RequirePlatformAdmin`` or the
platform login (both 404 unless ``PLATFORM_ADMIN_ALLOWED_CIDRS`` is set).
Listed in CLAUDE.md §4.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.platform_admin import PlatformAdmin, PlatformAdminAudit
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


__all__ = ["PlatformAdminAuditRepository", "PlatformAdminRepository"]
