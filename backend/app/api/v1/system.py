"""Public platform status for the merchant app (D-019): whether the
platform is in maintenance and which announcements are live. No sign-in, no
tenant data, nothing an operator did not write for everyone to read."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter

from app.api.deps import DbSession
from app.schemas.base import CamelCaseModel
from app.services.platform_settings import PlatformSettingsService

router = APIRouter(prefix="/system", tags=["system"])


class AnnouncementRead(CamelCaseModel):
    id: str
    title: str
    body: str
    level: str
    ends_at: datetime | None


class SystemStatusRead(CamelCaseModel):
    maintenance: bool
    maintenance_message: str | None
    announcements: list[AnnouncementRead]


@router.get("/status", response_model=SystemStatusRead, summary="Maintenance and announcements")
async def system_status(session: DbSession) -> SystemStatusRead:
    service = PlatformSettingsService(session)
    maintenance = await service.maintenance()
    return SystemStatusRead(
        maintenance=maintenance.enabled,
        maintenance_message=maintenance.message,
        announcements=[
            AnnouncementRead(
                id=str(a.id), title=a.title, body=a.body, level=a.level, ends_at=a.ends_at
            )
            for a in await service.active_announcements()
        ],
    )
