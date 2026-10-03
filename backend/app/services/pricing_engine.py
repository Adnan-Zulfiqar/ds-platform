"""Dynamic pricing engine.

Rules are resolved product > category > store > global. Within a scope level,
higher ``priority`` wins.

Currency integrity: same-currency amounts may be used directly. Differing
currencies require a real FX quote from ``FxService``. A missing quote blocks
pricing calculations — never invent a 1:1 cross-currency rate.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import FxUnavailableError, SellingCurrencyMissingError, ValidationError
from app.core.logging import get_logger
from app.domain.fx import FxRateStatus
from app.domain.money import Money, normalise_currency
from app.models.notification import NotificationKind
from app.models.pricing import (
    PriceChange,
    PriceRounding,
    PricingRule,
    PricingScope,
    PricingStrategy,
    ShippingCostHandling,
)
from app.models.product import Product
from app.models.store import StorePlatform
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
from app.services.rule_resolution import RuleResolution, resolve_pricing_rule

logger = get_logger(__name__)

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
_SELLING_CURRENCY_MISSING_MESSAGE = (
    "Shopify selling currency is not available. Refresh the store currency "
    "before calculating prices."
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


#: Reasons a product cannot be priced with confidence. Strings rather than an
#: enum because they cross the API boundary into merchant-facing copy, and the
#: frontend needs to map each to a specific "here is what to do" instruction.
REVIEW_SUPPLIER_COST_UNKNOWN = "supplier_cost_unknown"
REVIEW_SHIPPING_COST_UNKNOWN = "shipping_cost_unknown"
REVIEW_SUPPLIER_CURRENCY_UNKNOWN = "supplier_currency_unknown"
REVIEW_FX_UNAVAILABLE = "fx_rate_unavailable"
REVIEW_NO_SHIPPING_MATCH = "no_shipping_method_matches"
#: The rule itself could not be evaluated -- a strategy missing the field it
#: needs, most often from a row written before that field was required. The
#: draft is kept and flagged rather than the import being failed: a product
#: that exists and is visibly unpriced is worth more to a merchant than an
#: import that refuses to complete over a misconfigured rule.
REVIEW_CALCULATION_FAILED = "pricing_calculation_failed"


class CostBearing(Protocol):
    """The three fields :func:`calculate_price` actually reads.

    Structural rather than `Product` on purpose: the currency-converted path
    must be able to price a *view* of a product's costs without mutating the
    row, because the supplier's own figures are a snapshot and have to stay
    in the supplier's currency.
    """

    cost_price_min: Decimal | None
    shipping_cost: Decimal | None
    currency: str | None


# Not frozen: `CostBearing` describes settable attributes (a real `Product`
# has them), and a frozen dataclass exposes read-only ones, which does not
# satisfy the protocol. Nothing mutates this object -- it is constructed once
# and discarded.
@dataclass(slots=True)
class _CostView:
    """A product's cost fields after currency conversion.

    Deliberately not a mutated ``Product``. The supplier's own cost figures
    are a snapshot and must stay in the supplier's currency; writing
    converted values back onto the row -- even transiently -- is how a
    snapshot silently becomes a derived value.
    """

    cost_price_min: Decimal | None
    shipping_cost: Decimal | None
    currency: str | None


@dataclass(frozen=True, slots=True)
class LandedCost:
    """What a product actually costs to put in a customer's hands.

    ``review_reasons`` is the load-bearing part. A product whose supplier
    shipping quote is unknown does **not** get a zero and a confident price:
    zero is a number, and a wrong one, on exactly the cheap heavy items where
    shipping dominates. It gets priced on what is known, flagged, and held
    out of automatic publishing until a human has looked.

    ``amount`` is what the pricing strategy is applied to;
    ``profit_basis`` is what profit is measured against. They differ under
    ``ABSORB_FROM_PROFIT``, where the merchant chooses to price as if
    shipping were free but still needs the profit figure to be honest.
    """

    amount: Decimal
    profit_basis: Decimal
    currency: str | None
    item_cost: Decimal
    shipping_cost: Decimal
    fees: Decimal
    handling: ShippingCostHandling
    review_reasons: tuple[str, ...] = ()
    #: Set when shipping is billed to the customer separately rather than
    #: folded into the item price.
    separate_shipping_charge: Decimal | None = None

    @property
    def needs_review(self) -> bool:
        return bool(self.review_reasons)


def landed_cost(
    product: CostBearing,
    *,
    rule: PricingRule | None = None,
    shipping_cost: Decimal | None = None,
) -> LandedCost:
    """``item + supplier shipping + known duty/fees``, in the supplier currency.

    Deliberately does no FX. Both cost components are already denominated in
    the supplier's currency, and converting here would bury a missing rate
    inside a cost figure; callers needing another currency go through
    ``FxService`` explicitly, where a missing quote fails closed.

    Missing inputs are recorded, never defaulted. The zero substituted below
    exists only so the arithmetic can proceed far enough to show the merchant
    a partial breakdown -- the accompanying review reason is what stops that
    figure being treated as a price.
    """
    reasons: list[str] = []

    item = product.cost_price_min
    if item is None:
        reasons.append(REVIEW_SUPPLIER_COST_UNKNOWN)
        item = Decimal("0")

    shipping = shipping_cost if shipping_cost is not None else product.shipping_cost
    if shipping is None:
        reasons.append(REVIEW_SHIPPING_COST_UNKNOWN)
        shipping = Decimal("0")

    currency = product.currency
    if not currency:
        reasons.append(REVIEW_SUPPLIER_CURRENCY_UNKNOWN)

    handling = rule.shipping_cost_handling if rule else ShippingCostHandling.INCLUDE_IN_PRICE
    duty_percent = (rule.duty_percent if rule else None) or Decimal("0")
    fees_fixed = (rule.fees_fixed if rule else None) or Decimal("0")

    dutiable = item + shipping
    fees = (dutiable * duty_percent / Decimal("100")) + fees_fixed

    # Profit is always measured against everything we actually pay. Only the
    # *pricing basis* changes with handling.
    profit_basis = dutiable + fees
    if handling is ShippingCostHandling.INCLUDE_IN_PRICE:
        priced_on = profit_basis
        separate = None
    elif handling is ShippingCostHandling.CHARGE_SEPARATELY:
        priced_on = item + fees
        separate = shipping
    else:  # ABSORB_FROM_PROFIT
        priced_on = item + fees
        separate = None

    return LandedCost(
        amount=priced_on.quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP),
        profit_basis=profit_basis.quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP),
        currency=currency or None,
        item_cost=item,
        shipping_cost=shipping,
        fees=fees.quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP),
        handling=handling,
        review_reasons=tuple(reasons),
        separate_shipping_charge=separate,
    )


def apply_rounding(price: Decimal, rounding: PriceRounding) -> Decimal:
    """Charm-round a price to the **nearest** matching ending.

    Nearest, not upward. Charm pricing exists to sit a shade *under* a round
    number -- 20.00 is meant to become 19.99, and a rule that pushed it to
    20.99 would be a 5% price rise wearing a charm-pricing label. Ties round
    down, for the same reason.

    Below the first charm ending the price is returned untouched. An earlier
    draft of this returned ``whole - 1 + 0.99`` unconditionally, which for a
    0.45 price produced **-0.01** -- a negative selling price reaching
    storage. There is no charm ending at or below such a price, and inventing
    one either way (negative, or rounding up to 0.99) would be worse than
    leaving a sub-unit price alone.

    Rounding down can cost a penny of margin, so :func:`compute_sell_price`
    re-applies the floors afterwards and steps up an increment if one is
    breached.
    """
    if rounding is PriceRounding.WHOLE:
        return price.to_integral_value(rounding=ROUND_HALF_UP)

    # Exhaustive by construction. An earlier version selected the ending with
    # `.99 if rounding is NINETY_NINE else .95`, which silently charm-rounded
    # anything that was neither -- including a transient rule whose `rounding`
    # is still `None` because the column default only applies on INSERT. That
    # turned an unrounded 15.00 into 14.95. Anything not explicitly a charm
    # ending now means "do not round".
    if rounding is PriceRounding.NINETY_NINE:
        ending = Decimal("0.99")
    elif rounding is PriceRounding.NINETY_FIVE:
        ending = Decimal("0.95")
    else:
        return price

    whole = price.to_integral_value(rounding=ROUND_FLOOR)
    below = whole - Decimal("1") + ending
    above = whole + ending
    if above <= price:
        below, above = above, whole + Decimal("1") + ending
    if below <= Decimal("0"):
        # No charm ending exists at or below this price. Leave it alone
        # rather than emit a non-positive price or silently raise it.
        return price
    # Ties (exactly between two endings) resolve downward.
    return below if (price - below) <= (above - price) else above


def _next_ending_above(price: Decimal, rounding: PriceRounding) -> Decimal:
    """The first charm ending at or above ``price``. Used to repair a floor."""
    if rounding is PriceRounding.WHOLE:
        return price.to_integral_value(rounding=ROUND_CEILING)
    if rounding is PriceRounding.NINETY_NINE:
        ending = Decimal("0.99")
    elif rounding is PriceRounding.NINETY_FIVE:
        ending = Decimal("0.95")
    else:
        return price
    whole = price.to_integral_value(rounding=ROUND_FLOOR)
    candidate = whole + ending
    return candidate if candidate >= price else whole + Decimal("1") + ending


def compute_sell_price_before_rounding(*, cost: Decimal, rule: PricingRule) -> Decimal:
    """The strategy price with floors and ceiling applied, but not rounded.

    Split out so a merchant can be shown what the rule produced *before* charm
    rounding moved it. Without that, a rule that computes 20.00 and displays
    19.99 looks like an arithmetic error rather than the rounding mode they
    chose. It is exported rather than recomputed in the client because the
    frontend must never carry a second copy of this formula.
    """
    return _strategy_price(cost=cost, rule=rule)[0].quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP)


def sale_fee_percent(rule: PricingRule | None) -> Decimal:
    """The rule's sale fee as a percentage, validated (0 <= fee < 100)."""
    fee = getattr(rule, "sale_fee_percent", None) if rule is not None else None
    if fee is None:
        return Decimal("0")
    if fee < 0 or fee >= Decimal("100"):
        raise ValidationError("The sale fee must be at least 0% and below 100%.")
    return Decimal(fee)


