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
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import ColumnElement, Select, and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    ConflictError,
    FxUnavailableError,
    NotFoundError,
    ValidationError,
)
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
from app.services.pricing_engine import (
    REVIEW_CALCULATION_FAILED,
    REVIEW_FX_UNAVAILABLE,
    CostBearing,
    PriceCalculation,
    calculate_price,
    convert_currency,
    landed_cost,
)
from app.services.rule_resolution import RuleResolution
from app.services.shipping_rules import ShippingQuote, ShippingSelection, select_shipping

#: Ceiling on one preview page. A settings screen must never try to load a
#: 10,000-product catalogue into memory to answer "what would this do".
MAX_PREVIEW_PAGE = 200

#: Ceiling on one confirmed application. Published in the preview response so
#: the screen knows what it may submit, and refused with this number named
#: rather than silently truncated -- a merchant who selected 6,000 drafts and
#: got 5,000 repriced with no warning would have no way to find the other
#: thousand.
MAX_APPLICATION_PRODUCTS = 5_000

#: Products per worker transaction. Bounds both how much a single commit can
#: lose to a crash and how many ORM objects a session holds at once; a
#: 5,000-product run is 100 short transactions, not one enormous one.
APPLICATION_BATCH_SIZE = 50

#: How long a `running` application may go without a heartbeat before it is
#: considered abandoned. Generously above the batch soft time limit: the cost
#: of waiting too long is a delayed retry, while the cost of reclaiming too
#: early is two workers on one run, and only the second is dangerous.
STALE_AFTER = timedelta(minutes=15)

#: Times a run may be reclaimed before it is parked as failed. A run that dies
#: the same way on every attempt is a defect to look at, not work to retry
#: forever -- and an unbounded loop would hide it.
MAX_RECOVERIES = 3


def stale_running_predicate(*, now: datetime | None = None) -> ColumnElement[bool]:
    """ "This `running` row has stopped reporting." One definition, two callers.

    The reconciler's sweep and the reclaim's conditional UPDATE must agree
    exactly, or the sweep selects rows the write then refuses -- a silent
    no-op loop that looks like a working recovery path.

    **The NULL branch is not defensive padding.** ``heartbeat_at`` arrived in
    migration 0027, so a run abandoned before that upgrade carries NULL, and
    ``heartbeat_at < cutoff`` is NULL -- never true. Those rows would sit
    `running` forever with nothing able to see them. 0028 backfills the ones
    that exist; this covers any that appear another way, and it ages them from
    ``started_at``/``created_at`` so a row that is *legitimately* mid-claim for
    a moment is not mistaken for an abandoned one.
    """
    cutoff = (now or datetime.now(UTC)) - STALE_AFTER
    return or_(
        RuleApplication.heartbeat_at < cutoff,
        and_(
            RuleApplication.heartbeat_at.is_(None),
            func.coalesce(RuleApplication.started_at, RuleApplication.created_at) < cutoff,
        ),
    )


