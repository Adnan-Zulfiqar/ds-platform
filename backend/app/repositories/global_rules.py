"""Shipping rules and rule-version history data access (M3A-2).

Both repositories are tenant-scoped, so the ``tenant_id`` predicate is
injected by :class:`TenantScopedRepository._base_query` and cannot be
forgotten at a call site.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pricing import GlobalRuleKind, GlobalRuleVersion, PricingScope, ShippingRule
from app.repositories.base import TenantScopedRepository


class ShippingRuleRepository(TenantScopedRepository[ShippingRule]):
    sortable_fields = frozenset({"created_at", "updated_at", "name", "priority", "scope"})
    searchable_fields = frozenset({"name"})

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, ShippingRule)

    async def find_candidates(
        self,
        *,
        product_id: uuid.UUID | None,
        variant_id: uuid.UUID | None = None,
        store_id: uuid.UUID | None = None,
        category_id: str | None = None,
    ) -> list[ShippingRule]:
        """Active rules that could apply here, in one query.

        Mirrors ``PricingRuleRepository.find_candidates`` deliberately: the
        two must offer the same candidate set for the shared resolver to
        produce the same precedence for both kinds of rule.
        """
        clauses = [ShippingRule.scope == PricingScope.GLOBAL]
        if product_id is not None:
            clauses.append(
                (ShippingRule.scope == PricingScope.PRODUCT)
                & (ShippingRule.product_id == product_id)
            )
        if variant_id is not None:
            clauses.append(
                (ShippingRule.scope == PricingScope.VARIANT)
                & (ShippingRule.variant_id == variant_id)
            )
        if store_id is not None:
            clauses.append(
                (ShippingRule.scope == PricingScope.STORE) & (ShippingRule.store_id == store_id)
            )
        if category_id is not None:
            clauses.append(
                (ShippingRule.scope == PricingScope.CATEGORY)
                & (ShippingRule.category_id == category_id)
            )
        from sqlalchemy import or_

        query = (
            self._base_query()
            .where(ShippingRule.is_active.is_(True))
            .where(or_(*clauses))
            .order_by(ShippingRule.priority.desc(), ShippingRule.created_at.asc())
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def active_global(self) -> ShippingRule | None:
        query = (
            self._base_query()
            .where(ShippingRule.scope == PricingScope.GLOBAL)
            .where(ShippingRule.is_active.is_(True))
            .limit(1)
        )
        return (await self.session.execute(query)).scalars().first()


class GlobalRuleVersionRepository(TenantScopedRepository[GlobalRuleVersion]):
    """Append-only history.

    Deliberately exposes no update or delete method. The base class provides
    ``soft_delete``; nothing here calls it, and nothing should -- an audit
    trail that can be edited is not one. Additions go through
    :class:`app.services.global_rules.GlobalRuleService`, which is the only
    place version numbers are assigned.
    """

    sortable_fields = frozenset({"created_at", "version"})
    searchable_fields = frozenset({"note"})

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, GlobalRuleVersion)

    async def history_for(
        self, *, rule_kind: GlobalRuleKind, rule_id: uuid.UUID
    ) -> list[GlobalRuleVersion]:
        """Every version of one rule, newest first."""
        query = (
            self._base_query()
            .where(GlobalRuleVersion.rule_kind == rule_kind)
            .where(GlobalRuleVersion.rule_id == rule_id)
            .order_by(GlobalRuleVersion.version.desc())
        )
        return list((await self.session.execute(query)).scalars().all())

    async def latest_version_number(self, *, rule_kind: GlobalRuleKind, rule_id: uuid.UUID) -> int:
        """Highest version recorded, or 0 when there is no history yet.

        Read inside the same transaction as the insert that follows it. The
        unique constraint on ``(tenant_id, rule_kind, rule_id, version)`` is
        what actually prevents two concurrent writers agreeing on the same
        number -- this read only picks the candidate.
        """
        query = (
            select(GlobalRuleVersion.version)
            .where(GlobalRuleVersion.tenant_id == self._require_tenant())
            .where(GlobalRuleVersion.rule_kind == rule_kind)
            .where(GlobalRuleVersion.rule_id == rule_id)
            .order_by(GlobalRuleVersion.version.desc())
            .limit(1)
        )
        return (await self.session.execute(query)).scalars().first() or 0

    async def version_at(
        self, *, rule_kind: GlobalRuleKind, rule_id: uuid.UUID, version: int
    ) -> GlobalRuleVersion | None:
        """The specific version a historical calculation was produced under."""
        query = (
            self._base_query()
            .where(GlobalRuleVersion.rule_kind == rule_kind)
            .where(GlobalRuleVersion.rule_id == rule_id)
            .where(GlobalRuleVersion.version == version)
        )
        return (await self.session.execute(query)).scalars().first()

    def _require_tenant(self) -> uuid.UUID:
        from app.core.context import require_tenant_id

        return require_tenant_id()


__all__ = ["GlobalRuleVersionRepository", "ShippingRuleRepository"]
