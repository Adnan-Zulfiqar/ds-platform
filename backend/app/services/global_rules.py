"""Global rule management, versioning and preview (M3A-2).

One service owns every mutation of a pricing or shipping rule, because
versioning is only trustworthy if there is no second way to change a rule.
Anything that writes a rule writes its history in the same transaction; a
rollback therefore takes both, and there is no code path that produces a rule
change without an audit row.

**No arithmetic lives here.** Landed cost, strategies, rounding and
precedence all come from the M3A-1 core (``pricing_engine``,
``rule_resolution``, ``shipping_rules``). A second engine is exactly what
this milestone spent its first session removing.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.exc import IntegrityError

from app.core.exceptions import ConflictError, ValidationError
from app.models.pricing import (
    GlobalRuleKind,
    GlobalRuleVersion,
    PricingRule,
    ShippingRule,
)
from app.models.product import Product, ProductSource, ProductStatus
from app.repositories.global_rules import GlobalRuleVersionRepository, ShippingRuleRepository
from app.repositories.pricing import PricingRuleRepository
from app.services.base import BaseService
from app.services.pricing_engine import PriceCalculation, calculate_price
from app.services.rule_resolution import (
    RuleResolution,
    resolve_pricing_rule,
    resolve_shipping_rule,
)

#: Fields copied into a version snapshot, per rule kind. An explicit list, not
#: `__table__.columns`: an allowlist means a column added later cannot leak
#: into the audit trail unreviewed, which matters because these rows are
#: returned to clients.
_PRICING_SNAPSHOT_FIELDS = (
    "name",
    "scope",
    "strategy",
    "priority",
    "store_id",
    "category_id",
    "product_id",
    "variant_id",
    "markup_percent",
    "markup_fixed",
    "margin_percent",
    "min_profit",
    "min_profit_per_variant",
    "min_price",
    "max_price",
    "duty_percent",
    "fees_fixed",
    "sale_fee_percent",
    "rounding",
    "compare_at_percent",
    "shipping_cost_handling",
    "applies_to_new_imports",
    "tiers",
    "currency",
    "is_active",
)

_SHIPPING_SNAPSHOT_FIELDS = (
    "name",
    "scope",
    "priority",
    "store_id",
    "category_id",
    "product_id",
    "variant_id",
    "destination_country",
    "selection_strategy",
    "max_delivery_days",
    "max_shipping_cost",
    "tracking_required",
    "preferred_carriers",
    "blocked_carriers",
    "no_match_behaviour",
    "is_active",
)


def _jsonable(value: Any) -> Any:
    """Reduce a column value to something JSONB can hold, losslessly.

    Decimals become strings rather than floats. A float would round-trip
    19.99 as 19.989999999999998 and quietly corrupt the record of what a
    merchant actually configured.
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value"):  # StrEnum members
        return value.value
    return value


def _snapshot(rule: PricingRule | ShippingRule, fields: tuple[str, ...]) -> dict[str, Any]:
    return {name: _jsonable(getattr(rule, name, None)) for name in fields}


@dataclass(frozen=True, slots=True)
class PreviewInputs:
    """Sample figures for a live preview. Never persisted."""

    item_cost: Decimal | None
    shipping_cost: Decimal | None
    currency: str | None


@dataclass(frozen=True, slots=True)
class RulePreview:
    """A calculation plus the resolution that produced it."""

    resolution: RuleResolution[PricingRule]
    calculation: PriceCalculation


