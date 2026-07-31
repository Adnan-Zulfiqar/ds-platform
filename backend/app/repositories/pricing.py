"""Pricing rules and price-change audit data access."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pricing import PriceChange, PricingRule, PricingScope
from app.repositories.base import TenantScopedRepository


class PricingRuleRepository(TenantScopedRepository[PricingRule]):
    sortable_fields = frozenset({"created_at", "updated_at", "name", "priority", "scope"})
    searchable_fields = frozenset({"name"})

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, PricingRule)

    async def list_active(self) -> list[PricingRule]:
        query = (
            self._base_query()
            .where(PricingRule.is_active.is_(True))
            .order_by(PricingRule.priority.desc(), PricingRule.created_at.asc())
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def find_candidates(
        self,
        *,
        product_id: uuid.UUID,
        store_id: uuid.UUID | None,
        category_id: str | None,
    ) -> list[PricingRule]:
        """Active rules that could apply to this product, broadest match first.

        The engine picks the narrowest matching scope among these; fetching
        candidates in one query avoids N lookups per product during a bulk apply.
        """
        clauses = [PricingRule.scope == PricingScope.GLOBAL]
        clauses.append(
            (PricingRule.scope == PricingScope.PRODUCT) & (PricingRule.product_id == product_id)
        )
        if store_id is not None:
            clauses.append(
                (PricingRule.scope == PricingScope.STORE) & (PricingRule.store_id == store_id)
            )
        if category_id is not None:
            clauses.append(
                (PricingRule.scope == PricingScope.CATEGORY)
                & (PricingRule.category_id == category_id)
            )
        from sqlalchemy import or_

        query = (
            self._base_query()
            .where(PricingRule.is_active.is_(True), or_(*clauses))
            .order_by(PricingRule.priority.desc())
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())


class PriceChangeRepository(TenantScopedRepository[PriceChange]):
    sortable_fields = frozenset({"created_at", "applied_at", "new_price"})
    searchable_fields = frozenset({"reason"})

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, PriceChange)
