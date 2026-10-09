"""Platform-wide settings: maintenance mode and announcements (Admin
Control Center phase 10, D-019).

**Maintenance mode** makes the merchant API read-only: every non-GET
request to a tenant route answers 503 ``maintenance``, while sign-in, the
operator console and every inbound webhook or OAuth callback keep working
(a refused webhook can lose data; some providers retry, some do not).
Background jobs keep running; maintenance is for "no new changes from the
app", not a full stop.

The flag is read on every write request, so it is cached in-process for a
few seconds: an operator's change reaches every worker within
``MAINTENANCE_CACHE_SECONDS``, and the database is not asked on every call.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from app.core.exceptions import AppError, NotFoundError, ValidationError
from app.models.platform_admin import PlatformAnnouncement
from app.repositories.platform_admin import PlatformSettingsRepository
from app.services.base import BaseService

MAINTENANCE_KEY: Final = "maintenance"
MAINTENANCE_CACHE_SECONDS: Final = 5.0
ANNOUNCEMENT_LEVELS: Final = frozenset({"info", "warning", "critical"})

_cache: dict[str, tuple[float, Maintenance]] = {}


class MaintenanceModeError(AppError):
    code = "maintenance"
    status_code = 503
    message = "DropPilot is in maintenance. Changes are paused; please try again shortly."


@dataclass(frozen=True, slots=True)
class Maintenance:
    enabled: bool
    message: str | None
    updated_at: datetime | None


class PlatformSettingsService(BaseService):
    async def maintenance(self, *, cached: bool = True) -> Maintenance:
        now = time.monotonic()
        hit = _cache.get(MAINTENANCE_KEY)
        if cached and hit is not None and now - hit[0] < MAINTENANCE_CACHE_SECONDS:
            return hit[1]
        row = await PlatformSettingsRepository(self.session).get(MAINTENANCE_KEY)
        value = row.value if row else {}
        state = Maintenance(
            enabled=bool(value.get("enabled", False)),
            message=value.get("message") or None,
            updated_at=row.updated_at if row else None,
        )
        _cache[MAINTENANCE_KEY] = (now, state)
        return state

    async def set_maintenance(
        self, *, enabled: bool, message: str | None, admin_id: uuid.UUID
    ) -> Maintenance:
        await PlatformSettingsRepository(self.session).put(
            MAINTENANCE_KEY,
            {"enabled": enabled, "message": (message or "")[:500] or None},
            admin_id=admin_id,
        )
        _cache.pop(MAINTENANCE_KEY, None)
        return await self.maintenance(cached=False)

    async def active_announcements(self) -> list[PlatformAnnouncement]:
        return await PlatformSettingsRepository(self.session).active_announcements(
            datetime.now(UTC)
        )

    async def announcements(self) -> list[PlatformAnnouncement]:
        return await PlatformSettingsRepository(self.session).recent_announcements()

    async def announce(
        self,
        *,
        title: str,
        body: str,
        level: str,
        starts_at: datetime | None,
        ends_at: datetime | None,
        admin_id: uuid.UUID,
    ) -> PlatformAnnouncement:
        if level not in ANNOUNCEMENT_LEVELS:
            raise ValidationError(f"Level must be one of {sorted(ANNOUNCEMENT_LEVELS)}.")
        start = starts_at or datetime.now(UTC)
        if ends_at is not None and ends_at <= start:
            raise ValidationError("An announcement must end after it starts.")
        return await PlatformSettingsRepository(self.session).add_announcement(
            title=title[:160],
            body=body[:2000],
            level=level,
            starts_at=start,
            ends_at=ends_at,
            created_by_admin_id=admin_id,
        )

    async def end_announcement(self, announcement_id: uuid.UUID) -> PlatformAnnouncement:
        row = await PlatformSettingsRepository(self.session).get_announcement(announcement_id)
        if row is None:
            raise NotFoundError.for_resource("Announcement", announcement_id)
        now = datetime.now(UTC)
        if row.ends_at is None or row.ends_at > now:
            row.ends_at = max(now, row.starts_at)
            await self.session.flush()
        return row


def maintenance_exempt(path: str) -> bool:
    """Inbound provider traffic and OAuth returns, which must never be
    refused: a dropped webhook can be a lost order or a missed compliance
    request."""
    tail = path.rstrip("/")
    return (
        tail.endswith(("/callback", "/webhook", "/marketplace-account-deletion", "/claim-install"))
        or tail.endswith("/webhooks/stripe")
        or ("/shopify/webhooks/" in tail and not tail.endswith("/reconcile"))
    )


__all__ = [
    "ANNOUNCEMENT_LEVELS",
    "MAINTENANCE_CACHE_SECONDS",
    "Maintenance",
    "MaintenanceModeError",
    "PlatformSettingsService",
    "maintenance_exempt",
]