def compute_sell_price(*, cost: Decimal, rule: PricingRule) -> Decimal:
    """Apply one rule's strategy to a **landed** cost.

    ``cost`` is the landed cost (item + supplier shipping + known fees), not
    the bare item price -- see :func:`landed_cost`. Every guardrail below is
    therefore measured against what the product actually costs to deliver,
    which is the only basis on which "minimum profit" means real profit.

    Order is deliberate and load-bearing: strategy, then floors, then the
    ceiling, then rounding. Rounding last because rounding *before* the floor
    check could leave a price under ``min_profit``; the ceiling after the
    floors because a ``max_price`` the merchant set explicitly should win
    over a computed minimum.
    """
    price, floor = _strategy_price(cost=cost, rule=rule)

    price = apply_rounding(price, rule.rounding)

    # Rounding to the nearest charm ending can land a penny below a floor the
    # merchant set. Step up to the next ending only when it actually does, so
    # an unconstrained price still reads as a charm price.
    if floor is not None and price < floor:
        price = _next_ending_above(floor, rule.rounding)
    # A max_price is a hard ceiling: never let a rounding repair cross it.
    if rule.max_price is not None:
        price = min(price, rule.max_price)

    return price.quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP)


def _strategy_price(*, cost: Decimal, rule: PricingRule) -> tuple[Decimal, Decimal | None]:
    """Strategy, then floors, then ceiling. Returns the price and the floor.

    The floor comes back with the price because the caller needs it again
    after rounding -- charm rounding is downward, so it can cross a floor the
    merchant set and has to be repaired against the same number.
    """
    fee_percent = sale_fee_percent(rule)
    #: Share of the price the merchant keeps after the sale fee.
    keep = Decimal("1") - fee_percent / Decimal("100")
    if rule.strategy is PricingStrategy.PERCENTAGE_MARKUP:
        if rule.markup_percent is None:
            raise ValidationError("Percentage markup rules require markup_percent.")
        price = cost * (Decimal("1") + rule.markup_percent / Decimal("100"))
    elif rule.strategy is PricingStrategy.FIXED_MARKUP:
        if rule.markup_fixed is None:
            raise ValidationError("Fixed markup rules require markup_fixed.")
        price = cost + rule.markup_fixed
    elif rule.strategy is PricingStrategy.TARGET_MARGIN:
        if rule.margin_percent is None:
            raise ValidationError("Target margin rules require margin_percent.")
        # Guarded at the schema layer too, but division by zero here would be
        # a 500 rather than a validation error, so it is checked twice.
        if rule.margin_percent + fee_percent >= Decimal("100"):
            raise ValidationError("Target margin plus the sale fee must be below 100%.")
        # Margin *after* the fee: price - fee * price - cost = margin * price.
        price = cost / (Decimal("1") - (rule.margin_percent + fee_percent) / Decimal("100"))
    elif rule.strategy is PricingStrategy.HYBRID:
        if rule.markup_percent is None and rule.markup_fixed is None:
            raise ValidationError("Hybrid rules require markup_percent or markup_fixed.")
        percent = rule.markup_percent or Decimal("0")
        fixed = rule.markup_fixed or Decimal("0")
        price = cost * (Decimal("1") + percent / Decimal("100")) + fixed
    else:
        tiered = _tiered_price(cost, rule.tiers)
        # No matching tier — fall back to cost so apply never invents a gain.
        price = cost if tiered is None else tiered
    if rule.strategy is not PricingStrategy.TARGET_MARGIN and fee_percent > 0:
        # Markups are on cost; the fee is then taken from the selling price,
        # so the price is grossed up for the markup to survive it.
        price = price / keep

    floor: Decimal | None = None
    if rule.min_profit is not None:
        # Net of the sale fee: price * (1 - fee) - cost >= min_profit.
        floor = (cost + rule.min_profit) / keep
    if rule.min_price is not None:
        floor = rule.min_price if floor is None else max(floor, rule.min_price)
    if floor is not None:
        price = max(price, floor)
    if rule.max_price is not None:
        price = min(price, rule.max_price)

    # Deliberately unquantized: `apply_rounding` compares against whole units
    # and charm endings, and quantizing first would change what it sees.
    # Callers quantize once, after their own last adjustment.
    return price, floor


