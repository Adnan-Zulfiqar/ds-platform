"""Prompt and prompt-execution repositories.

`PromptRepository` extends `BaseRepository`, not the tenant-scoped variant —
the same choice `RoleRepository` makes, for the same reason: `ai_prompts` is
platform-global reference data with no `tenant_id`. `PromptExecutionRepository`
extends `TenantScopedRepository`: an execution belongs to whichever tenant
triggered it, and reads must never cross that boundary.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.ai_prompt import AIPrompt, PromptExecution
from app.repositories.base import BaseRepository, TenantScopedRepository
from app.schemas.common import ListQueryParams


class PromptRepository(BaseRepository[AIPrompt]):
    sortable_fields = frozenset({"created_at", "updated_at", "name", "version"})
    searchable_fields = frozenset({"name", "description"})

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, AIPrompt)

    async def get_active(self, name: str) -> AIPrompt | None:
        query = self._base_query().where(AIPrompt.name == name, AIPrompt.active.is_(True))
        return (await self.session.execute(query)).scalar_one_or_none()

    async def get_active_or_raise(self, name: str) -> AIPrompt:
        prompt = await self.get_active(name)
        if prompt is None:
            raise NotFoundError.for_resource("Prompt", name)
        return prompt

    async def get_version(self, name: str, version: int) -> AIPrompt | None:
        query = self._base_query().where(AIPrompt.name == name, AIPrompt.version == version)
        return (await self.session.execute(query)).scalar_one_or_none()

    async def list_versions(self, name: str) -> Sequence[AIPrompt]:
        """Every version of `name`, newest first — the version-history view."""
        query = self._base_query().where(AIPrompt.name == name).order_by(AIPrompt.version.desc())
        return (await self.session.execute(query)).scalars().all()

    async def next_version_number(self, name: str) -> int:
        query = select(func.max(AIPrompt.version)).where(AIPrompt.name == name)
        current = (await self.session.execute(query)).scalar_one_or_none()
        return (current or 0) + 1

    async def list_active(self, params: ListQueryParams) -> tuple[Sequence[AIPrompt], int]:
        """One row per prompt name — the summary list view.

        Every name that has ever been created has exactly one active version
        by construction (creation auto-activates version 1; activation always
        deactivates the previous holder before activating the next), so
        filtering to `active = true` is equivalent to "one row per name"
        without a separate GROUP BY.
        """
        query = self._base_query().where(AIPrompt.active.is_(True))
        query = self._apply_search(query, params)

        count_query = select(func.count()).select_from(query.subquery())
        total = (await self.session.execute(count_query)).scalar_one()

        query = self._apply_sorting(query, params)
        query = query.offset(params.offset).limit(params.limit)

        rows = (await self.session.execute(query)).scalars().all()
        return rows, total

    async def activate(self, *, name: str, version: int) -> AIPrompt:
        """Make `version` the active row for `name`, deactivating whichever
        version currently holds it.

        Two sequential flushes, not one: deactivating the old row and
        activating the new one in the same flush leaves SQLAlchemy free to
        order the two UPDATE statements either way, and activating before
        deactivating would collide with the partial unique index that allows
        only one active row per name. Flushing the deactivation first
        guarantees the database is never asked to hold two active rows for
        the same name at once. This also serves "rollback" — activating an
        older version is the same operation as activating any other version.
        """
        target = await self.get_version(name, version)
        if target is None:
            raise NotFoundError.for_resource("Prompt", f"{name} v{version}")
        if target.active:
            return target

        current = await self.get_active(name)
        if current is not None:
            current.active = False
            await self.session.flush()

        target.active = True
        await self.session.flush()
        return target


class PromptExecutionRepository(TenantScopedRepository[PromptExecution]):
    sortable_fields = frozenset({"created_at", "updated_at", "duration_ms"})
    searchable_fields = frozenset({"prompt_name"})

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, PromptExecution)


__all__ = ["PromptExecutionRepository", "PromptRepository"]