class GlobalRuleService(BaseService):
    """Create, read, version, activate and preview global rules."""

    def __init__(self, session: Any) -> None:
        super().__init__(session)
        self.pricing = PricingRuleRepository(session)
        self.shipping = ShippingRuleRepository(session)
        self.versions = GlobalRuleVersionRepository(session)

    # ------------------------------------------------------------------ read
    async def get_pricing_rule(self, rule_id: uuid.UUID) -> PricingRule:
        """404 for both "missing" and "another tenant's" -- a 403 would
        confirm the id exists and let an attacker enumerate other tenants."""
        return await self.pricing.get_by_id_or_raise(rule_id)

    async def get_shipping_rule(self, rule_id: uuid.UUID) -> ShippingRule:
        return await self.shipping.get_by_id_or_raise(rule_id)

    async def history(
        self,
        *,
        rule_kind: GlobalRuleKind,
        rule_id: uuid.UUID,
        page: int = 1,
        size: int = 25,
    ) -> tuple[list[GlobalRuleVersion], int]:
        """One page of history for one rule, newest first, plus the total.

        Reads history directly rather than loading the rule first, so a
        deactivated -- or soft-deleted -- rule still answers. That is the
        whole point of keeping the trail: "what happened to the rule that is
        no longer here" is exactly when someone looks.
        """
        return await self.versions.history_for(
            rule_kind=rule_kind, rule_id=rule_id, page=page, size=size
        )

    # ----------------------------------------------------------------- write
    async def create_pricing_rule(
        self, values: dict[str, Any], *, actor_id: uuid.UUID | None, note: str | None = None
    ) -> PricingRule:
        rule: PricingRule = await self._create(self.pricing, values, kind=GlobalRuleKind.PRICING)
        await self._record(
            rule,
            kind=GlobalRuleKind.PRICING,
            previous={},
            actor_id=actor_id,
            note=note,
        )
        return rule

    async def create_shipping_rule(
        self, values: dict[str, Any], *, actor_id: uuid.UUID | None, note: str | None = None
    ) -> ShippingRule:
        rule: ShippingRule = await self._create(self.shipping, values, kind=GlobalRuleKind.SHIPPING)
        await self._record(
            rule,
            kind=GlobalRuleKind.SHIPPING,
            previous={},
            actor_id=actor_id,
            note=note,
        )
        return rule

    async def update_pricing_rule(
        self,
        rule_id: uuid.UUID,
        changes: dict[str, Any],
        *,
        expected_updated_at: datetime,
        actor_id: uuid.UUID | None,
        note: str | None = None,
    ) -> PricingRule:
        updated: PricingRule = await self._update(
            self.pricing,
            rule_id,
            changes,
            kind=GlobalRuleKind.PRICING,
            expected_updated_at=expected_updated_at,
            actor_id=actor_id,
            note=note,
        )
        return updated

    async def update_shipping_rule(
        self,
        rule_id: uuid.UUID,
        changes: dict[str, Any],
        *,
        expected_updated_at: datetime,
        actor_id: uuid.UUID | None,
        note: str | None = None,
    ) -> ShippingRule:
        updated: ShippingRule = await self._update(
            self.shipping,
            rule_id,
            changes,
            kind=GlobalRuleKind.SHIPPING,
            expected_updated_at=expected_updated_at,
            actor_id=actor_id,
            note=note,
        )
        return updated

    async def set_pricing_active(
        self,
        rule_id: uuid.UUID,
        *,
        active: bool,
        expected_updated_at: datetime,
        actor_id: uuid.UUID | None,
        note: str | None = None,
    ) -> PricingRule:
        return await self.update_pricing_rule(
            rule_id,
            {"is_active": active},
            expected_updated_at=expected_updated_at,
            actor_id=actor_id,
            note=note,
        )

    async def set_shipping_active(
        self,
        rule_id: uuid.UUID,
        *,
        active: bool,
        expected_updated_at: datetime,
        actor_id: uuid.UUID | None,
        note: str | None = None,
    ) -> ShippingRule:
        return await self.update_shipping_rule(
            rule_id,
            {"is_active": active},
            expected_updated_at=expected_updated_at,
            actor_id=actor_id,
            note=note,
        )

    # ------------------------------------------------------------- internals
    async def _create(self, repo: Any, values: dict[str, Any], *, kind: GlobalRuleKind) -> Any:
        payload = dict(values)
        payload.setdefault("version", 1)
        try:
            rule = await repo.create(**payload)
            await self.flush()
        except IntegrityError as exc:  # pragma: no cover - exercised via API tests
            raise self._translate_integrity(exc, kind=kind) from exc
        return rule

    async def _update(
        self,
        repo: Any,
        rule_id: uuid.UUID,
        changes: dict[str, Any],
        *,
        kind: GlobalRuleKind,
        expected_updated_at: datetime,
        actor_id: uuid.UUID | None,
        note: str | None,
    ) -> Any:
        rule = await repo.get_by_id_or_raise(rule_id)
        fields = (
            _PRICING_SNAPSHOT_FIELDS
            if kind is GlobalRuleKind.PRICING
            else _SHIPPING_SNAPSHOT_FIELDS
        )
        previous = _snapshot(rule, fields)

        effective = {
            field: value
            for field, value in changes.items()
            if getattr(rule, field, object()) != value
        }
        if not effective:
            # A no-op writes nothing and records nothing. Bumping the version
            # for a save that changed no setting would fill the history with
            # entries a merchant cannot distinguish from real changes -- and
            # would move `updated_at`, invalidating other editors' tokens.
            return rule

        updated = await repo.update_if_unmodified_since(
            rule_id,
            expected_updated_at=expected_updated_at,
            version=rule.version + 1,
            **effective,
        )
        if not updated:
            raise ConflictError(
                "This rule was changed since you loaded it. Reload to see the "
                "latest version before saving again.",
            )
        try:
            await self.flush()
        except IntegrityError as exc:
            raise self._translate_integrity(exc, kind=kind) from exc

        await self.session.refresh(rule)
        await self._record(rule, kind=kind, previous=previous, actor_id=actor_id, note=note)
        return rule

    async def _record(
        self,
        rule: PricingRule | ShippingRule,
        *,
        kind: GlobalRuleKind,
        previous: dict[str, Any],
        actor_id: uuid.UUID | None,
        note: str | None,
    ) -> GlobalRuleVersion:
        """Append one history entry. Called only from this module.

        Runs inside the caller's transaction on purpose: if the rule write
        rolls back, so does its history, and the two can never disagree.
        """
        fields = (
            _PRICING_SNAPSHOT_FIELDS
            if kind is GlobalRuleKind.PRICING
            else _SHIPPING_SNAPSHOT_FIELDS
        )
        new_values = _snapshot(rule, fields)
        changed = (
            sorted(name for name in fields if previous.get(name) != new_values.get(name))
            if previous
            else sorted(fields)
        )

        latest = await self.versions.latest_version_number(rule_kind=kind, rule_id=rule.id)
        # `latest_version_number` only picks the candidate; the unique
        # constraint on (tenant, kind, rule, version) is what actually settles
        # two writers who both read the same number. Uncaught, that collision
        # surfaced as a 500 naming a database index -- which tells a merchant
        # nothing and leaks the schema. It is the same "someone else changed
        # this while you were working" the concurrency token reports, so it
        # gets the same answer.
        try:
            return await self.versions.create(
                rule_kind=kind,
                rule_id=rule.id,
                pricing_rule_id=rule.id if kind is GlobalRuleKind.PRICING else None,
                shipping_rule_id=rule.id if kind is GlobalRuleKind.SHIPPING else None,
                version=latest + 1,
                changed_fields=changed,
                previous_values=previous,
                new_values=new_values,
                snapshot=new_values,
                changed_by_user_id=actor_id,
                note=note,
                is_active=bool(rule.is_active),
                products_affected=0,
            )
        except IntegrityError as exc:
            raise self._translate_integrity(exc, kind=kind) from exc
        except ConflictError as exc:
            # The repository already turned the constraint into a conflict, but
            # with the generic wording it uses for any duplicate. A version
            # collision has a specific, actionable explanation, so it gets one.
            raise ConflictError(
                "This rule was changed by someone else at the same moment. Reload it and try again."
            ) from exc

    @staticmethod
    def _translate_integrity(exc: IntegrityError, *, kind: GlobalRuleKind) -> Exception:
        """Turn a database constraint into the merchant-facing reason.

        The partial unique indexes added in 0024 are the only thing that can
        settle two concurrent activations; this is where that raw failure
        becomes an explanation rather than a 500.
        """
        message = str(getattr(exc, "orig", exc))
        if "one_active_global" in message:
            noun = "pricing" if kind is GlobalRuleKind.PRICING else "shipping"
            return ConflictError(
                f"This tenant already has an active global {noun} rule. "
                "Deactivate it before activating another."
            )
        if "tenant_name" in message:
            return ConflictError("A rule with that name already exists.")
        if "rule_version" in message or "global_rule_versions" in message:
            # Two writers assigned the same version number. Nothing was
            # written -- the whole transaction, rule change included, rolls
            # back -- so retrying is genuinely safe and is what to advise.
            return ConflictError(
                "This rule was changed by someone else at the same moment. Reload it and try again."
            )
        return ValidationError("That rule could not be saved.")

    # ------------------------------------------------------------ resolution
    async def resolve_pricing(
        self,
        *,
        product_id: uuid.UUID | None,
        variant_id: uuid.UUID | None = None,
        store_id: uuid.UUID | None = None,
        category_id: str | None = None,
    ) -> RuleResolution[PricingRule]:
        candidates = await self.pricing.find_candidates(
            product_id=product_id or uuid.uuid4(),
            variant_id=variant_id,
            store_id=store_id,
            category_id=category_id,
        )
        return resolve_pricing_rule(
            candidates,
            product_id=product_id,
            variant_id=variant_id,
            store_id=store_id,
            category_id=category_id,
        )

    async def resolve_shipping(
        self,
        *,
        product_id: uuid.UUID | None,
        variant_id: uuid.UUID | None = None,
        store_id: uuid.UUID | None = None,
        category_id: str | None = None,
    ) -> RuleResolution[ShippingRule]:
        candidates = await self.shipping.find_candidates(
            product_id=product_id,
            variant_id=variant_id,
            store_id=store_id,
            category_id=category_id,
        )
        return resolve_shipping_rule(
            candidates,
            product_id=product_id,
            variant_id=variant_id,
            store_id=store_id,
            category_id=category_id,
        )

    # --------------------------------------------------------------- preview
    async def preview(
        self,
        inputs: PreviewInputs,
        *,
        product_id: uuid.UUID | None = None,
        variant_id: uuid.UUID | None = None,
        store_id: uuid.UUID | None = None,
        category_id: str | None = None,
        rule_id: uuid.UUID | None = None,
    ) -> RulePreview:
        """Calculate one price from sample figures, writing nothing.

        Constructs a transient ``Product`` that is never added to the
        session, so the whole call is provably read-only: there is no object
        for a flush to pick up. That is stronger than remembering not to
        commit.
        """
        if rule_id is not None:
            resolution = RuleResolution(
                rule=await self.get_pricing_rule(rule_id),
                scope=None,
                version=None,
                reason="Explicitly requested rule.",
            )
            rule = resolution.rule
            resolution = RuleResolution(
                rule=rule,
                scope=rule.scope if rule else None,
                version=rule.version if rule else None,
                reason="Explicitly requested rule.",
            )
        else:
            resolution = await self.resolve_pricing(
                product_id=product_id,
                variant_id=variant_id,
                store_id=store_id,
                category_id=category_id,
            )

        sample = Product(
            source=ProductSource.MANUAL,
            external_id="preview",
            title="Preview",
            status=ProductStatus.DRAFT,
            cost_price_min=inputs.item_cost,
            shipping_cost=inputs.shipping_cost,
            currency=inputs.currency,
        )
        return RulePreview(
            resolution=resolution,
            calculation=calculate_price(sample, rule=resolution.rule),
        )


def now_utc() -> datetime:
    return datetime.now(UTC)


__all__ = ["GlobalRuleService", "PreviewInputs", "RulePreview"]
