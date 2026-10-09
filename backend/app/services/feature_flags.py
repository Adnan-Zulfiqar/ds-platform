"""Feature switches (Admin Control Center phase 8, D-019).

Each switch has a platform-wide default (``feature_flags``, seeded **on**)
and an optional per-workspace override (``tenant_feature_flags``), both
set only by operators in the console. The application asks one question,
``is_enabled(key)`` for the current workspace, at the place each feature
starts:

* ``ai_bulk_pipeline``: ``PipelineBulkRunService.create`` refuses a run;
* ``supplier_auto_ordering``: the auto-place task places nothing;
* ``channel_publishing``: publish readiness reports a blocker, so no
  channel publishes.

A switch nobody reads would be a disconnected button; every key here has a
reader, and ``KNOWN_FLAGS`` is the list a test holds them to.
"""

from __future__ import annotations

from typing import Final

from app.core.exceptions import PermissionDeniedError
from app.repositories.billing import FeatureFlagRepository, TenantFeatureFlagRepository
from app.services.base import BaseService

AI_BULK_PIPELINE: Final = "ai_bulk_pipeline"
SUPPLIER_AUTO_ORDERING: Final = "supplier_auto_ordering"
CHANNEL_PUBLISHING: Final = "channel_publishing"
KNOWN_FLAGS: Final = frozenset({AI_BULK_PIPELINE, SUPPLIER_AUTO_ORDERING, CHANNEL_PUBLISHING})


class FeatureDisabledError(PermissionDeniedError):
    code = "feature_disabled"
    message = "This feature is switched off for this workspace. Contact support."


class FeatureFlagService(BaseService):
    async def is_enabled(self, key: str) -> bool:
        """The workspace's override if it has one, else the platform default,
        else on (a switch that was never created cannot have been turned off)."""
        override = await TenantFeatureFlagRepository(self.session).by_key(key)
        if override is not None:
            return override.enabled
        flag = await FeatureFlagRepository(self.session).by_key(key)
        return True if flag is None else flag.enabled

    async def require(self, key: str) -> None:
        if not await self.is_enabled(key):
            raise FeatureDisabledError()


__all__ = [
    "AI_BULK_PIPELINE",
    "CHANNEL_PUBLISHING",
    "KNOWN_FLAGS",
    "SUPPLIER_AUTO_ORDERING",
    "FeatureDisabledError",
    "FeatureFlagService",
]
