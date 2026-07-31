"""Dynamic pricing engine.

Rules are resolved product > category > store > global. Within a scope level,
higher ``priority`` wins. Currency conversion is a hook that currently returns
the amount unchanged — FX belongs in a later phase once a rate source exists.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.models.notification import NotificationKind
from app.models.pricing import PriceChange, PricingRule, PricingScope, PricingStrategy
from app.models.product import Product
from app.repositories.pricing import PriceChangeRepository, PricingRuleRepository
from app.repositories.product import ProductRepository
from app.schemas.common import ListQueryParams
from app.schemas.pricing import (
    PricePreviewItem,
    PricePreviewResponse,
    PricingApplyRequest,
    PricingRuleCreate,
    PricingRuleUpdate,
)
from app.services.base import BaseService
from app.services.notification_service import NotificationService

_SCOPE_RANK = {
    PricingScope.PRODUCT: 4,
    PricingScope.CATEGORY: 3,
    PricingScope.STORE: 2,
    PricingScope.GLOBAL: 1,
}

_MONEY_QUANT = Decimal("0.0001")


def convert_currency(
    amount: Decimal, *, from_currency: str | None, to_currency: str | None
) -> Decimal:
    """Currency conversion hook.

    Identity today: there is no rate feed wired in. Returning the input rather
    than inventing a rate keeps sell prices honest until a real FX source lands.
    """
    _ = from_currency, to_currency
    return amount


def compute_sell_price(*, cost: Decimal, rule: PricingRule) -> Decimal:
    """Apply one rule's strategy to a cost price."""
    if rule.strategy is PricingStrategy.PERCENTAGE_MARKUP:
        if rule.markup_percent is None:
            raise ValidationError("Percentage markup rules require markup_percent.")
        price = cost * (Decimal("1") + rule.markup_percent / Decimal("100"))
    elif rule.strategy is PricingStrategy.FIXED_MARKUP:
        if rule.markup_fixed is None:
            raise ValidationError("Fixed markup rules require markup_fixed.")
        price = cost + rule.markup_fixed
    else:
        tiered = _tiered_price(cost, rule.tiers)
        # No matching tier — fall back to cost so apply never invents a gain.
        price = cost if tiered is None else tiered

    if rule.min_profit is not None:
        price = max(price, cost + rule.min_profit)
    if rule.max_price is not None:
        price = min(price, rule.max_price)

    return price.quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP)


def _tiered_price(cost: Decimal, tiers: list[dict[str, Any]]) -> Decimal | None:
    for tier in tiers:
        try:
            min_cost = Decimal(str(tier.get("min_cost", tier.get("minCost", "0"))))
            max_raw = tier.get("max_cost", tier.get("maxCost"))
            max_cost = Decimal(str(max_raw)) if max_raw is not None else None
            markup = Decimal(str(tier.get("markup_percent", tier.get("markupPercent", "0"))))
        except (TypeError, ValueError, ArithmeticError):
            continue
        if cost < min_cost:
            continue
        if max_cost is not None and cost > max_cost:
            continue
        return cost * (Decimal("1") + markup / Decimal("100"))
    return None


def select_rule(
    candidates: list[PricingRule],
    *,
    product_id: uuid.UUID,
    store_id: uuid.UUID | None,
    category_id: str | None,
) -> PricingRule | None:
    """Pick the narrowest matching rule; priority breaks ties."""
    matching: list[PricingRule] = []
    for rule in candidates:
        if rule.scope is PricingScope.PRODUCT and rule.product_id == product_id:
            matching.append(rule)
        elif (
            rule.scope is PricingScope.CATEGORY
            and category_id is not None
            and rule.category_id == category_id
        ):
            matching.append(rule)
        elif (
            rule.scope is PricingScope.STORE and store_id is not None and rule.store_id == store_id
        ):
            matching.append(rule)
        elif rule.scope is PricingScope.GLOBAL:
            matching.append(rule)
    if not matching:
        return None
    matching.sort(key=lambda r: (_SCOPE_RANK[r.scope], r.priority), reverse=True)
    return matching[0]


