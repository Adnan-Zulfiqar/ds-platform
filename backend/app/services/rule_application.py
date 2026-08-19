"""Applying global rules to drafts: at import, in preview, and in bulk (M3A-3).

Three surfaces, one calculation. Import, preview and confirmed application all
go through :meth:`DraftPricingService.calculate_for` so a merchant can never
see one number in the preview and a different one after confirming.

Nothing here does arithmetic. Landed cost, strategies, rounding, precedence
and shipping selection all come from the M3A-1 core.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.pricing import PricingRule
from app.models.product import Product, ProductVariant
from app.models.rule_application import (
    ApplicationItemOutcome,
    ApplicationStatus,
    RuleApplication,
    RuleApplicationItem,
)
from app.models.shopify import ListingSyncStatus, StoreListing
from app.repositories.product import ProductRepository
from app.services.base import BaseService
from app.services.global_rules import GlobalRuleService
from app.services.pricing_engine import PriceCalculation, calculate_price
from app.services.rule_resolution import RuleResolution
from app.services.shipping_rules import ShippingQuote, ShippingSelection, select_shipping

#: Ceiling on one preview page. A settings screen must never try to load a
#: 10,000-product catalogue into memory to answer "what would this do".
MAX_PREVIEW_PAGE = 200


@dataclass(frozen=True, slots=True)
class VariantOutcome:
    """One variant's proposed price and why."""

    variant_id: uuid.UUID
    label: str | None
    current_price: Decimal | None
    calculation: PriceCalculation
    resolution: RuleResolution[PricingRule]

    @property
    def review_reasons(self) -> tuple[str, ...]:
        return self.calculation.review_reasons

    @property
    def can_apply(self) -> bool:
        return self.calculation.price is not None and not self.calculation.needs_review


@dataclass(frozen=True, slots=True)
class ProductOutcome:
    """A product's proposal, including every variant's.

    A failed variant is present here with its reason rather than dropped: a
    variant that silently disappears from a preview is one the merchant
    cannot discover is mispriced.
    """

    product: Product
    resolution: RuleResolution[PricingRule]
    shipping: ShippingSelection | None
    calculation: PriceCalculation
    variants: tuple[VariantOutcome, ...] = ()
    published: bool = False
    review_reasons: tuple[str, ...] = ()

    @property
    def proposed_price(self) -> Decimal | None:
        """Lowest enabled variant price, or the product-level figure.

        A product with variants advertises a "from" price; deriving it from
        the variants rather than pricing the product separately is what keeps
        the two from disagreeing.
        """
        priced = [v.calculation.price for v in self.variants if v.calculation.price is not None]
        if priced:
            return min(priced)
        return self.calculation.price

    @property
    def needs_review(self) -> bool:
        return bool(self.review_reasons) or any(v.calculation.needs_review for v in self.variants)

    @property
    def can_apply(self) -> bool:
        if self.published or self.needs_review:
            return False
        return self.proposed_price is not None


