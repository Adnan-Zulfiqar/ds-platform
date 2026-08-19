"""Draft impact preview and confirmed rule application (M3A-3).

Extends the existing ``/api/v1/global-rules`` router rather than adding a
second settings API. Preview is read-only and open to viewers; starting or
cancelling an application is an admin action.

Nothing here reprices a published product. That is enforced in the service --
a published id submitted to the application endpoint is recorded with a
`published` outcome and skipped, not silently accepted.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status
from pydantic import Field
from sqlalchemy import event

from app.api.deps import DbSession, RequireAdmin, RequireViewer, endpoint_rate_limit
from app.models.rule_application import ApplicationItemOutcome, ApplicationStatus
from app.schemas.base import CamelCaseModel
from app.services.rule_application import (
    APPLICATION_BATCH_SIZE,
    MAX_APPLICATION_PRODUCTS,
    MAX_PREVIEW_PAGE,
    ApplyRequest,
    DraftSelectionFilter,
    ImpactPreviewService,
    ProductOutcome,
    RuleApplicationService,
)

# Sideways, not downwards: ``app.tasks`` is a peer entry point into the domain,
# the same way this package is, and it does not import ``app.api``. The queue
# hand-off lives there rather than here so that this handler keeps to
# validate-delegate-return and knows nothing about brokers.
from app.tasks.pricing import publish_rule_application

# Tighter quotas than the broad middleware allowance, through the same
# limiter. Both endpoints price a catalogue page per call, and the apply
# endpoint additionally starts background work; the global quota is sized for
# ordinary browsing and is far too generous for either.
#
# The preview number is deliberately roomy: the calculator debounces at 400ms,
# so a merchant typing continuously for a minute produces well under this. It
# is a ceiling on abuse, not on use.
_preview_limit = endpoint_rate_limit("global-rules-preview", limit=120, window_seconds=60)
_impact_limit = endpoint_rate_limit("global-rules-impact", limit=120, window_seconds=60)
_apply_limit = endpoint_rate_limit("global-rules-apply", limit=20, window_seconds=60)

router = APIRouter(prefix="/global-rules", tags=["global-rules"])


def _publish_after_commit(session: DbSession, application_id: uuid.UUID) -> None:
    """Publish once this request's transaction is durable, and not before.

    ``once=True`` so a session reused across requests cannot publish the same
    application again, and the listener is attached to the *sync* session
    because that is where SQLAlchemy emits the event.
    """

    def on_commit(_session: object) -> None:
        publish_rule_application(application_id)

    event.listen(session.sync_session, "after_commit", on_commit, once=True)


class PreviewVariantRead(CamelCaseModel):
    variant_id: uuid.UUID
    label: str | None
    current_price: Decimal | None
    proposed_price: Decimal | None
    landed_cost: Decimal
    profit: Decimal | None
    markup_percent: Decimal | None
    margin_percent: Decimal | None
    needs_review: bool
    review_reasons: list[str]


class PreviewItemRead(CamelCaseModel):
    """One draft's impact, with everything needed to judge it."""

    product_id: uuid.UUID
    title: str
    currency: str | None
    current_price: Decimal | None
    item_cost: Decimal
    supplier_shipping_cost: Decimal
    fees: Decimal
    landed_cost: Decimal
    proposed_price: Decimal | None
    compare_at_price: Decimal | None
    profit: Decimal | None
    markup_percent: Decimal | None
    margin_percent: Decimal | None
    pricing_rule_id: uuid.UUID | None
    pricing_rule_version: int | None
    pricing_rule_scope: str | None
    rule_reason: str
    shipping_rule_id: uuid.UUID | None
    shipping_rule_version: int | None
    shipping_explanation: str | None
    published: bool
    #: False when the item cannot be written -- published, or held for review.
    can_apply: bool
    needs_review: bool
    review_reasons: list[str]
    variants: list[PreviewVariantRead]


class PreviewPageRead(CamelCaseModel):
    items: list[PreviewItemRead]
    total: int
    page: int
    size: int
    applicable_count: int
    review_count: int
    published_count: int
    #: How many drafts "select all matching" would cover, capped at the
    #: application ceiling. Counted server-side: the browser holds one page.
    selectable_total: int = 0
    #: The uncapped match count, so the screen can say when a selection was
    #: truncated instead of silently applying to fewer than the merchant sees.
    matching_total: int = 0
    max_application_products: int = MAX_APPLICATION_PRODUCTS
    #: Products per worker transaction. Exposed because it is the resolution
    #: at which progress advances and at which a cancellation takes effect.
    application_batch_size: int = APPLICATION_BATCH_SIZE


