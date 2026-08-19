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
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Path, Query, status

from app.api.deps import DbSession, RequireAdmin, RequireViewer
from app.models.rule_application import ApplicationItemOutcome, ApplicationStatus
from app.schemas.base import CamelCaseModel
from app.services.rule_application import (
    MAX_PREVIEW_PAGE,
    ApplyRequest,
    ImpactPreviewService,
    ProductOutcome,
    RuleApplicationService,
)

router = APIRouter(prefix="/global-rules", tags=["global-rules"])


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


class ApplyRequestBody(CamelCaseModel):
    product_ids: list[uuid.UUID]
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
    total_count: int
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
    product_ids: Annotated[list[uuid.UUID] | None, Query(alias="productIds")] = None,
) -> PreviewPageRead:
    """Read-only, paginated, and open to viewers.

    Writes nothing: the service that backs this has no write path at all, and
    the page is sliced in the database rather than in memory so a large
    catalogue cannot be loaded to answer one page.
    """
    result = await ImpactPreviewService(session).preview(
        product_ids=product_ids,
        search=search,
        needs_review_only=needs_review_only,
        page=page,
        size=size,
    )
    items = [_to_item(outcome) for outcome in result.items]
    return PreviewPageRead(
        items=items,
        total=result.total,
        page=result.page,
        size=result.size,
        applicable_count=sum(1 for i in items if i.can_apply),
        review_count=sum(1 for i in items if i.needs_review),
        published_count=sum(1 for i in items if i.published),
    )


@router.post(
    "/drafts/apply",
    response_model=ApplicationRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Apply the active rules to selected drafts",
)
async def apply_to_drafts(
    session: DbSession, payload: ApplyRequestBody, principal: RequireAdmin
) -> ApplicationRead:
    """Explicit confirmation only.

    There is no path from saving a rule to this endpoint; a settings save
    changes no product. Re-sending the same idempotency key returns the
    original run rather than repricing twice; sending it with a different
    payload is refused as a conflict.
    """
    application = await RuleApplicationService(session).start(
        ApplyRequest(
            product_ids=tuple(payload.product_ids),
            idempotency_key=payload.idempotency_key,
            expected_rule_id=payload.expected_rule_id,
            expected_rule_version=payload.expected_rule_version,
        ),
        actor_id=principal.user_id,
    )
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
    summary="Cancel an application that has not written anything",
)
async def cancel_application(
    session: DbSession,
    application_id: Annotated[uuid.UUID, Path()],
    _principal: RequireAdmin,
) -> ApplicationRead:
    """Refused once prices have landed: the writes are real, and pretending
    they can be taken back would misrepresent the catalogue."""
    application = await RuleApplicationService(session).cancel(application_id)
    return ApplicationRead.model_validate(application, from_attributes=True)