class PricingEngine(BaseService):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.rules = PricingRuleRepository(session)
        self.changes = PriceChangeRepository(session)
        self.products = ProductRepository(session)
        self.notifications = NotificationService(session)

    async def create_rule(self, payload: PricingRuleCreate) -> PricingRule:
        self._validate_rule_payload(payload)
        return await self.rules.create(**payload.model_dump())

    async def update_rule(self, rule_id: uuid.UUID, payload: PricingRuleUpdate) -> PricingRule:
        rule = await self.rules.get_by_id_or_raise(rule_id)
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(rule, field, value)
        await self.session.flush()
        return rule

    async def delete_rule(self, rule_id: uuid.UUID) -> None:
        rule = await self.rules.get_by_id_or_raise(rule_id)
        await self.rules.soft_delete(rule)

    async def list_rules(self, params: ListQueryParams) -> tuple[list[PricingRule], int]:
        rows, total = await self.rules.list(params)
        return list(rows), total

    async def preview(
        self,
        *,
        product_ids: list[uuid.UUID] | None = None,
        store_id: uuid.UUID | None = None,
        limit: int = 100,
    ) -> PricePreviewResponse:
        products = await self._target_products(
            product_ids=product_ids, store_id=store_id, limit=limit
        )
        items: list[PricePreviewItem] = []
        would_change = 0
        for product in products:
            proposed, rule = await self._propose(product)
            current = product.sell_price
            if proposed != current:
                would_change += 1
            items.append(
                PricePreviewItem(
                    product_id=product.id,
                    title=product.title,
                    cost_price=product.cost_price_min,
                    current_sell_price=current,
                    proposed_sell_price=proposed,
                    rule_id=rule.id if rule else None,
                    rule_name=rule.name if rule else None,
                    currency=product.currency,
                )
            )
        return PricePreviewResponse(items=items, would_change=would_change)

    async def apply(
        self,
        request: PricingApplyRequest,
        *,
        applied_by_user_id: uuid.UUID | None = None,
    ) -> list[PriceChange]:
        products = await self._target_products(
            product_ids=request.product_ids,
            store_id=request.store_id,
            limit=request.limit,
        )
        changes: list[PriceChange] = []
        now = datetime.now(UTC)
        for product in products:
            proposed, rule = await self._propose(product)
            if proposed is None or proposed == product.sell_price:
                continue
            previous = product.sell_price
            product.sell_price = proposed
            change = await self.changes.create(
                product_id=product.id,
                rule_id=rule.id if rule else None,
                store_id=product.store_id,
                previous_price=previous,
                new_price=proposed,
                cost_price=product.cost_price_min,
                currency=product.currency,
                reason="rule_apply",
                applied_by_user_id=applied_by_user_id,
                applied_at=now,
            )
            changes.append(change)
        await self.session.flush()
        if changes:
            await self.notifications.notify(
                kind=NotificationKind.PRICE_CHANGED,
                title=f"Prices updated for {len(changes)} product(s)",
                body="Pricing rules were applied to the catalogue.",
                href="/pricing",
                payload={"changed": len(changes)},
            )
        return changes

    async def _propose(self, product: Product) -> tuple[Decimal | None, PricingRule | None]:
        cost = product.cost_price_min
        if cost is None:
            return None, None
        candidates = await self.rules.find_candidates(
            product_id=product.id,
            store_id=product.store_id,
            category_id=product.category_id,
        )
        rule = select_rule(
            candidates,
            product_id=product.id,
            store_id=product.store_id,
            category_id=product.category_id,
        )
        if rule is None:
            return None, None
        amount = convert_currency(cost, from_currency=product.currency, to_currency=rule.currency)
        return compute_sell_price(cost=amount, rule=rule), rule

    async def _target_products(
        self,
        *,
        product_ids: list[uuid.UUID] | None,
        store_id: uuid.UUID | None,
        limit: int,
    ) -> list[Product]:
        if product_ids:
            products: list[Product] = []
            for pid in product_ids[:limit]:
                product = await self.products.get_by_id(pid)
                if product is not None:
                    products.append(product)
            return products
        return await self.products.list_for_inventory(store_id=store_id, limit=limit)

    def _validate_rule_payload(self, payload: PricingRuleCreate) -> None:
        if payload.scope is PricingScope.PRODUCT and payload.product_id is None:
            raise ValidationError("Product-scoped rules require product_id.")
        if payload.scope is PricingScope.STORE and payload.store_id is None:
            raise ValidationError("Store-scoped rules require store_id.")
        if payload.scope is PricingScope.CATEGORY and not payload.category_id:
            raise ValidationError("Category-scoped rules require category_id.")
        if payload.strategy is PricingStrategy.PERCENTAGE_MARKUP and payload.markup_percent is None:
            raise ValidationError("percentage_markup requires markup_percent.")
        if payload.strategy is PricingStrategy.FIXED_MARKUP and payload.markup_fixed is None:
            raise ValidationError("fixed_markup requires markup_fixed.")
        if payload.strategy is PricingStrategy.TIERED and not payload.tiers:
            raise ValidationError("tiered strategy requires at least one tier.")
