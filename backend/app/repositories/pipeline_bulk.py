"""Tenant-scoped access for pipeline bulk runs and items.

``PipelineBulkRunTenantLookup`` is deliberately not a repository: one method,
returns a tenant id, cannot load a row onto a request path. Same shape as
``RuleApplicationTenantLookup``.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pipeline_bulk import PipelineBulkItemState, PipelineBulkRun, PipelineBulkRunItem
from app.repositories.base import TenantScopedRepository


class PipelineBulkRunRepository(TenantScopedRepository[PipelineBulkRun]):
    sortable_fields = frozenset({"created_at", "updated_at", "status", "finished_at"})

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, PipelineBulkRun)


class PipelineBulkRunItemRepository(TenantScopedRepository[PipelineBulkRunItem]):
    sortable_fields = frozenset({"created_at", "finished_at", "state"})

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, PipelineBulkRunItem)

    async def lock_next_pending(self, run_id: uuid.UUID) -> PipelineBulkRunItem | None:
        """Next unprocessed item, held for the rest of this transaction.

        Ordered by first-seen ``created_at`` so retries walk the snapshot, not
        a live catalogue. ``populate_existing`` so a cached pending copy cannot
        hide a terminal state another transaction already committed.
        """
        query = (
            self._base_query()
            .where(PipelineBulkRunItem.run_id == run_id)
            .where(PipelineBulkRunItem.state == PipelineBulkItemState.PENDING)
            .order_by(PipelineBulkRunItem.created_at.asc(), PipelineBulkRunItem.id.asc())
            .with_for_update()
            .limit(1)
            .execution_options(populate_existing=True)
        )
        return (await self.session.execute(query)).scalars().first()

    async def lock_by_id(self, item_id: uuid.UUID) -> PipelineBulkRunItem | None:
        query = (
            self._base_query()
            .where(PipelineBulkRunItem.id == item_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return (await self.session.execute(query)).scalars().first()


class PipelineBulkRunTenantLookup:
    """Resolves which tenant owns a run id, before context exists.

    Deliberately unscoped, and deliberately **not** a repository: it takes no
    base class, exposes exactly one question, and returns a tenant id and
    nothing else. Taking the tenant from a Celery payload would let a forged
    or stale message run one tenant's bulk job over another's catalogue.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def tenant_for(self, run_id: uuid.UUID) -> uuid.UUID | None:
        query = select(PipelineBulkRun.tenant_id).where(PipelineBulkRun.id == run_id)
        return (await self.session.execute(query)).scalars().first()


__all__ = [
    "PipelineBulkRunItemRepository",
    "PipelineBulkRunRepository",
    "PipelineBulkRunTenantLookup",
]