class SelectionFilterBody(CamelCaseModel):
    """“Everything matching what I am looking at”, sent instead of a list.

    The browser never enumerates 5,000 identifiers to select them: it sends
    the filter it is displaying and the server expands it once, at
    confirmation, into the durable snapshot the worker walks.
    """

    search: str | None = None
    needs_review_only: bool = False
    #: Excludes published drafts and those already flagged for review.
    safe_only: bool = True


class ApplyRequestBody(CamelCaseModel):
    product_ids: list[uuid.UUID] = Field(default_factory=list)
    #: Supplied instead of `productIds` for a select-all-matching confirmation.
    selection_filter: SelectionFilterBody | None = None
    idempotency_key: str
    #: The rule version the merchant saw in the preview. Revalidated before
    #: any write; a rule edited in between invalidates those items rather
    #: than repricing against arithmetic nobody reviewed.
    expected_rule_id: uuid.UUID | None = None
    expected_rule_version: int | None = None


class ApplicationItemRead(CamelCaseModel):
    #: Null when the submitted id resolved to nothing in this tenant -- the
    #: result still appears, with the id in `message`.
    product_id: uuid.UUID | None
    variant_id: uuid.UUID | None
    outcome: ApplicationItemOutcome
    previous_price: Decimal | None
    new_price: Decimal | None
    landed_cost: Decimal | None
    applied_rule_version: int | None
    review_reasons: list[str]
    message: str | None


class ApplicationRead(CamelCaseModel):
    id: uuid.UUID
    status: ApplicationStatus
    idempotency_key: str
    #: Set once a worker has picked the run up, and refreshed at every batch
    #: boundary. A `running` row whose heartbeat has gone quiet was abandoned.
    heartbeat_at: datetime | None = None
    recovery_count: int = 0
    finished_at: datetime | None = None
    total_count: int
    #: How far through the selection the worker has committed. With
    #: `totalCount` this is the progress a polling client renders.
    processed_count: int
    applied_count: int
    skipped_count: int
    review_count: int
    failed_count: int
    failure_reason: str | None
    items: list[ApplicationItemRead]


def _to_item(outcome: ProductOutcome) -> PreviewItemRead:
    landed = outcome.calculation.landed
    rule = outcome.resolution.rule
    shipping_rule = outcome.shipping.rule if outcome.shipping else None
    return PreviewItemRead(
        product_id=outcome.product.id,
        title=outcome.product.title,
        currency=landed.currency,
        current_price=outcome.product.sell_price,
        item_cost=landed.item_cost,
        supplier_shipping_cost=landed.shipping_cost,
        fees=landed.fees,
        landed_cost=landed.amount,
        proposed_price=outcome.proposed_price,
        compare_at_price=outcome.calculation.compare_at,
        profit=outcome.calculation.profit,
        markup_percent=outcome.calculation.markup_percent,
        margin_percent=outcome.calculation.margin_percent,
        pricing_rule_id=rule.id if rule else None,
        pricing_rule_version=outcome.resolution.version,
        pricing_rule_scope=outcome.resolution.scope.value if outcome.resolution.scope else None,
        rule_reason=outcome.resolution.reason,
        shipping_rule_id=shipping_rule.id if shipping_rule else None,
        shipping_rule_version=shipping_rule.version if shipping_rule else None,
        shipping_explanation=outcome.shipping.explanation if outcome.shipping else None,
        published=outcome.published,
        can_apply=outcome.can_apply,
        needs_review=outcome.needs_review,
        review_reasons=list(outcome.review_reasons),
        variants=[
            PreviewVariantRead(
                variant_id=v.variant_id,
                label=v.label,
                current_price=v.current_price,
                proposed_price=v.calculation.price,
                landed_cost=v.calculation.landed.amount,
                profit=v.calculation.profit,
                markup_percent=v.calculation.markup_percent,
                margin_percent=v.calculation.margin_percent,
                needs_review=v.calculation.needs_review,
                review_reasons=list(v.review_reasons),
            )
            for v in outcome.variants
        ],
    )