class DraftPricingService(BaseService):
    """Calculates what a draft *should* cost. Writes only when told to."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.rules = GlobalRuleService(session)
        self.products = ProductRepository(session)

    async def is_published(self, product_id: uuid.UUID) -> bool:
        """Publication truth is channel state, not ``Product.status``.

        Mirrors ``ProductRepository._synced_listing_exists``: import leaves
        `status` as ``draft`` and a Shopify publish does not flip it, so
        reading `status` here would let a live listing be repriced.
        """
        query = (
            select(StoreListing.id)
            .where(StoreListing.product_id == product_id)
            .where(StoreListing.status == ListingSyncStatus.SYNCED)
            .limit(1)
        )
        return (await self.session.execute(query)).scalars().first() is not None

    async def calculate_for(
        self,
        product: Product,
        *,
        variants: list[ProductVariant] | None = None,
        quotes: list[ShippingQuote] | None = None,
        destination_country: str | None = None,
    ) -> ProductOutcome:
        """The single calculation every M3A-3 surface uses."""
        published = await self.is_published(product.id)

        pricing = await self.rules.resolve_pricing(
            product_id=product.id,
            store_id=product.store_id,
            category_id=product.category_id,
        )
        shipping_resolution = await self.rules.resolve_shipping(
            product_id=product.id,
            store_id=product.store_id,
            category_id=product.category_id,
        )

        selection: ShippingSelection | None = None
        shipping_cost = product.shipping_cost
        extra: list[str] = []

        # Only run shipping selection when a rule actually asks for it. A
        # tenant with no shipping rule keeps pre-M3A behaviour and is not
        # suddenly told its whole catalogue needs review.
        if shipping_resolution.rule is not None:
            selection = select_shipping(
                quotes or [],
                rule=shipping_resolution.rule,
                destination_country=destination_country or product.ship_to_country,
            )
            extra.extend(selection.review_reasons)
            if selection.cost is not None:
                shipping_cost = selection.cost

        calculation = calculate_price(
            product,
            rule=pricing.rule,
            shipping_cost=shipping_cost,
            extra_review_reasons=tuple(extra),
        )

        variant_outcomes: list[VariantOutcome] = []
        for variant in variants or []:
            # Each variant is priced from its own supplier cost. A product
            # whose variants differ in cost must not inherit one price.
            view = _VariantCostView(
                cost_price_min=variant.cost_price,
                shipping_cost=shipping_cost,
                currency=variant.currency or product.currency,
            )
            variant_resolution = await self.rules.resolve_pricing(
                product_id=product.id,
                variant_id=variant.id,
                store_id=product.store_id,
                category_id=product.category_id,
            )
            variant_outcomes.append(
                VariantOutcome(
                    variant_id=variant.id,
                    label=variant.label,
                    current_price=variant.sell_price,
                    calculation=calculate_price(
                        view,
                        rule=variant_resolution.rule,
                        extra_review_reasons=tuple(extra),
                    ),
                    resolution=variant_resolution,
                )
            )

        return ProductOutcome(
            product=product,
            resolution=pricing,
            shipping=selection,
            calculation=calculation,
            variants=tuple(variant_outcomes),
            published=published,
            review_reasons=tuple(calculation.review_reasons),
        )

    def stamp(self, outcome: ProductOutcome) -> None:
        """Record on the product how its price was reached.

        Written whenever a rule prices a product, including when it refuses
        to: `needs_review` plus the reasons is the evidence a merchant acts
        on, and leaving it unset would make "why was nothing applied"
        unanswerable.
        """
        product = outcome.product
        rule = outcome.resolution.rule
        product.applied_pricing_rule_id = rule.id if rule else None
        product.applied_pricing_rule_version = rule.version if rule else None
        if outcome.shipping is not None and outcome.shipping.rule is not None:
            product.applied_shipping_rule_id = outcome.shipping.rule.id
            product.applied_shipping_rule_version = outcome.shipping.rule.version
        product.landed_cost = outcome.calculation.landed.amount
        product.landed_cost_fees = outcome.calculation.landed.fees
        product.pricing_calculated_at = datetime.now(UTC)
        product.needs_review = outcome.needs_review
        product.pricing_review_reasons = list(
            dict.fromkeys(
                list(outcome.review_reasons)
                + [r for v in outcome.variants for r in v.review_reasons]
            )
        )


@dataclass(slots=True)
class _VariantCostView:
    """A variant's cost fields in the shape ``calculate_price`` reads."""

    cost_price_min: Decimal | None
    shipping_cost: Decimal | None
    currency: str | None


@dataclass(frozen=True, slots=True)
class PreviewPage:
    """One page of impact preview, plus the counts the screen needs."""

    items: tuple[ProductOutcome, ...]
    total: int
    page: int
    size: int