def compute_compare_at_price(price: Decimal, rule: PricingRule) -> Decimal | None:
    """The struck-through "was" price, or ``None`` when unconfigured.

    Derived from the selling price, never from cost, and never fed back into
    profit: it is a display value only. Kept out of :func:`compute_sell_price`
    so no caller can mistake it for something the guardrails apply to.
    """
    if rule.compare_at_percent is None or rule.compare_at_percent <= Decimal("0"):
        return None
    inflated = price * (Decimal("1") + rule.compare_at_percent / Decimal("100"))
    return inflated.quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class PriceCalculation:
    """One product's calculated price plus everything needed to explain it.

    Every figure the merchant is shown comes from this one object, so the
    preview, the audit row and the applied price cannot disagree about how a
    number was reached.
    """

    landed: LandedCost
    rule: PricingRule | None
    price: Decimal | None
    compare_at: Decimal | None
    review_reasons: tuple[str, ...] = ()

    @property
    def needs_review(self) -> bool:
        return bool(self.review_reasons)

    @property
    def sale_fee(self) -> Decimal | None:
        """What the sale fee takes from this price (Track E2)."""
        if self.price is None:
            return None
        fee = self.price * sale_fee_percent(self.rule) / Decimal("100")
        return fee.quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP)

    @property
    def profit(self) -> Decimal | None:
        """Net of the sale fee: what the merchant actually keeps."""
        if self.price is None:
            return None
        fee = self.sale_fee or Decimal("0")
        return (self.price - fee - self.landed.profit_basis).quantize(
            _MONEY_QUANT, rounding=ROUND_HALF_UP
        )

    @property
    def markup_percent(self) -> Decimal | None:
        """Profit as a share of **cost**. Distinct from margin -- see below."""
        profit = self.profit
        if profit is None or self.landed.profit_basis <= 0:
            return None
        return (profit / self.landed.profit_basis * Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    @property
    def margin_percent(self) -> Decimal | None:
        """Profit as a share of the **selling price**. Always < markup."""
        profit = self.profit
        if profit is None or self.price is None or self.price <= 0:
            return None
        return (profit / self.price * Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )


def calculate_price(
    product: CostBearing,
    *,
    rule: PricingRule | None,
    shipping_cost: Decimal | None = None,
    extra_review_reasons: tuple[str, ...] = (),
) -> PriceCalculation:
    """Price one product under one rule, or explain why it cannot be priced.

    The single entry point every M3A surface uses -- import, preview and
    bulk apply all call this, so a merchant can never see one number in the
    preview and a different one after confirming.

    Returns a calculation with ``price=None`` whenever the inputs are not
    trustworthy. That is the whole point: a missing supplier cost produces a
    flagged, un-priced result rather than a confident guess.
    """
    landed = landed_cost(product, rule=rule, shipping_cost=shipping_cost)
    reasons = tuple(landed.review_reasons) + tuple(extra_review_reasons)

    if rule is None:
        return PriceCalculation(
            landed=landed, rule=None, price=None, compare_at=None, review_reasons=reasons
        )
    if REVIEW_SUPPLIER_COST_UNKNOWN in reasons or REVIEW_SHIPPING_COST_UNKNOWN in reasons:
        # Do not invent a selling price from a cost we do not have.
        return PriceCalculation(
            landed=landed, rule=rule, price=None, compare_at=None, review_reasons=reasons
        )

    price = compute_sell_price(cost=landed.amount, rule=rule)

    # A per-variant floor is applied here rather than inside
    # `compute_sell_price` because it is measured against the *profit basis*,
    # which differs from the pricing basis when shipping is absorbed.
    if rule.min_profit_per_variant is not None:
        # Net of the sale fee, like the product-level floor (Track E2).
        keep = Decimal("1") - sale_fee_percent(rule) / Decimal("100")
        required = (landed.profit_basis + rule.min_profit_per_variant) / keep
        if price < required:
            price = _next_ending_above(required, rule.rounding)
            if rule.max_price is not None:
                price = min(price, rule.max_price)
            price = price.quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP)

    return PriceCalculation(
        landed=landed,
        rule=rule,
        price=price,
        compare_at=compute_compare_at_price(price, rule),
        review_reasons=reasons,
    )


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
    variant_id: uuid.UUID | None = None,
) -> PricingRule | None:
    """Pick the narrowest matching rule; priority breaks ties.

    Kept as a thin forwarder rather than deleted: it is the shape existing
    callers and tests use. The precedence itself lives in
    ``app.services.rule_resolution`` so pricing and shipping cannot drift
    apart, and so the M3A variant scope is honoured everywhere at once.
    """
    return resolve_pricing_rule(
        candidates,
        product_id=product_id,
        variant_id=variant_id,
        store_id=store_id,
        category_id=category_id,
    ).rule


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
        """Backwards-compatible wrapper over :meth:`propose_calculation`."""
        calculation = await self.propose_calculation(product)
        return calculation.price, calculation.rule

    async def propose_calculation(self, product: Product) -> PriceCalculation:
        """The single catalogue-side entry point for "what should this cost?".

        Everything the preview, the apply and the audit row need comes from
        one object, so they cannot disagree about how a figure was reached.

        The pre-M3A version of this passed ``product.cost_price_min`` -- the
        bare item price -- into a function whose contract is a *landed* cost.
        ``min_profit`` therefore promised profit that supplier shipping could
        erase entirely. It now goes through :func:`calculate_price`, which
        adds supplier shipping and known duty/fees, and which refuses to
        invent a price when either is unknown.
        """
        resolution = await self.resolve_for(product)
        rule = resolution.rule
        if rule is None:
            # Not an error: the product simply has no governing rule and
            # keeps whatever price it already has.
            return calculate_price(product, rule=None)

        # Catalogue rules may only price when the rule currency matches the
        # product currency -- cross-currency goes through FxService on the
        # draft workspace path, where a missing rate fails closed.
        try:
            converted_item = (
                None
                if product.cost_price_min is None
                else convert_currency(
                    product.cost_price_min,
                    from_currency=product.currency,
                    to_currency=rule.currency,
                )
            )
            converted_shipping = (
                None
                if product.shipping_cost is None
                else convert_currency(
                    product.shipping_cost,
                    from_currency=product.currency,
                    to_currency=rule.currency,
                )
            )
        except FxUnavailableError:
            return calculate_price(
                product, rule=rule, extra_review_reasons=(REVIEW_FX_UNAVAILABLE,)
            )

        if converted_item is not None and converted_item != product.cost_price_min:
            # A conversion happened; price on the converted figures without
            # mutating the product, whose cost fields stay supplier-native.
            shadow = _CostView(
                cost_price_min=converted_item,
                shipping_cost=converted_shipping,
                currency=rule.currency or product.currency,
            )
            return calculate_price(shadow, rule=rule)
        return calculate_price(product, rule=rule)

    async def resolve_for(self, product: Product) -> RuleResolution[PricingRule]:
        """Which pricing rule governs ``product``, and why."""
        candidates = await self.rules.find_candidates(
            product_id=product.id,
            store_id=product.store_id,
            category_id=product.category_id,
        )
        return resolve_pricing_rule(
            candidates,
            product_id=product.id,
            store_id=product.store_id,
            category_id=product.category_id,
        )

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
        if selling_currency is None:
            logger.info(
                "pricing_blocked_currency_missing",
                product_id=str(product_id),
                store_id=str(store_id) if store_id else None,
                source=selling_source,
            )
            return DraftPricingWorkspaceRead(
                product_id=product.id,
                currency=None,
                selling_currency=None,
                selling_currency_source=selling_source,
                destination_store_id=store_id,
                product_sell_price=product.sell_price,
                cost_price_min=product.cost_price_min,
                cost_price_max=product.cost_price_max,
                shipping_cost=product.shipping_cost,
                shipping_cost_available=product.shipping_cost is not None,
                shipping_warning=None,
                fx_note=_SELLING_CURRENCY_MISSING_MESSAGE,
                pricing_blocked=True,
                pricing_block_code="selling_currency_missing",
                pricing_block_message=_SELLING_CURRENCY_MISSING_MESSAGE,
                fx_provider=None,
                fx_status=None,
                variants=[],
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
        ws_fx_rate: Decimal | None = None
        ws_fx_base: str | None = None
        ws_fx_quote: str | None = None
        ws_fx_provider: str | None = None
        ws_fx_provider_ts: datetime | None = None
        ws_fx_fetched_at: datetime | None = None
        ws_fx_is_stale: bool | None = None
        for variant in product.variants:
            supplier_currency = variant.currency or product.currency
            converted_cost: Decimal | None = None
            converted_currency: str | None = None
            conversion_required = False
            conversion_type: str | None = None
            conversion_rate: Decimal | None = None
            conversion_ts: datetime | None = None
            fx_fetched_at: datetime | None = None
            fx_provider: str | None = None
            fx_base: str | None = None
            fx_quote_ccy: str | None = None
            fx_is_stale = False
            row_blocked = False
            row_block_message: str | None = None

            if variant.cost_price is not None and supplier_currency and selling_currency:
                conversion_required = normalise_currency(supplier_currency) != normalise_currency(
                    selling_currency
                )
                try:
                    source_money = Money.of(variant.cost_price, supplier_currency)
                    converted, evidence, _quote = await self.fx.convert(
                        source_money, to_currency=selling_currency, allow_stale=True
                    )
                    converted_cost = converted.amount
                    converted_currency = converted.currency
                    if evidence is None:
                        conversion_type = "direct"
                    else:
                        conversion_type = "fx"
                        conversion_rate = evidence.quote.rate
                        conversion_ts = evidence.quote.provider_timestamp
                        fx_fetched_at = evidence.quote.fetched_at
                        fx_provider = evidence.quote.provider_name
                        fx_status = evidence.quote.status.value
                        fx_base = evidence.quote.base_currency
                        fx_quote_ccy = evidence.quote.quote_currency
                        fx_is_stale = evidence.quote.is_stale
                        ws_fx_rate = conversion_rate
                        ws_fx_base = fx_base
                        ws_fx_quote = fx_quote_ccy
                        ws_fx_provider = fx_provider
                        ws_fx_provider_ts = conversion_ts
                        ws_fx_fetched_at = fx_fetched_at
                        ws_fx_is_stale = fx_is_stale
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

            # A price exists but was never stamped with the currency it was
            # computed in (pre-migration-0021 data), or was stamped under a
            # selling currency that has since changed (a store switch, a
            # newly-verified Shopify sync) — either way, the stored number is
            # not trustworthy as "the price in today's selling currency"
            # without a fresh run through this workspace.
            needs_recalculation = variant.sell_price is not None and (
                variant.sell_price_currency is None
                or normalise_currency(variant.sell_price_currency)
                != normalise_currency(selling_currency)
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
                    fx_fetched_at=fx_fetched_at,
                    fx_provider=fx_provider,
                    fx_status=fx_status if conversion_required else None,
                    fx_base_currency=fx_base,
                    fx_quote_currency=fx_quote_ccy,
                    fx_is_stale=fx_is_stale if conversion_type == "fx" else None,
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
                    needs_recalculation=needs_recalculation,
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
            fx_provider=ws_fx_provider or (self.fx.provider_name if any_blocked else None),
            fx_status=fx_status if any_blocked or ws_fx_rate is not None else None,
            fx_rate=ws_fx_rate,
            fx_base_currency=ws_fx_base,
            fx_quote_currency=ws_fx_quote,
            fx_provider_timestamp=ws_fx_provider_ts,
            fx_fetched_at=ws_fx_fetched_at,
            fx_is_stale=ws_fx_is_stale,
            needs_recalculation=any(row.needs_recalculation for row in rows),
            variants=rows,
        )

    async def _resolve_selling_currency(
        self,
        product: Product,
        *,
        destination_store_id: uuid.UUID | None,
    ) -> tuple[str | None, str, uuid.UUID | None]:
        """Resolve destination selling currency.

        Shopify: only a verified ``currency`` with ``currency_last_synced_at``
        is authoritative. Unsynced Shopify currency must **not** fall back to
        tenant, supplier, USD, or any default.

        Non-Shopify / channel-independent: store currency or tenant default may
        be used when explicitly configured.

        **``product.import_currency`` (M24B) outranks the tenant default.**
        A store-less draft imported for GB with a real, localized GBP supplier
        price previously fell straight through to ``tenant.default_currency``
        (``server_default="USD"`` on every tenant, so every store-less draft
        silently priced in USD regardless of what was actually imported) —
        this is that exact live-reproduced bug, not a hypothetical one. The
        currency actually requested from and confirmed by AliExpress for this
        specific draft is a stronger signal than a workspace-wide fallback
        that exists for drafts with no import context at all, so it is
        checked first. Unset (``NULL``) for every pre-M24B draft, so nothing
        changes for those — they fall through to the unchanged tenant
        default / supplier-unanimous / legacy chain below, exactly as before.
        """
        store_id = destination_store_id or product.store_id
        if store_id is not None:
            store = await self.stores.get_by_id(store_id)
            if store is not None:
                if store.platform is StorePlatform.SHOPIFY:
                    if store.currency_last_synced_at is not None and store.currency:
                        try:
                            return (
                                normalise_currency(store.currency),
                                "shopify_store",
                                store.id,
                            )
                        except ValidationError:
                            return None, "selling_currency_missing", store.id
                    return None, "selling_currency_missing", store.id
                if store.currency:
                    try:
                        return normalise_currency(store.currency), "store", store.id
                    except ValidationError:
                        pass

        if product.import_currency:
            try:
                return normalise_currency(product.import_currency), "import_market", store_id
            except ValidationError:
                pass

        tenant = await self.tenants.get_by_id(product.tenant_id)
        if tenant is not None and getattr(tenant, "default_currency", None):
            return (
                normalise_currency(tenant.default_currency),
                "workspace",
                store_id,
            )

        # Channel-independent only — never used when a Shopify store is linked.
        variant_codes = {normalise_currency(v.currency) for v in product.variants if v.currency}
        if len(variant_codes) == 1:
            return next(iter(variant_codes)), "supplier_unanimous", store_id
        if product.currency:
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
            if preview.pricing_block_code == "selling_currency_missing":
                raise SellingCurrencyMissingError(
                    preview.pricing_block_message or _SELLING_CURRENCY_MISSING_MESSAGE,
                    details={"code": "selling_currency_missing"},
                )
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
                # `compare_at_price` is meaningless without knowing which
                # currency it is in, same reasoning as `sell_price` below —
                # and setting one without the other previously set would
                # leave the pair inconsistent, so stamp it here too.
                variant.sell_price_currency = preview.selling_currency
                continue
            proposed = proposed_by_id.get(variant.id)
            if proposed is None:
                continue
            variant.sell_price = proposed
            # The currency this specific write is in — always the *resolved*
            # selling currency for this call, never the supplier's `currency`.
            # Read back later by `_variant_needs_recalculation` to detect a
            # stale price after the selling currency itself changes (a store
            # switch, a newly-verified Shopify sync) rather than trusting a
            # number that happens to still be sitting in the column.
            variant.sell_price_currency = preview.selling_currency
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
        """Round to .99 endings, delegating to the one charm-rounding rule.

        M24A had its own implementation here. M3A introduced a second one for
        global rules, and two roundings that agree today are two that can
        disagree after the next edit -- the draft workspace and the global
        rule would then quote different prices for the same product. This
        forwards instead. Verified equivalent across the range M24A covered
        (>= 1.00) before the change; below 1.00 both leave the price alone.
        """
        return apply_rounding(amount, PriceRounding.NINETY_NINE)

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
        fx_fetched_at: datetime | None = None,
        fx_provider: str | None,
        fx_status: str | None,
        fx_base_currency: str | None = None,
        fx_quote_currency: str | None = None,
        fx_is_stale: bool | None = None,
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
        needs_recalculation: bool = False,
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
            fx_fetched_at=fx_fetched_at,
            fx_provider=fx_provider,
            fx_status=fx_status,
            fx_base_currency=fx_base_currency,
            fx_quote_currency=fx_quote_currency,
            fx_is_stale=fx_is_stale,
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
            needs_recalculation=needs_recalculation,
        )

    @staticmethod
    def _quantize(amount: Decimal, *, cents: bool) -> Decimal:
        quant = _CENT_QUANT if cents else _MONEY_QUANT
        return amount.quantize(quant, rounding=ROUND_HALF_UP)