@router.get(
    "/drafts/impact",
    response_model=PreviewPageRead,
    summary="Preview the impact of the active rules on existing drafts",
)
async def preview_draft_impact(
    session: DbSession,
    _authorized: RequireViewer,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=MAX_PREVIEW_PAGE)] = 25,
    search: Annotated[str | None, Query(max_length=200)] = None,
    needs_review_only: Annotated[bool, Query(alias="needsReviewOnly")] = False,
    safe_only: Annotated[bool, Query(alias="safeOnly")] = False,
    product_ids: Annotated[list[uuid.UUID] | None, Query(alias="productIds")] = None,
    _throttle: None = Depends(_impact_limit),
) -> PreviewPageRead:
    """Read-only, paginated, and open to viewers.

    Writes nothing: the service that backs this has no write path at all, and
    the page is sliced in the database rather than in memory so a large
    catalogue cannot be loaded to answer one page.
    """
    service = ImpactPreviewService(session)
    selection = DraftSelectionFilter(
        search=search,
        needs_review_only=needs_review_only,
        safe_only=safe_only,
        product_ids=tuple(product_ids or ()),
    )
    result = await service.preview(selection, page=page, size=size)
    items = [_to_item(outcome) for outcome in result.items]
    # What "select all matching" would actually cover, counted in the database
    # rather than inferred from the page in front of the merchant.
    selectable = await service.count_matching(
        DraftSelectionFilter(
            search=search,
            needs_review_only=needs_review_only,
            safe_only=True,
            product_ids=tuple(product_ids or ()),
        )
    )
    return PreviewPageRead(
        items=items,
        total=result.total,
        page=result.page,
        size=result.size,
        applicable_count=sum(1 for i in items if i.can_apply),
        review_count=sum(1 for i in items if i.needs_review),
        published_count=sum(1 for i in items if i.published),
        selectable_total=min(selectable, MAX_APPLICATION_PRODUCTS),
        matching_total=selectable,
    )


@router.post(
    "/drafts/apply",
    response_model=ApplicationRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Apply the active rules to selected drafts",
)
async def apply_to_drafts(
    session: DbSession,
    payload: ApplyRequestBody,
    principal: RequireAdmin,
    _throttle: None = Depends(_apply_limit),
) -> ApplicationRead:
    """Explicit confirmation only. Accepts the work; does not perform it.

    There is no path from saving a rule to this endpoint; a settings save
    changes no product. Re-sending the same idempotency key returns the
    original run rather than repricing twice; sending it with a different
    payload is refused as a conflict.

    Returns `202` with a `pending` application. Repricing a catalogue is not
    request-shaped work -- it is handed to the existing Celery queue and
    polled through `GET /global-rules/applications/{id}`. At most
    `maxApplicationProducts` drafts (published in the preview response) may be
    submitted in one application; more is refused with that number named,
    never silently truncated.

    The message is published from an ``after_commit`` hook, which is the only
    moment that means "the row is durable". A background task looked like the
    right place for it and was not: Starlette runs those inside the response
    call, still within the session's scope, so the worker looked up a row that
    had not been written and the whole request rolled back. If the publish
    itself fails, ``pricing.reconcile_applications`` republishes the run --
    the row and the message cannot be written atomically, so the gap is
    reconciled rather than wished away.
    """
    application = await RuleApplicationService(session).create(
        ApplyRequest(
            product_ids=tuple(payload.product_ids),
            idempotency_key=payload.idempotency_key,
            expected_rule_id=payload.expected_rule_id,
            expected_rule_version=payload.expected_rule_version,
            selection_filter=(
                DraftSelectionFilter(
                    search=payload.selection_filter.search,
                    needs_review_only=payload.selection_filter.needs_review_only,
                    safe_only=payload.selection_filter.safe_only,
                )
                if payload.selection_filter is not None
                else None
            ),
        ),
        actor_id=principal.user_id,
    )
    if application.status is ApplicationStatus.PENDING:
        _publish_after_commit(session, application.id)
    return ApplicationRead.model_validate(application, from_attributes=True)


@router.get(
    "/applications/{application_id}",
    response_model=ApplicationRead,
    summary="Get an application's status and results",
)
async def get_application(
    session: DbSession,
    application_id: Annotated[uuid.UUID, Path()],
    _authorized: RequireViewer,
) -> ApplicationRead:
    application = await RuleApplicationService(session).get(application_id)
    return ApplicationRead.model_validate(application, from_attributes=True)


@router.post(
    "/applications/{application_id}/cancel",
    response_model=ApplicationRead,
    summary="Cancel an application that has not finished",
)
async def cancel_application(
    session: DbSession,
    application_id: Annotated[uuid.UUID, Path()],
    _principal: RequireAdmin,
) -> ApplicationRead:
    """Stops a run that is `pending` or `running`; idempotent.

    A `pending` run is stopped outright -- the worker's claim will refuse the
    message when it arrives, so a task already in flight cannot revive it. A
    `running` run stops cooperatively at its next batch boundary, and drafts
    repriced by batches that already committed keep those prices; they are
    real writes, recorded item by item, and quietly reverting them would be a
    second unreviewed reprice. A run that has already finished is refused,
    because reporting it as cancelled would misrepresent the catalogue.
    """
    application = await RuleApplicationService(session).cancel(application_id)
    return ApplicationRead.model_validate(application, from_attributes=True)