def _price(
    view: CostBearing,
    *,
    rule: PricingRule | None,
    shipping_cost: Decimal | None = None,
    extra: tuple[str, ...] = (),
) -> PriceCalculation:
    """``calculate_price`` with the two guards every M3A surface needs.

    **Currency.** A rule that declares a currency may only price a cost in
    that same currency. A rule with none declares no denomination and prices
    in the product's, which is the ordinary single-currency case and is left
    exactly as it was. The check goes through ``convert_currency`` rather
    than comparing strings here, so there is still one place that decides
    what a currency pair means -- and that function refuses to invent a 1:1
    rate, which is the whole point. ``markup_percent`` would survive a
    mismatch unscathed, but ``min_price``, ``max_price``, ``markup_fixed``,
    ``min_profit`` and ``fees_fixed`` are money, and applying a GBP floor to
    a USD cost is a silent mispricing rather than a visible failure.

    **Evaluation.** A rule whose strategy is missing the field it needs
    raises out of ``compute_sell_price``. Left uncaught, that failed an
    entire import over one misconfigured rule. It becomes a review reason
    instead: no price, a flagged draft, and an import that still completes.
    """
    if rule is not None and rule.currency is not None:
        try:
            convert_currency(Decimal("0"), from_currency=view.currency, to_currency=rule.currency)
        except FxUnavailableError:
            return calculate_price(
                view,
                rule=rule,
                shipping_cost=shipping_cost,
                extra_review_reasons=(*extra, REVIEW_FX_UNAVAILABLE),
            )

    try:
        return calculate_price(
            view, rule=rule, shipping_cost=shipping_cost, extra_review_reasons=extra
        )
    except (ValidationError, ArithmeticError):
        # The landed cost is still computed under the rule -- its duty and
        # fees are readable even when its strategy is not -- so the merchant
        # sees the real cost breakdown next to "this rule could not price it".
        landed = landed_cost(view, rule=rule, shipping_cost=shipping_cost)
        return PriceCalculation(
            landed=landed,
            rule=rule,
            price=None,
            compare_at=None,
            review_reasons=(*landed.review_reasons, *extra, REVIEW_CALCULATION_FAILED),
        )


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

        calculation = _price(
            product,
            rule=pricing.rule,
            shipping_cost=shipping_cost,
            extra=tuple(extra),
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
                    calculation=_price(
                        view,
                        rule=variant_resolution.rule,
                        extra=tuple(extra),
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
class DraftSelectionFilter:
    """What a merchant is looking at, and therefore what "select all" means.

    One object shared by the preview and by selection, so the set shown and
    the set applied are produced by the same predicate. Two separate query
    builders would eventually disagree, and the merchant would confirm a
    number that did not match what ran.

    ``safe_only`` excludes published drafts, those already flagged for review,
    and those missing any of the three supplier figures the calculation refuses
    to invent -- item cost, freight and currency.

    Those three columns are checked directly rather than trusting
    ``needs_review``, which is only set once something has actually priced the
    draft: a product imported before any rule existed carries no flag, and
    filtering on the flag alone told merchants "5 ready" for a selection that
    included one nothing could price. Checking the inputs costs one more
    predicate and makes the count mean what it says.

    It is still a filter over recorded state rather than a promise -- a rule
    denominated in another currency, or a shipping rule with no matching quote,
    can still hold a draft back when the run reaches it. Those are recorded as
    `needs_review` in the results rather than written, which is the same
    fail-closed behaviour every other surface has.
    """

    search: str | None = None
    needs_review_only: bool = False
    safe_only: bool = False
    product_ids: tuple[uuid.UUID, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "search": self.search,
            "needsReviewOnly": self.needs_review_only,
            "safeOnly": self.safe_only,
            "productIds": [str(p) for p in self.product_ids],
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any] | None) -> DraftSelectionFilter:
        data = payload or {}
        return cls(
            search=data.get("search"),
            needs_review_only=bool(data.get("needsReviewOnly")),
            safe_only=bool(data.get("safeOnly")),
            product_ids=tuple(uuid.UUID(p) for p in data.get("productIds", [])),
        )


def draft_query(tenant_id: uuid.UUID, selection: DraftSelectionFilter) -> Select[tuple[Product]]:
    """The one predicate behind both the preview and "select all matching"."""
    query = (
        select(Product).where(Product.tenant_id == tenant_id).where(Product.deleted_at.is_(None))
    )
    if selection.product_ids:
        query = query.where(Product.id.in_(selection.product_ids))
    if selection.search:
        query = query.where(Product.title.ilike(f"%{selection.search.strip()}%"))
    if selection.needs_review_only:
        query = query.where(Product.needs_review.is_(True))
    if selection.safe_only:
        # Published drafts are excluded in the database rather than filtered
        # out afterwards, so "select all matching" can never hand the worker a
        # live listing to reprice in the first place.
        published = (
            select(StoreListing.product_id)
            .where(StoreListing.product_id == Product.id)
            .where(StoreListing.status == ListingSyncStatus.SYNCED)
        )
        query = (
            query.where(~published.exists())
            .where(Product.needs_review.is_(False))
            .where(Product.cost_price_min.is_not(None))
            .where(Product.shipping_cost.is_not(None))
            .where(Product.currency.is_not(None))
        )
    return query


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
        selection: DraftSelectionFilter | None = None,
        *,
        page: int = 1,
        size: int = 25,
    ) -> PreviewPage:
        size = max(1, min(size, MAX_PREVIEW_PAGE))
        page = max(1, page)

        base = draft_query(self._tenant_id(), selection or DraftSelectionFilter())

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

    async def count_matching(self, selection: DraftSelectionFilter) -> int:
        """How many drafts a filter matches, without loading any of them.

        What "select all matching" would cover, counted in the database. The
        alternative -- inferring it from the page the merchant can see -- would
        understate every selection beyond the first page.
        """
        query = select(func.count()).select_from(
            draft_query(self._tenant_id(), selection).subquery()
        )
        return int((await self.session.execute(query)).scalar_one())

    def _tenant_id(self) -> uuid.UUID:
        from app.core.context import require_tenant_id

        return require_tenant_id()


