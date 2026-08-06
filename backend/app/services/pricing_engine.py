"""Dynamic pricing engine.

Rules are resolved product > category > store > global. Within a scope level,
higher ``priority`` wins.

Currency integrity: same-currency amounts may be used directly. Differing
currencies require a real FX quote from ``FxService``. A missing quote blocks
pricing calculations — never invent a 1:1 cross-currency rate.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import FxUnavailableError, ValidationError
from app.domain.fx import FxRateStatus
from app.domain.money import Money, normalise_currency
from app.models.notification import NotificationKind
from app.models.pricing import PriceChange, PricingRule, PricingScope, PricingStrategy
from app.models.product import Product
from app.repositories.pricing import PriceChangeRepository, PricingRuleRepository
from app.repositories.product import ProductRepository
from app.repositories.store import StoreRepository
from app.repositories.tenant import TenantRepository
from app.schemas.common import ListQueryParams
from app.schemas.draft_pricing import (
    DraftPricingApplyMode,
    DraftPricingApplyRequest,
    DraftPricingWorkspaceRead,
    DraftVariantPricingRow,
)
from app.schemas.pricing import (
    PricePreviewItem,
    PricePreviewResponse,
    PricingApplyRequest,
    PricingRuleCreate,
    PricingRuleUpdate,
)
from app.services.base import BaseService
from app.services.fx import FxService, get_fx_service
from app.services.notification_service import NotificationService

_SCOPE_RANK = {
    PricingScope.PRODUCT: 4,
    PricingScope.CATEGORY: 3,
    PricingScope.STORE: 2,
    PricingScope.GLOBAL: 1,
}

_MONEY_QUANT = Decimal("0.0001")
_CENT_QUANT = Decimal("0.01")

_BLOCK_MESSAGE = (
    "Pricing cannot be calculated because a valid currency conversion is not available."
)


def convert_currency(
    amount: Decimal, *, from_currency: str | None, to_currency: str | None
) -> Decimal:
    """Same-currency pass-through only.

    Cross-currency conversion must go through ``FxService.convert``. Calling this
    with differing currencies raises ``FxUnavailableError`` so callers cannot
    accidentally invent a 1:1 rate.
    """
    if from_currency is None or to_currency is None:
        raise FxUnavailableError(
            _BLOCK_MESSAGE,
            details={"from": from_currency, "to": to_currency},
        )
    source = normalise_currency(from_currency)
    target = normalise_currency(to_currency)
    if source != target:
        raise FxUnavailableError(
            _BLOCK_MESSAGE,
            details={"from": source, "to": target, "hint": "use_fx_service"},
        )
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
    def __init__(
        self,
        session: AsyncSession,
        *,
        fx: FxService | None = None,
    ) -> None:
        super().__init__(session)
        self.rules = PricingRuleRepository(session)
        self.changes = PriceChangeRepository(session)
        self.products = ProductRepository(session)
        self.stores = StoreRepository(session)
        self.tenants = TenantRepository(session)
        self.notifications = NotificationService(session)
        self.fx = fx or get_fx_service()

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
        # Catalogue rules may only price when rule currency matches product
        # currency — cross-currency requires FxService (draft workspace path).
        try:
            amount = convert_currency(
                cost, from_currency=product.currency, to_currency=rule.currency
            )
        except FxUnavailableError:
            return None, rule
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

    async def draft_workspace(
        self,
        product_id: uuid.UUID,
        *,
        handling_cost: Decimal = Decimal("0"),
        fee_percent: Decimal = Decimal("0"),
        propose: DraftPricingApplyRequest | None = None,
    ) -> DraftPricingWorkspaceRead:
        """Decimal-safe per-variant margin rows for the draft Pricing tab."""
        if handling_cost < 0 or fee_percent < 0:
            raise ValidationError("Handling cost and fee percent must be non-negative.")
        product = await self.products.get_by_id_or_raise(product_id)
        selling_currency, selling_source, store_id = await self._resolve_selling_currency(
            product,
            destination_store_id=(propose.destination_store_id if propose is not None else None),
        )
        shipping = product.shipping_cost
        shipping_available = shipping is not None
        shipping_warning = (
            None
            if shipping_available
            else (
                "AliExpress did not return a shipping cost for this destination. "
                "Select a shipping method or enter an estimate — missing freight "
                "is never treated as zero."
            )
        )
        rule_name: str | None = None
        _, rule = await self._propose(product)
        if rule is not None:
            rule_name = rule.name

        rows: list[DraftVariantPricingRow] = []
        any_blocked = False
        fx_status: str | None = None
        for variant in product.variants:
            supplier_currency = variant.currency or product.currency
            converted_cost: Decimal | None = None
            converted_currency: str | None = None
            conversion_required = False
            conversion_type: str | None = None
            conversion_rate: Decimal | None = None
            conversion_ts: datetime | None = None
            fx_provider: str | None = None
            row_blocked = False
            row_block_message: str | None = None

            if variant.cost_price is not None and supplier_currency and selling_currency:
                conversion_required = normalise_currency(supplier_currency) != normalise_currency(
                    selling_currency
                )
                try:
                    source_money = Money.of(variant.cost_price, supplier_currency)
                    converted, evidence, _quote = await self.fx.convert(
                        source_money, to_currency=selling_currency
                    )
                    converted_cost = converted.amount
                    converted_currency = converted.currency
                    if evidence is None:
                        conversion_type = "direct"
                    else:
                        conversion_type = "fx"
                        conversion_rate = evidence.quote.rate
                        conversion_ts = (
                            evidence.quote.source_timestamp or evidence.quote.retrieved_at
                        )
                        fx_provider = evidence.quote.provider_name
                        fx_status = evidence.quote.status.value
                        if evidence.quote.status is FxRateStatus.STALE:
                            row_block_message = (
                                f"Exchange rate {supplier_currency}→{selling_currency} "
                                "is stale. Refresh the exchange rate before applying "
                                "or publishing prices."
                            )
                            # Stale: allow draft edit preview but flag block for apply.
                            row_blocked = True
                            any_blocked = True
                except (FxUnavailableError, ValidationError) as exc:
                    conversion_type = "unavailable"
                    row_blocked = True
                    any_blocked = True
                    row_block_message = (
                        str(exc) if isinstance(exc, FxUnavailableError) else _BLOCK_MESSAGE
                    )
                    fx_provider = self.fx.provider_name
                    fx_status = FxRateStatus.UNAVAILABLE.value
            elif variant.cost_price is not None:
                row_blocked = True
                any_blocked = True
                row_block_message = (
                    "Supplier cost is missing a currency code. Refresh supplier "
                    "data before calculating a selling price."
                )
                conversion_type = "unavailable"

            # Propose only on a successfully converted (or direct) cost.
            proposed: Decimal | None = None
            if (
                propose is not None
                and not row_blocked
                and converted_cost is not None
                and conversion_type in {"direct", "fx"}
            ):
                # Shipping must share selling currency — unknown shipping is
                # excluded from landed math (never invented as zero).
                shipping_for_math = (
                    shipping
                    if shipping_available
                    and supplier_currency
                    and normalise_currency(supplier_currency)
                    == normalise_currency(selling_currency)
                    else None
                )
                proposed = self._propose_variant_sell(
                    cost=converted_cost,
                    current=variant.sell_price,
                    request=propose,
                    shipping_cost=shipping_for_math,
                )

            rows.append(
                await self._variant_row(
                    variant_id=variant.id,
                    label=variant.label,
                    is_enabled=variant.is_enabled,
                    supplier_cost=variant.cost_price,
                    supplier_currency=supplier_currency,
                    converted_cost=converted_cost if not row_blocked else None,
                    converted_currency=converted_currency if not row_blocked else None,
                    conversion_required=conversion_required,
                    conversion_type=conversion_type,
                    conversion_rate=conversion_rate,
                    conversion_rate_timestamp=conversion_ts,
                    fx_provider=fx_provider,
                    fx_status=fx_status if conversion_required else None,
                    sell_price=variant.sell_price,
                    compare_at_price=variant.compare_at_price,
                    proposed_sell_price=proposed if not row_blocked else None,
                    shipping_cost=shipping,
                    handling_cost=handling_cost,
                    fee_percent=fee_percent,
                    pricing_rule_source=rule_name,
                    row_blocked=row_blocked,
                    row_block_message=row_block_message,
                    allow_profit=not row_blocked,
                )
            )

        fx_note = (
            _BLOCK_MESSAGE
            if any_blocked
            else (
                f"Selling currency {selling_currency} "
                f"(source: {selling_source}). "
                "Supplier amounts keep their source currency; calculated "
                "columns use the selling currency only after a valid "
                "conversion or a direct target-currency price."
            )
        )
        return DraftPricingWorkspaceRead(
            product_id=product.id,
            currency=selling_currency,
            selling_currency=selling_currency,
            selling_currency_source=selling_source,
            destination_store_id=store_id,
            product_sell_price=product.sell_price,
            cost_price_min=product.cost_price_min,
            cost_price_max=product.cost_price_max,
            shipping_cost=shipping,
            shipping_cost_available=shipping_available,
            shipping_warning=shipping_warning,
            fx_note=fx_note,
            pricing_blocked=any_blocked,
            pricing_block_code="fx_unavailable" if any_blocked else None,
            pricing_block_message=_BLOCK_MESSAGE if any_blocked else None,
            fx_provider=self.fx.provider_name if any_blocked else None,
            fx_status=fx_status if any_blocked else None,
            variants=rows,
        )

    async def _resolve_selling_currency(
        self,
        product: Product,
        *,
        destination_store_id: uuid.UUID | None,
    ) -> tuple[str | None, str, uuid.UUID | None]:
        """Store currency → tenant default → unanimous supplier currency."""
        store_id = destination_store_id or product.store_id
        if store_id is not None:
            store = await self.stores.get_by_id(store_id)
            if store is not None and store.currency:
                return normalise_currency(store.currency), "shopify_store", store.id

        tenant = await self.tenants.get_by_id(product.tenant_id)
        if tenant is not None and getattr(tenant, "default_currency", None):
            return (
                normalise_currency(tenant.default_currency),
                "workspace",
                store_id,
            )

        variant_codes = {normalise_currency(v.currency) for v in product.variants if v.currency}
        if len(variant_codes) == 1:
            return next(iter(variant_codes)), "supplier_unanimous", store_id
        if product.currency:
            # Last resort — may still block rows when variants disagree.
            return normalise_currency(product.currency), "product_legacy", store_id
        return None, "unresolved", store_id

    async def apply_draft_variant_pricing(
        self,
        product_id: uuid.UUID,
        request: DraftPricingApplyRequest,
    ) -> DraftPricingWorkspaceRead:
        """Write merchant sell/compare-at prices on variants (supplier cost untouched)."""
        preview = await self.draft_workspace(
            product_id,
            handling_cost=request.handling_cost,
            fee_percent=request.fee_percent,
            propose=request,
        )
        if preview.pricing_blocked:
            raise FxUnavailableError(
                preview.pricing_block_message or _BLOCK_MESSAGE,
                details={"code": preview.pricing_block_code or "fx_unavailable"},
            )

        product = await self.products.get_by_id_or_raise(product_id)
        target_ids = set(request.variant_ids) if request.variant_ids else None
        proposed_by_id = {
            row.variant_id: row.proposed_sell_price
            for row in preview.variants
            if row.proposed_sell_price is not None and not row.row_blocked
        }
        for variant in product.variants:
            if target_ids is not None and variant.id not in target_ids:
                continue
            if not variant.is_enabled and target_ids is None:
                continue
            if request.mode is DraftPricingApplyMode.SET_COMPARE_AT:
                if request.compare_at_price is None:
                    raise ValidationError("set_compare_at requires compare_at_price.")
                if request.compare_at_price < 0:
                    raise ValidationError("compare_at_price must be non-negative.")
                variant.compare_at_price = self._quantize(
                    request.compare_at_price, cents=request.round_to_cents
                )
                continue
            proposed = proposed_by_id.get(variant.id)
            if proposed is None:
                continue
            variant.sell_price = proposed
        enabled_sells = [
            v.sell_price for v in product.variants if v.is_enabled and v.sell_price is not None
        ]
        if enabled_sells:
            product.sell_price = min(enabled_sells)
        await self.session.flush()
        return await self.draft_workspace(
            product_id,
            handling_cost=request.handling_cost,
            fee_percent=request.fee_percent,
        )

    def _propose_variant_sell(
        self,
        *,
        cost: Decimal | None,
        current: Decimal | None,
        request: DraftPricingApplyRequest,
        shipping_cost: Decimal | None = None,
    ) -> Decimal | None:
        if request.mode is DraftPricingApplyMode.SET_SELL_PRICE:
            if request.sell_price is None:
                raise ValidationError("set_sell_price requires sell_price.")
            if request.sell_price < 0:
                raise ValidationError("sell_price must be non-negative.")
            return self._quantize(request.sell_price, cents=request.round_to_cents)
        if cost is None:
            return None
        landed = cost + request.handling_cost
        if request.include_shipping_in_cost and shipping_cost is not None:
            landed += shipping_cost
        if request.mode is DraftPricingApplyMode.PERCENTAGE_MARKUP:
            if request.markup_percent is None:
                raise ValidationError("percentage_markup requires markup_percent.")
            price = landed * (Decimal("1") + request.markup_percent / Decimal("100"))
        elif request.mode is DraftPricingApplyMode.FIXED_MARKUP:
            if request.markup_fixed is None:
                raise ValidationError("fixed_markup requires markup_fixed.")
            price = landed + request.markup_fixed
        elif request.mode is DraftPricingApplyMode.TARGET_MARGIN:
            if request.target_margin_percent is None:
                raise ValidationError("target_margin requires target_margin_percent.")
            if request.target_margin_percent >= Decimal("100"):
                raise ValidationError("target_margin_percent must be below 100.")
            # sell = landed / (1 - margin%)
            denominator = Decimal("1") - (request.target_margin_percent / Decimal("100"))
            price = landed / denominator
        elif request.mode is DraftPricingApplyMode.SET_COMPARE_AT:
            return current
        else:
            raise ValidationError(f"Unsupported pricing mode: {request.mode}")
        if request.min_profit is not None:
            price = max(price, landed + request.min_profit)
        if request.min_sell_price is not None:
            price = max(price, request.min_sell_price)
        if request.max_sell_price is not None:
            price = min(price, request.max_sell_price)
        if request.psychological_rounding:
            price = self._psychological_round(price)
        if price < 0:
            raise ValidationError("Proposed sell price must be non-negative.")
        return self._quantize(price, cents=request.round_to_cents)

    @staticmethod
    def _psychological_round(amount: Decimal) -> Decimal:
        """Round to .99 endings when amount >= 1."""
        if amount < 1:
            return amount
        whole = amount.to_integral_value(rounding=ROUND_HALF_UP)
        if whole < 1:
            return amount
        return whole - Decimal("0.01")

    async def _variant_row(
        self,
        *,
        variant_id: uuid.UUID,
        label: str | None,
        is_enabled: bool,
        supplier_cost: Decimal | None,
        supplier_currency: str | None,
        converted_cost: Decimal | None,
        converted_currency: str | None,
        conversion_required: bool,
        conversion_type: str | None,
        conversion_rate: Decimal | None,
        conversion_rate_timestamp: datetime | None,
        fx_provider: str | None,
        fx_status: str | None,
        sell_price: Decimal | None,
        compare_at_price: Decimal | None,
        proposed_sell_price: Decimal | None,
        shipping_cost: Decimal | None,
        handling_cost: Decimal,
        fee_percent: Decimal,
        pricing_rule_source: str | None,
        row_blocked: bool,
        row_block_message: str | None,
        allow_profit: bool,
    ) -> DraftVariantPricingRow:
        shipping_available = shipping_cost is not None
        # Missing freight is excluded from landed-cost math (not invented as zero).
        freight_component = shipping_cost if shipping_cost is not None else Decimal("0")
        landed_extra = handling_cost + freight_component
        fee_base = proposed_sell_price if proposed_sell_price is not None else sell_price
        fee_estimate: Decimal | None = None
        break_even: Decimal | None = None
        profit: Decimal | None = None
        margin: Decimal | None = None
        if allow_profit and converted_cost is not None:
            fee_estimate = (
                (fee_base * fee_percent / Decimal("100")).quantize(
                    _MONEY_QUANT, rounding=ROUND_HALF_UP
                )
                if fee_base is not None
                else Decimal("0")
            )
            break_even = (converted_cost + landed_extra + (fee_estimate or Decimal("0"))).quantize(
                _MONEY_QUANT, rounding=ROUND_HALF_UP
            )
            effective = proposed_sell_price if proposed_sell_price is not None else sell_price
            if effective is not None:
                profit = (
                    effective - converted_cost - landed_extra - (fee_estimate or Decimal("0"))
                ).quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP)
                if effective > 0:
                    margin = (profit / effective * Decimal("100")).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP
                    )
        return DraftVariantPricingRow(
            variant_id=variant_id,
            label=label,
            is_enabled=is_enabled,
            supplier_cost=supplier_cost,
            supplier_currency=supplier_currency,
            converted_cost=converted_cost,
            converted_currency=converted_currency,
            conversion_required=conversion_required,
            conversion_type=conversion_type,
            conversion_rate=conversion_rate,
            conversion_rate_timestamp=conversion_rate_timestamp,
            fx_provider=fx_provider,
            fx_status=fx_status,
            supplier_shipping_cost=shipping_cost,
            shipping_cost_available=shipping_available,
            handling_cost=handling_cost.quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP),
            fee_estimate=fee_estimate,
            sell_price=sell_price,
            compare_at_price=compare_at_price,
            proposed_sell_price=proposed_sell_price,
            profit=profit,
            margin_percent=margin,
            break_even_price=break_even,
            pricing_rule_source=pricing_rule_source,
            manual_override=sell_price is not None,
            row_blocked=row_blocked,
            row_block_message=row_block_message,
        )

    @staticmethod
    def _quantize(amount: Decimal, *, cents: bool) -> Decimal:
        quant = _CENT_QUANT if cents else _MONEY_QUANT
        return amount.quantize(quant, rounding=ROUND_HALF_UP)
