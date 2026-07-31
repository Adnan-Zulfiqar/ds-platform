"""Automation rules and runs."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.automation import AutomationRule, AutomationRun, AutomationSchedule
from app.repositories.base import TenantScopedRepository


class AutomationRuleRepository(TenantScopedRepository[AutomationRule]):
    sortable_fields = frozenset({"created_at", "updated_at", "name", "last_run_at", "next_run_at"})
    searchable_fields = frozenset({"name"})

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, AutomationRule)

    async def list_due(self, *, now: datetime) -> list[AutomationRule]:
        """Active scheduled rules whose next_run_at is due (or never set)."""
        query = self._base_query().where(
            AutomationRule.is_active.is_(True),
            AutomationRule.schedule != AutomationSchedule.MANUAL,
            or_(AutomationRule.next_run_at.is_(None), AutomationRule.next_run_at <= now),
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())


class AutomationRunRepository(TenantScopedRepository[AutomationRun]):
    sortable_fields = frozenset({"created_at", "started_at", "finished_at", "status"})
    searchable_fields = frozenset({"summary", "error_message"})

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, AutomationRun)