@dataclass(frozen=True, slots=True)
class ApplyRequest:
    """A confirmed application. The fingerprint is derived from this."""

    product_ids: tuple[uuid.UUID, ...] = ()
    idempotency_key: str = ""
    expected_rule_id: uuid.UUID | None = None
    expected_rule_version: int | None = None
    variant_ids: tuple[uuid.UUID, ...] = ()
    #: Set instead of `product_ids` when the merchant confirmed "everything
    #: matching what I am looking at". Resolved to concrete ids server-side at
    #: creation -- see `RuleApplicationService.create`.
    selection_filter: DraftSelectionFilter | None = None

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
                "filter": self.selection_filter.to_json() if self.selection_filter else None,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()


class ClaimResult(StrEnum):
    """Why a worker did or did not take ownership of a run."""

    #: Won the `pending -> running` transition. Nobody else can now.
    CLAIMED = "claimed"
    #: Already `running` under *this* task id -- a redelivery or retry of the
    #: message that claimed it. Resuming is correct; restarting is not. The
    #: run is re-leased under a *new* token, so any earlier process still
    #: holding the old one is fenced out by the same act.
    RESUMED = "resumed"
    #: Already `running` under a different task. Left alone.
    ALREADY_RUNNING = "already_running"
    #: Cancelled, or already finished. A delayed message must not revive it.
    NOT_CLAIMABLE = "not_claimable"
    #: No such application. A message can outlive its row.
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ApplicationLease:
    """One worker's proof that it, and not another, owns a run.

    Carried from the claim into every subsequent operation and checked against
    the row under a lock. Passing it explicitly rather than storing it on the
    service is deliberate: a worker builds a new service per transaction, so
    anything held on the instance would be lost exactly where the guarantee is
    needed.
    """

    application_id: uuid.UUID
    #: Identity of the Celery delivery, for the audit trail and for telling a
    #: redelivery of *this* message from another worker's run.
    task_id: str | None
    #: The fence itself. Unique per attempt; see `RuleApplication.lease_token`.
    token: uuid.UUID


@dataclass(frozen=True, slots=True)
class ClaimOutcome:
    """What the claim decided, and the lease it issued if it granted one."""

    result: ClaimResult
    lease: ApplicationLease | None = None

    @property
    def owns(self) -> bool:
        return self.lease is not None


@dataclass(frozen=True, slots=True)
class LeaseObservation:
    """The exact state a sweep saw, so the write can be conditional on it.

    A reconciler reads in one transaction and writes in another. Between the
    two the worker it is about to declare dead may have committed a batch, or
    a second reconciler may have already acted. Pinning the write to the values
    that justified it turns "probably still stale" into a fact the database
    checks.
    """

    application_id: uuid.UUID
    tenant_id: uuid.UUID
    heartbeat_at: datetime | None
    lease_token: uuid.UUID | None
    recovery_count: int


class BatchOutcome(StrEnum):
    """Why a worker's batch attempt stopped where it did."""

    #: Batch committed; more of the selection remains.
    MORE = "more"
    #: Batch committed; the selection is exhausted.
    DONE = "done"
    #: The merchant cancelled. This worker still owns the run and should
    #: finalise it, keeping the results of every batch that did commit.
    CANCELLED = "cancelled"
    #: **Ownership is gone.** Reclaimed, finished or taken over by another
    #: worker. Nothing was read into a write and nothing was written; the
    #: caller must stop without touching the row.
    LOST = "lost"