class ImpactPreviewService(BaseService):
    """Read-only "what would this do to my existing drafts".

    Every method here is a read. The service holds no write path at all,
    which is a stronger guarantee than remembering not to commit: there is no
    code to accidentally reach.
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.pricing = DraftPricingService(session)
        self.products = ProductRepository(session)

    async def preview(
        self,
        *,
        product_ids: list[uuid.UUID] | None = None,
        search: str | None = None,
        needs_review_only: bool = False,
        page: int = 1,
        size: int = 25,
    ) -> PreviewPage:
        size = max(1, min(size, MAX_PREVIEW_PAGE))
        page = max(1, page)

        base = (
            select(Product)
            .where(Product.tenant_id == self._tenant_id())
            .where(Product.deleted_at.is_(None))
        )
        if product_ids:
            base = base.where(Product.id.in_(product_ids))
        if search:
            base = base.where(Product.title.ilike(f"%{search.strip()}%"))
        if needs_review_only:
            base = base.where(Product.needs_review.is_(True))

        total = (
            await self.session.execute(select(func.count()).select_from(base.subquery()))
        ).scalar_one()

        # Paginated at the database, not in Python: a 10,000-product
        # catalogue must never be materialised to answer one page.
        rows = (
            (
                await self.session.execute(
                    base.order_by(Product.created_at.desc()).offset((page - 1) * size).limit(size)
                )
            )
            .scalars()
            .all()
        )

        outcomes: list[ProductOutcome] = []
        for product in rows:
            variants = (
                (
                    await self.session.execute(
                        select(ProductVariant)
                        .where(ProductVariant.product_id == product.id)
                        .where(ProductVariant.deleted_at.is_(None))
                    )
                )
                .scalars()
                .all()
            )
            outcomes.append(await self.pricing.calculate_for(product, variants=list(variants)))

        return PreviewPage(items=tuple(outcomes), total=total, page=page, size=size)

    def _tenant_id(self) -> uuid.UUID:
        from app.core.context import require_tenant_id

        return require_tenant_id()


@dataclass(frozen=True, slots=True)
class ApplyRequest:
    """A confirmed application. The fingerprint is derived from this."""

    product_ids: tuple[uuid.UUID, ...]
    idempotency_key: str
    expected_rule_id: uuid.UUID | None = None
    expected_rule_version: int | None = None
    variant_ids: tuple[uuid.UUID, ...] = ()

    def fingerprint(self) -> str:
        """Stable hash of what was confirmed.

        A retry carrying the same key but a *different* payload is a client
        bug, not a retry, and must be refused rather than silently answered
        with the first run's result.
        """
        payload = json.dumps(
            {
                "products": sorted(str(p) for p in self.product_ids),
                "variants": sorted(str(v) for v in self.variant_ids),
                "rule": str(self.expected_rule_id) if self.expected_rule_id else None,
                "version": self.expected_rule_version,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()


class RuleApplicationService(BaseService):
    """Runs a confirmed application and records exactly what it did."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.pricing = DraftPricingService(session)
        self.products = ProductRepository(session)

    async def get(self, application_id: uuid.UUID) -> RuleApplication:
        query = (
            select(RuleApplication)
            .where(RuleApplication.tenant_id == self._tenant_id())
            .where(RuleApplication.id == application_id)
        )
        found = (await self.session.execute(query)).scalars().first()
        if found is None:
            # 404 for missing and for another tenant's alike -- a different
            # response would confirm the id exists.
            raise NotFoundError("Application not found.")
        return found

    async def start(self, request: ApplyRequest, *, actor_id: uuid.UUID | None) -> RuleApplication:
        """Create and run an application, or return the existing one.

        Runs synchronously inside the request transaction for the selection
        sizes this endpoint accepts. The service boundary is deliberately the
        unit a background worker would call, so moving execution onto the
        existing Celery queue later changes the caller, not this logic -- and
        does not introduce a second queue.
        """
        if not request.idempotency_key.strip():
            raise ValidationError("An idempotency key is required.")
        if not request.product_ids:
            raise ValidationError("Select at least one draft to apply rules to.")

        fingerprint = request.fingerprint()
        existing = await self._find_by_key(request.idempotency_key)
        if existing is not None:
            if existing.request_fingerprint != fingerprint:
                raise ConflictError(
                    "That idempotency key was already used with a different request."
                )
            # A genuine retry: hand back the original run untouched.
            return existing

        application = RuleApplication(
            tenant_id=self._tenant_id(),
            idempotency_key=request.idempotency_key.strip(),
            request_fingerprint=fingerprint,
            status=ApplicationStatus.RUNNING,
            pricing_rule_id=request.expected_rule_id,
            pricing_rule_version=request.expected_rule_version,
            requested_by_user_id=actor_id,
            selection={
                "productIds": [str(p) for p in request.product_ids],
                "variantIds": [str(v) for v in request.variant_ids],
            },
            started_at=datetime.now(UTC),
        )
        self.session.add(application)
        try:
            await self.flush()
        except IntegrityError as exc:
            # Lost the race to a concurrent identical request; that request's
            # run is the answer.
            await self.session.rollback()
            concurrent = await self._find_by_key(request.idempotency_key)
            if concurrent is not None:
                return concurrent
            raise ConflictError("That application could not be started.") from exc

        await self._run(application, request)
        return application

    async def cancel(self, application_id: uuid.UUID) -> RuleApplication:
        """Cancel a run that has not started writing.

        A completed or partial run is not cancellable: the writes already
        landed, and pretending otherwise would misrepresent the catalogue.
        """
        application = await self.get(application_id)
        if application.status not in (ApplicationStatus.PENDING, ApplicationStatus.RUNNING):
            raise ConflictError(
                f"An application that is {application.status.value} cannot be cancelled."
            )
        if application.applied_count:
            raise ConflictError(
                "This application has already written prices and cannot be cancelled."
            )
        application.status = ApplicationStatus.CANCELLED
        application.finished_at = datetime.now(UTC)
        await self.flush()
        return application

    # ------------------------------------------------------------- internals
    async def _run(self, application: RuleApplication, request: ApplyRequest) -> None:
        counts = {"applied": 0, "skipped": 0, "review": 0, "failed": 0}

        for product_id in request.product_ids:
            product = (
                (
                    await self.session.execute(
                        select(Product)
                        .where(Product.tenant_id == application.tenant_id)
                        .where(Product.id == product_id)
                        .where(Product.deleted_at.is_(None))
                    )
                )
                .scalars()
                .first()
            )

            if product is None:
                # A foreign or deleted id is recorded, not raised: one bad id
                # must not roll back every draft that priced correctly.
                # No `product_id`: the row is constrained by a foreign key
                # and this id resolves to nothing in this tenant. Recording
                # it in the message keeps the result complete without
                # inventing a reference.
                self._record(
                    application,
                    None,
                    None,
                    ApplicationItemOutcome.FAILED,
                    message=f"Draft {product_id} was not found in this workspace.",
                )
                counts["failed"] += 1
                continue

            variants = (
                (
                    await self.session.execute(
                        select(ProductVariant)
                        .where(ProductVariant.product_id == product.id)
                        .where(ProductVariant.deleted_at.is_(None))
                    )
                )
                .scalars()
                .all()
            )

            outcome = await self.pricing.calculate_for(product, variants=list(variants))

            if outcome.published:
                self._record(
                    application,
                    product.id,
                    None,
                    ApplicationItemOutcome.PUBLISHED,
                    message="Published products are never repriced by this workflow.",
                )
                counts["skipped"] += 1
                continue

            if (
                request.expected_rule_id is not None
                and outcome.resolution.rule is not None
                and (
                    outcome.resolution.rule.id != request.expected_rule_id
                    or outcome.resolution.version != request.expected_rule_version
                )
            ):
                self._record(
                    application,
                    product.id,
                    None,
                    ApplicationItemOutcome.STALE,
                    message="The governing rule changed after this preview was taken.",
                )
                counts["skipped"] += 1
                continue

            if not outcome.can_apply:
                self._record(
                    application,
                    product.id,
                    None,
                    ApplicationItemOutcome.NEEDS_REVIEW,
                    reasons=list(outcome.review_reasons),
                    message="Held for review; the inputs were not complete enough to price.",
                )
                self.pricing.stamp(outcome)
                counts["review"] += 1
                continue

            previous = product.sell_price
            proposed = outcome.proposed_price
            if previous == proposed:
                self._record(
                    application,
                    product.id,
                    None,
                    ApplicationItemOutcome.SKIPPED,
                    previous=previous,
                    new=proposed,
                    message="Already at the proposed price.",
                )
                counts["skipped"] += 1
                continue

            product.sell_price = proposed
            for variant_outcome in outcome.variants:
                if not variant_outcome.can_apply:
                    self._record(
                        application,
                        product.id,
                        variant_outcome.variant_id,
                        ApplicationItemOutcome.NEEDS_REVIEW,
                        reasons=list(variant_outcome.review_reasons),
                        message="Variant held for review.",
                    )
                    counts["review"] += 1
                    continue
                variant = next(v for v in variants if v.id == variant_outcome.variant_id)
                variant.sell_price = variant_outcome.calculation.price
                variant.compare_at_price = variant_outcome.calculation.compare_at
                self._record(
                    application,
                    product.id,
                    variant.id,
                    ApplicationItemOutcome.APPLIED,
                    previous=variant_outcome.current_price,
                    new=variant_outcome.calculation.price,
                    landed=variant_outcome.calculation.landed.amount,
                )

            self.pricing.stamp(outcome)
            self._record(
                application,
                product.id,
                None,
                ApplicationItemOutcome.APPLIED,
                previous=previous,
                new=proposed,
                landed=outcome.calculation.landed.amount,
                version=outcome.resolution.version,
            )
            counts["applied"] += 1

        application.total_count = len(request.product_ids)
        application.applied_count = counts["applied"]
        application.skipped_count = counts["skipped"]
        application.review_count = counts["review"]
        application.failed_count = counts["failed"]
        application.finished_at = datetime.now(UTC)
        application.status = (
            ApplicationStatus.COMPLETED
            if counts["failed"] == 0 and counts["review"] == 0
            else ApplicationStatus.PARTIAL
        )
        await self.flush()

        # Load the results explicitly before returning. `selectin` eager
        # loading applies when an application is *queried*, not to one just
        # built in this session, so the collection is stale; touching it
        # afterwards would trigger a lazy load, which is implicit IO and
        # raises `MissingGreenlet` in async SQLAlchemy. Same reasoning as
        # `ProductImportService.import_product`.
        await self.session.refresh(application, attribute_names=["items"])

    def _record(
        self,
        application: RuleApplication,
        product_id: uuid.UUID | None,
        variant_id: uuid.UUID | None,
        outcome: ApplicationItemOutcome,
        *,
        previous: Decimal | None = None,
        new: Decimal | None = None,
        landed: Decimal | None = None,
        version: int | None = None,
        reasons: list[str] | None = None,
        message: str | None = None,
    ) -> None:
        self.session.add(
            RuleApplicationItem(
                tenant_id=application.tenant_id,
                application_id=application.id,
                product_id=product_id,
                variant_id=variant_id,
                outcome=outcome,
                previous_price=previous,
                new_price=new,
                landed_cost=landed,
                applied_rule_version=version,
                review_reasons=reasons or [],
                message=message,
            )
        )

    async def _find_by_key(self, key: str) -> RuleApplication | None:
        query = (
            select(RuleApplication)
            .where(RuleApplication.tenant_id == self._tenant_id())
            .where(RuleApplication.idempotency_key == key.strip())
        )
        return (await self.session.execute(query)).scalars().first()

    def _tenant_id(self) -> uuid.UUID:
        from app.core.context import require_tenant_id

        return require_tenant_id()


__all__ = [
    "MAX_PREVIEW_PAGE",
    "ApplyRequest",
    "DraftPricingService",
    "ImpactPreviewService",
    "PreviewPage",
    "ProductOutcome",
    "RuleApplicationService",
    "VariantOutcome",
]