class RuleApplicationService(BaseService):
    """Runs a confirmed application and records exactly what it did.

    Split deliberately into *create* (in the request) and *claim / batch /
    finalise* (in a worker), because those happen in different processes and
    at different times. Everything a worker needs is read back from the row:
    the selection, the rule version the merchant confirmed against, and how
    far a previous attempt got.
    """

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

    # ------------------------------------------------------------- request
    async def create(self, request: ApplyRequest, *, actor_id: uuid.UUID | None) -> RuleApplication:
        """Record a confirmed application as `pending`. Writes no prices.

        Returns without running anything: execution belongs to a worker, so
        the merchant's request does not hold a connection open for the length
        of a catalogue-wide reprice. Re-sending the same idempotency key
        returns the original run rather than starting a second one; sending it
        with a different payload is refused as a conflict.
        """
        if not request.idempotency_key.strip():
            raise ValidationError("An idempotency key is required.")
        if not request.product_ids and request.selection_filter is None:
            raise ValidationError("Select at least one draft to apply rules to.")
        if len(request.product_ids) > MAX_APPLICATION_PRODUCTS:
            raise ValidationError(
                f"Select at most {MAX_APPLICATION_PRODUCTS} drafts in one application; "
                f"{len(request.product_ids)} were submitted."
            )

        fingerprint = request.fingerprint()
        existing = await self._find_by_key(request.idempotency_key)
        if existing is not None:
            if existing.request_fingerprint != fingerprint:
                raise ConflictError(
                    "That idempotency key was already used with a different request."
                )
            # A genuine retry: hand back the original run untouched.
            return existing

        if request.selection_filter is not None:
            product_ids = await self._resolve_filter(request.selection_filter)
            if not product_ids:
                raise ValidationError(
                    "Nothing matches that selection, so there is nothing to apply."
                )
        else:
            # Duplicates in the selection are collapsed here rather than at the
            # cursor. The run walks the stored list by index, so a repeated id
            # would otherwise be processed twice and collide on
            # `uq_rule_application_items_target`.
            product_ids = list(dict.fromkeys(request.product_ids))

        application = RuleApplication(
            tenant_id=self._tenant_id(),
            idempotency_key=request.idempotency_key.strip(),
            request_fingerprint=fingerprint,
            status=ApplicationStatus.PENDING,
            pricing_rule_id=request.expected_rule_id,
            pricing_rule_version=request.expected_rule_version,
            requested_by_user_id=actor_id,
            selection={
                "productIds": [str(p) for p in product_ids],
                "variantIds": [str(v) for v in request.variant_ids],
            },
            selection_filter=(
                request.selection_filter.to_json() if request.selection_filter is not None else None
            ),
            total_count=len(product_ids),
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

        # Load the (empty) results collection explicitly. `selectin` eager
        # loading applies when an application is *queried*, not to one just
        # built in this session, so serialising the response would otherwise
        # trigger a lazy load -- implicit IO, which raises `MissingGreenlet`
        # in async SQLAlchemy rather than awaiting.
        await self.session.refresh(application, attribute_names=["items"])
        return application

    async def mark_enqueued(self, application_id: uuid.UUID) -> None:
        """Record that the broker accepted the message.

        Written by the reconciler when it republishes a run the broker never
        took, so `enqueued_at` answers "did this one need rescuing". The
        ordinary path publishes from an ``after_commit`` hook, which is
        synchronous and cannot write back.
        """
        application = await self.get(application_id)
        application.enqueued_at = datetime.now(UTC)
        await self.flush()

    # -------------------------------------------------------------- worker
    async def claim(self, application_id: uuid.UUID, *, task_id: str | None) -> ClaimOutcome:
        """Take ownership of a run, atomically, and receive a lease.

        The `pending -> running` transition *is* the lock: it is a single
        conditional UPDATE, so of two workers handed the same message exactly
        one sees a row change. The loser does no work, which is what makes
        at-least-once delivery safe here.

        The lease token issued alongside is what makes ownership provable
        *later*. Status and task id are not enough on their own: a retry
        carries the same task id as the attempt it replaces, so a check
        against the id would admit both the retry and whatever process is
        still limping along under the original. Every grant of ownership --
        first claim, re-claim after recovery, and the re-lease below -- mints
        a new token, so exactly one process can ever be the current owner.
        """
        now = datetime.now(UTC)
        token = uuid.uuid4()
        claimed = await self.session.execute(
            update(RuleApplication)
            .where(RuleApplication.id == application_id)
            .where(RuleApplication.tenant_id == self._tenant_id())
            .where(RuleApplication.status == ApplicationStatus.PENDING)
            .values(
                status=ApplicationStatus.RUNNING,
                claimed_by_task_id=task_id,
                lease_token=token,
                started_at=now,
                heartbeat_at=now,
            )
            .returning(RuleApplication.id)
            .execution_options(synchronize_session=False)
        )
        if claimed.scalars().first() is not None:
            return ClaimOutcome(ClaimResult.CLAIMED, self._lease(application_id, task_id, token))

        current = (
            (
                await self.session.execute(
                    select(RuleApplication)
                    .where(RuleApplication.tenant_id == self._tenant_id())
                    .where(RuleApplication.id == application_id)
                    # Same reason as `_lock_owned`: this decides ownership, so
                    # it must see the row rather than a remembered copy of it.
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .first()
        )
        if current is None:
            return ClaimOutcome(ClaimResult.UNKNOWN)
        if current.status is not ApplicationStatus.RUNNING:
            # Cancelled or already finished. A message delayed behind a
            # cancellation must not revive the run.
            return ClaimOutcome(ClaimResult.NOT_CLAIMABLE)
        if task_id is None or current.claimed_by_task_id != task_id:
            return ClaimOutcome(ClaimResult.ALREADY_RUNNING)

        # This task's own redelivery. Resuming from the durable cursor is
        # correct; starting over would reprice what already landed. The
        # re-lease is conditional on the token this read observed, so of two
        # deliveries of the same message exactly one wins -- and the process
        # that previously held the run, if it is somehow still alive, loses
        # its fence in the same statement.
        released = await self.session.execute(
            update(RuleApplication)
            .where(RuleApplication.id == application_id)
            .where(RuleApplication.tenant_id == self._tenant_id())
            .where(RuleApplication.status == ApplicationStatus.RUNNING)
            .where(RuleApplication.claimed_by_task_id == task_id)
            .where(RuleApplication.lease_token.is_not_distinct_from(current.lease_token))
            .values(lease_token=token, heartbeat_at=now)
            .returning(RuleApplication.id)
            .execution_options(synchronize_session=False)
        )
        if released.scalars().first() is None:
            return ClaimOutcome(ClaimResult.ALREADY_RUNNING)
        return ClaimOutcome(ClaimResult.RESUMED, self._lease(application_id, task_id, token))

    def _lease(
        self, application_id: uuid.UUID, task_id: str | None, token: uuid.UUID
    ) -> ApplicationLease:
        return ApplicationLease(application_id=application_id, task_id=task_id, token=token)

    async def _lock_owned(self, lease: ApplicationLease) -> RuleApplication | None:
        """Lock the run and return it only if this lease still owns it.

        ``FOR UPDATE`` rather than a plain read, and the caller performs its
        product and audit writes in the *same* transaction. That is what makes
        the check a fence instead of a hint: a reclaim arriving mid-batch
        blocks on the lock until the batch commits, and then finds a heartbeat
        that has moved, so it takes nothing. The reverse order -- reclaim
        first -- leaves this read seeing a token that is no longer ours, and it
        returns ``None`` before a single write is issued.

        A read-then-write without the lock would leave exactly the window this
        exists to close, and an in-process lock would close nothing at all:
        the two workers are different processes, usually on different machines.
        """
        locked = await self._lock_row(lease.application_id)
        if locked is None or locked.lease_token != lease.token:
            return None
        return locked

    async def _lock_row(self, application_id: uuid.UUID) -> RuleApplication | None:
        """Take this tenant's row and hold it for the rest of the transaction.

        The single place any caller acquires the application row, so the
        ordering guarantees are stated once. Two properties matter and both are
        easy to lose:

        * ``FOR UPDATE`` — whoever else wants the row waits here rather than
          reading a version that is about to change. Every decision made after
          this line is made about a row nobody else can move.
        * ``populate_existing`` — SQLAlchemy would otherwise hand back the
          identity-map copy with its *original* attributes, so the lock would be
          taken correctly and then a stale copy inspected. A guard that reads
          its own cache is not a guard.

        Tenant-scoped, so a foreign identifier finds nothing and the caller
        reports it as missing rather than forbidden.
        """
        return (
            (
                await self.session.execute(
                    select(RuleApplication)
                    .where(RuleApplication.tenant_id == self._tenant_id())
                    .where(RuleApplication.id == application_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .first()
        )

    async def run_next_batch(
        self, application_id: uuid.UUID, *, lease: ApplicationLease
    ) -> BatchOutcome:
        """Process one bounded batch, under proof of ownership.

        One transaction per batch, so a crash loses at most one batch and the
        cursor never claims work that was rolled back with it. Ownership is
        re-established at the start of *every* batch rather than once at the
        claim: a worker that stalls long enough to be reclaimed and then wakes
        up is a normal consequence of a bounded heartbeat, and without this it
        would carry on repricing a catalogue a second worker had already taken
        over.
        """
        application = await self._lock_owned(lease)
        if application is None:
            # Reclaimed, finished, or never ours. Nothing has been written in
            # this transaction and nothing will be.
            return BatchOutcome.LOST
        if application.status is ApplicationStatus.CANCELLED:
            # Cooperative stop: the merchant cancelled between batches. Still
            # ours, so the caller may close it out honestly.
            return BatchOutcome.CANCELLED
        if application.status is not ApplicationStatus.RUNNING:
            return BatchOutcome.LOST

        request = self._request_from(application)
        cursor = application.processed_count
        batch = request.product_ids[cursor : cursor + APPLICATION_BATCH_SIZE]
        if not batch:
            return BatchOutcome.DONE

        await self._process(application, request, batch)
        application.processed_count = cursor + len(batch)
        # Stamped in the same transaction as the batch it accounts for. A
        # heartbeat written outside would keep ticking for a worker that had
        # stopped committing anything, which is exactly the state it exists to
        # detect.
        application.heartbeat_at = datetime.now(UTC)
        await self.flush()
        await self._refresh_counts(application)
        await self.flush()
        if application.processed_count < len(request.product_ids):
            return BatchOutcome.MORE
        return BatchOutcome.DONE

    async def finalize(
        self, application_id: uuid.UUID, *, lease: ApplicationLease
    ) -> RuleApplication | None:
        """Close a run and publish its final counts. Owner only.

        ``None`` means the lease no longer owns the run -- another worker took
        it over, so *its* result is the one that will be recorded. Writing a
        terminal status here would overwrite a run still in progress.
        """
        application = await self._lock_owned(lease)
        if application is None:
            return None
        await self._refresh_counts(application)
        if application.status is not ApplicationStatus.CANCELLED:
            application.finished_at = datetime.now(UTC)
            application.status = (
                ApplicationStatus.COMPLETED
                if application.failed_count == 0 and application.review_count == 0
                else ApplicationStatus.PARTIAL
            )
        application.lease_token = None
        await self.flush()
        await self.session.refresh(application, attribute_names=["items"])
        return application

    async def fail(
        self, application_id: uuid.UUID, reason: str, *, lease: ApplicationLease
    ) -> RuleApplication | None:
        """Record that the run could not be completed. Owner only.

        Owner-conditional for the same reason the batch is, and it matters
        most here: a worker that lost its run then raised would otherwise mark
        the *new* owner's run failed from its generic exception handler, which
        is a healthy run destroyed by a dead one. ``None`` means the lease was
        already gone, and the correct action is to write nothing.

        Counts are recomputed rather than zeroed: batches that committed
        before the failure really did reprice those drafts, and hiding them
        would send the merchant looking for changes the catalogue already has.
        """
        application = await self._lock_owned(lease)
        if application is None:
            return None
        if application.status is not ApplicationStatus.RUNNING:
            # Cancelled or already closed out by this same worker. Neither is
            # a failure to record.
            return None
        await self._refresh_counts(application)
        application.status = ApplicationStatus.FAILED
        application.failure_reason = reason[:500]
        application.finished_at = datetime.now(UTC)
        application.lease_token = None
        await self.flush()
        return application

    async def cancel(self, application_id: uuid.UUID) -> RuleApplication:
        """Cancel a run, under an explicit safe policy.

        * ``pending`` -- immediate and total. Nothing was written, and the
          claim will refuse the message when it arrives, so a task already in
          flight cannot revive it.
        * ``running`` -- **cooperative**. The run is marked cancelled and the
          worker stops at its next batch boundary. Batches that already
          committed keep their prices: they are real writes, recorded item by
          item, and silently reverting them would be a second unreviewed
          reprice. There is deliberately no hard task termination here --
          killing a worker mid-transaction is precisely the failure this
          design exists to avoid.
        * finished (``completed`` / ``partial`` / ``failed``) -- refused.
          Reporting a finished run as cancelled would misrepresent what the
          catalogue actually contains.

        Idempotent: cancelling an already-cancelled run returns it unchanged.

        **The row is locked before its status is read** (M3A-H1). This was a
        read-then-write: a plain ``SELECT``, the guard above evaluated against
        it, then a flush. Nothing held the row in between and there is no
        version column, so the flush emitted ``UPDATE … WHERE id = ?`` and
        overwrote whatever had landed meanwhile. A cancellation racing a
        worker's ``finalize`` read `running`, passed the guard, waited on the
        worker's lock, and then rewrote a **completed** run as cancelled —
        precisely the outcome the guard exists to refuse. Prices and result
        rows were never at risk; the record of what happened was, and that is
        the only account a merchant has afterwards.

        Locking first makes the inspection and the mutation one atomic step:
        a cancellation arriving mid-finalize now waits, re-reads the row the
        worker actually left, and refuses it. The lease is deliberately *not*
        consulted — cancelling is the merchant's action and no worker owns it —
        so what makes this safe is the lock and the status, not ownership.
        """
        application = await self._lock_row(application_id)
        if application is None:
            # 404 for missing and for another tenant's alike.
            raise NotFoundError("Application not found.")
        if application.status is ApplicationStatus.CANCELLED:
            # Idempotent and **silent**: returning early rather than rewriting
            # keeps `finished_at`, the reason and the counters at the values
            # the first cancellation recorded. A second request should be able
            # to confirm what happened without changing it.
            return application
        if application.status not in (ApplicationStatus.PENDING, ApplicationStatus.RUNNING):
            raise ConflictError(
                f"An application that is {application.status.value} cannot be cancelled."
            )
        was_running = application.status is ApplicationStatus.RUNNING
        application.status = ApplicationStatus.CANCELLED
        application.finished_at = datetime.now(UTC)
        if was_running:
            application.failure_reason = (
                "Cancelled while running. Drafts already repriced in committed "
                "batches were kept; see the item results."
            )
        await self._refresh_counts(application)
        await self.flush()
        return application

    # ------------------------------------------------------------- internals
    async def _resolve_filter(self, selection: DraftSelectionFilter) -> list[uuid.UUID]:
        """Turn "everything matching" into a concrete, ordered id list.

        Resolved **now**, at confirmation, not later in the worker. The
        merchant confirmed a count they were shown; resolving at run time
        would quietly sweep in drafts imported in the meantime, and the
        durable record would no longer describe what was agreed to.

        Ids only -- no product rows are loaded. The browser never enumerates
        the set either: it sends the filter, and this is what expands it.
        """
        query = (
            draft_query(self._tenant_id(), selection)
            .with_only_columns(Product.id)
            .order_by(Product.created_at.desc())
            .limit(MAX_APPLICATION_PRODUCTS)
        )
        return list((await self.session.execute(query)).scalars().all())

    async def observe(self, application_id: uuid.UUID) -> LeaseObservation | None:
        """Snapshot the fields a recovery decision has to be conditional on."""
        row = (
            (
                await self.session.execute(
                    select(
                        RuleApplication.id,
                        RuleApplication.tenant_id,
                        RuleApplication.heartbeat_at,
                        RuleApplication.lease_token,
                        RuleApplication.recovery_count,
                    )
                    .where(RuleApplication.tenant_id == self._tenant_id())
                    .where(RuleApplication.id == application_id)
                )
            )
            .tuples()
            .first()
        )
        if row is None:
            return None
        return LeaseObservation(
            application_id=row[0],
            tenant_id=row[1],
            heartbeat_at=row[2],
            lease_token=row[3],
            recovery_count=row[4],
        )

    async def reclaim_stale(self, *, observed: LeaseObservation) -> bool:
        """Return an abandoned run to the queue for a *new* worker to claim.

        `running` (silent worker) → **`pending`, unowned**. Not "running under
        a new owner": the run is handed back to the ordinary
        `pending -> running` claim, which is the only transition that can mint
        a lease against a real Celery task id. An earlier version wrote the
        new owner directly and had no id to write, so it left the row
        `running` with a NULL owner and enqueued a message whose task then saw
        `running` under someone else and refused it. Recovery published a
        message that could never do anything. Going back through `pending` is
        what makes the published message *able* to take the run.

        Conditional on the exact state that justified the decision -- the
        heartbeat and lease token the sweep read, the status, and the recovery
        bound. A worker that committed a batch in between moved its heartbeat,
        so this matches no row and keeps its run; a second reconciler racing
        this one finds the values already changed and loses. The guarantee is
        the database's, not a read-then-write in application code.

        The lease token is cleared in the same statement, which is what fences
        the old worker: its next batch locks the row, sees a token that is no
        longer its own, and stops before writing anything.

        Committed batches are untouched. The durable cursor is what the new
        worker resumes from, so nothing is repriced twice and nothing is
        skipped.
        """
        cleared = await self.session.execute(
            update(RuleApplication)
            .where(RuleApplication.id == observed.application_id)
            .where(RuleApplication.tenant_id == self._tenant_id())
            .where(RuleApplication.status == ApplicationStatus.RUNNING)
            .where(RuleApplication.recovery_count == observed.recovery_count)
            .where(RuleApplication.recovery_count < MAX_RECOVERIES)
            .where(RuleApplication.heartbeat_at.is_not_distinct_from(observed.heartbeat_at))
            .where(RuleApplication.lease_token.is_not_distinct_from(observed.lease_token))
            .where(stale_running_predicate())
            .values(
                status=ApplicationStatus.PENDING,
                claimed_by_task_id=None,
                lease_token=None,
                heartbeat_at=None,
                # Cleared so "pending with no enqueued_at" keeps meaning
                # "nothing is going to happen to this until it is published",
                # which is what the pending sweep acts on. Without it a
                # recovered run would look like one already on the broker.
                enqueued_at=None,
                recovery_count=RuleApplication.recovery_count + 1,
            )
            .returning(RuleApplication.id)
            .execution_options(synchronize_session=False)
        )
        return cleared.scalars().first() is not None

    async def abandon_stale(self, *, observed: LeaseObservation) -> bool:
        """Park a run that has been reclaimed too many times.

        Recorded as `failed` with the reason rather than retried forever: a
        run that dies identically on every attempt is a defect to look at, and
        an endless loop would hide it. Committed batches keep their results.

        Conditional on the same observation as the reclaim, for the same
        reason: a worker that came back to life between the sweep and this
        write must keep its run rather than have it declared dead.
        """
        parked = await self.session.execute(
            update(RuleApplication)
            .where(RuleApplication.id == observed.application_id)
            .where(RuleApplication.tenant_id == self._tenant_id())
            .where(RuleApplication.status == ApplicationStatus.RUNNING)
            .where(RuleApplication.recovery_count == observed.recovery_count)
            .where(RuleApplication.heartbeat_at.is_not_distinct_from(observed.heartbeat_at))
            .where(RuleApplication.lease_token.is_not_distinct_from(observed.lease_token))
            .where(stale_running_predicate())
            .values(
                status=ApplicationStatus.FAILED,
                lease_token=None,
                claimed_by_task_id=None,
                failure_reason=(
                    f"Abandoned after {observed.recovery_count} recovery attempts. "
                    "Drafts already repriced in committed batches were kept."
                ),
                finished_at=datetime.now(UTC),
            )
            .returning(RuleApplication.id)
            .execution_options(synchronize_session=False)
        )
        if parked.scalars().first() is None:
            return False
        # Counts are recomputed after the status write so the parked run still
        # reports what its committed batches actually did.
        application = await self.get(observed.application_id)
        await self.session.refresh(application)
        await self._refresh_counts(application)
        await self.flush()
        return True

    def _request_from(self, application: RuleApplication) -> ApplyRequest:
        """Rebuild the confirmed request from the durable row.

        The worker never receives the selection in its payload -- only an id.
        This is where "what was confirmed" comes back, which is why
        ``selection`` is stored rather than referenced.
        """
        selection = application.selection or {}
        return ApplyRequest(
            product_ids=tuple(uuid.UUID(p) for p in selection.get("productIds", [])),
            idempotency_key=application.idempotency_key,
            expected_rule_id=application.pricing_rule_id,
            expected_rule_version=application.pricing_rule_version,
            variant_ids=tuple(uuid.UUID(v) for v in selection.get("variantIds", [])),
            selection_filter=(
                DraftSelectionFilter.from_json(application.selection_filter)
                if application.selection_filter is not None
                else None
            ),
        )

    async def _refresh_counts(self, application: RuleApplication) -> None:
        """Recompute the counts from the recorded items.

        Derived rather than incremented, because a run spans transactions and
        may resume: an in-memory tally would restart at zero on the second
        attempt and under-report everything the first one did.
        """
        await self.flush()
        item = RuleApplicationItem

        async def count(predicate: ColumnElement[bool]) -> int:
            query = (
                select(func.count())
                .select_from(item)
                .where(item.application_id == application.id)
                .where(predicate)
            )
            return int((await self.session.execute(query)).scalar_one())

        # Product-level rows only for applied/skipped: a variant row is part
        # of its product's outcome, not a second product.
        application.applied_count = await count(
            (item.outcome == ApplicationItemOutcome.APPLIED) & item.variant_id.is_(None)
        )
        application.skipped_count = await count(
            item.outcome.in_(
                (
                    ApplicationItemOutcome.SKIPPED,
                    ApplicationItemOutcome.PUBLISHED,
                    ApplicationItemOutcome.STALE,
                )
            )
            & item.variant_id.is_(None)
        )
        # Review counts variants too -- a held variant is a thing the merchant
        # has to look at, whether or not its product priced.
        application.review_count = await count(item.outcome == ApplicationItemOutcome.NEEDS_REVIEW)
        application.failed_count = await count(item.outcome == ApplicationItemOutcome.FAILED)

    async def _process(
        self,
        application: RuleApplication,
        request: ApplyRequest,
        product_ids: tuple[uuid.UUID, ...],
    ) -> None:
        for product_id in product_ids:
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
    "APPLICATION_BATCH_SIZE",
    "MAX_APPLICATION_PRODUCTS",
    "MAX_PREVIEW_PAGE",
    "MAX_RECOVERIES",
    "STALE_AFTER",
    "ApplyRequest",
    "ClaimResult",
    "DraftPricingService",
    "DraftSelectionFilter",
    "ImpactPreviewService",
    "PreviewPage",
    "ProductOutcome",
    "RuleApplicationService",
    "VariantOutcome",
    "draft_query",
]
