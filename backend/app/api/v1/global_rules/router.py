"""Global pricing and shipping rule management (M3A-2).

Mounted at ``/api/v1/global-rules`` alongside the existing ``/pricing``
router rather than replacing it: ``/pricing`` owns catalogue preview and
apply, this owns the tenant-level rules those operations read. One settings
API style, not two.

Handlers validate, delegate and return. Every response is an explicit schema,
never an ORM model, and cross-tenant access falls through to the repository's
404 rather than a 403 -- a 403 would confirm the id exists.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status

from app.api.deps import DbSession, RequireAdmin, RequireViewer
from app.models.pricing import GlobalRuleKind, PriceRounding
from app.schemas.common import ListQueryParams, Page, list_query_params
from app.schemas.global_rules import (
    PreviewRequest,
    PreviewResponse,
    PricingRuleCreateRequest,
    PricingRuleRead,
    PricingRuleUpdateRequest,
    RuleActivationRequest,
    RuleResolutionRead,
    RuleVersionRead,
    ShippingRuleCreateRequest,
    ShippingRuleRead,
    ShippingRuleUpdateRequest,
)
from app.services.global_rules import GlobalRuleService, PreviewInputs

router = APIRouter(prefix="/global-rules", tags=["global-rules"])

RuleId = Annotated[uuid.UUID, Path(description="Rule identifier")]


def _resolution_read(resolution: object) -> RuleResolutionRead:
    rule = getattr(resolution, "rule", None)
    return RuleResolutionRead(
        rule_id=getattr(rule, "id", None),
        rule_name=getattr(rule, "name", None),
        scope=getattr(resolution, "scope", None),
        version=getattr(resolution, "version", None),
        reason=getattr(resolution, "reason", ""),
        overridden_rule_ids=[r.id for r in getattr(resolution, "overridden", ())],
    )


# --------------------------------------------------------------- pricing
@router.get("/pricing", response_model=Page[PricingRuleRead], summary="List pricing rules")
async def list_pricing_rules(
    session: DbSession,
    params: Annotated[ListQueryParams, Depends(list_query_params)],
    _authorized: RequireViewer,
) -> Page[PricingRuleRead]:
    service = GlobalRuleService(session)
    rules, total = await service.pricing.list(params)
    return Page[PricingRuleRead].build(
        items=[PricingRuleRead.model_validate(r, from_attributes=True) for r in rules],
        page=params.page,
        size=params.size,
        total_items=total,
    )


@router.get("/pricing/{rule_id}", response_model=PricingRuleRead, summary="Get a pricing rule")
async def get_pricing_rule(
    session: DbSession, rule_id: RuleId, _authorized: RequireViewer
) -> PricingRuleRead:
    rule = await GlobalRuleService(session).get_pricing_rule(rule_id)
    return PricingRuleRead.model_validate(rule, from_attributes=True)


@router.post(
    "/pricing",
    response_model=PricingRuleRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a pricing rule",
)
async def create_pricing_rule(
    session: DbSession, payload: PricingRuleCreateRequest, principal: RequireAdmin
) -> PricingRuleRead:
    values = payload.model_dump(exclude={"note"})
    rule = await GlobalRuleService(session).create_pricing_rule(
        values, actor_id=principal.user_id, note=payload.note
    )
    return PricingRuleRead.model_validate(rule, from_attributes=True)


@router.patch("/pricing/{rule_id}", response_model=PricingRuleRead, summary="Update a pricing rule")
async def update_pricing_rule(
    session: DbSession,
    rule_id: RuleId,
    payload: PricingRuleUpdateRequest,
    principal: RequireAdmin,
) -> PricingRuleRead:
    changes = payload.model_dump(exclude={"note", "expected_updated_at"}, exclude_unset=True)
    rule = await GlobalRuleService(session).update_pricing_rule(
        rule_id,
        changes,
        expected_updated_at=payload.expected_updated_at,
        actor_id=principal.user_id,
        note=payload.note,
    )
    return PricingRuleRead.model_validate(rule, from_attributes=True)


@router.post(
    "/pricing/{rule_id}/activation",
    response_model=PricingRuleRead,
    summary="Activate or deactivate a pricing rule",
)
async def set_pricing_activation(
    session: DbSession,
    rule_id: RuleId,
    payload: RuleActivationRequest,
    principal: RequireAdmin,
) -> PricingRuleRead:
    """One endpoint for both directions, and idempotent: re-sending the state
    a rule is already in writes nothing and adds no history entry."""
    rule = await GlobalRuleService(session).set_pricing_active(
        rule_id,
        active=payload.is_active,
        expected_updated_at=payload.expected_updated_at,
        actor_id=principal.user_id,
        note=payload.note,
    )
    return PricingRuleRead.model_validate(rule, from_attributes=True)


# -------------------------------------------------------------- shipping
@router.get("/shipping", response_model=Page[ShippingRuleRead], summary="List shipping rules")
async def list_shipping_rules(
    session: DbSession,
    params: Annotated[ListQueryParams, Depends(list_query_params)],
    _authorized: RequireViewer,
) -> Page[ShippingRuleRead]:
    service = GlobalRuleService(session)
    rules, total = await service.shipping.list(params)
    return Page[ShippingRuleRead].build(
        items=[ShippingRuleRead.model_validate(r, from_attributes=True) for r in rules],
        page=params.page,
        size=params.size,
        total_items=total,
    )


@router.get("/shipping/{rule_id}", response_model=ShippingRuleRead, summary="Get a shipping rule")
async def get_shipping_rule(
    session: DbSession, rule_id: RuleId, _authorized: RequireViewer
) -> ShippingRuleRead:
    rule = await GlobalRuleService(session).get_shipping_rule(rule_id)
    return ShippingRuleRead.model_validate(rule, from_attributes=True)


@router.post(
    "/shipping",
    response_model=ShippingRuleRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a shipping rule",
)
async def create_shipping_rule(
    session: DbSession, payload: ShippingRuleCreateRequest, principal: RequireAdmin
) -> ShippingRuleRead:
    values = payload.model_dump(exclude={"note"})
    rule = await GlobalRuleService(session).create_shipping_rule(
        values, actor_id=principal.user_id, note=payload.note
    )
    return ShippingRuleRead.model_validate(rule, from_attributes=True)


@router.patch(
    "/shipping/{rule_id}", response_model=ShippingRuleRead, summary="Update a shipping rule"
)
async def update_shipping_rule(
    session: DbSession,
    rule_id: RuleId,
    payload: ShippingRuleUpdateRequest,
    principal: RequireAdmin,
) -> ShippingRuleRead:
    changes = payload.model_dump(exclude={"note", "expected_updated_at"}, exclude_unset=True)
    rule = await GlobalRuleService(session).update_shipping_rule(
        rule_id,
        changes,
        expected_updated_at=payload.expected_updated_at,
        actor_id=principal.user_id,
        note=payload.note,
    )
    return ShippingRuleRead.model_validate(rule, from_attributes=True)


@router.post(
    "/shipping/{rule_id}/activation",
    response_model=ShippingRuleRead,
    summary="Activate or deactivate a shipping rule",
)
async def set_shipping_activation(
    session: DbSession,
    rule_id: RuleId,
    payload: RuleActivationRequest,
    principal: RequireAdmin,
) -> ShippingRuleRead:
    rule = await GlobalRuleService(session).set_shipping_active(
        rule_id,
        active=payload.is_active,
        expected_updated_at=payload.expected_updated_at,
        actor_id=principal.user_id,
        note=payload.note,
    )
    return ShippingRuleRead.model_validate(rule, from_attributes=True)


# ---------------------------------------------------------------- shared
@router.get(
    "/{rule_kind}/{rule_id}/history",
    response_model=list[RuleVersionRead],
    summary="Read a rule's version history",
)
async def rule_history(
    session: DbSession,
    rule_kind: Annotated[GlobalRuleKind, Path()],
    rule_id: RuleId,
    _authorized: RequireViewer,
) -> list[RuleVersionRead]:
    """Answers for deactivated and soft-deleted rules too -- "what happened
    to the rule that is no longer here" is exactly when history is read."""
    versions = await GlobalRuleService(session).history(rule_kind=rule_kind, rule_id=rule_id)
    return [RuleVersionRead.model_validate(v, from_attributes=True) for v in versions]


@router.get(
    "/resolve", response_model=RuleResolutionRead, summary="Resolve the effective pricing rule"
)
async def resolve_effective_rule(
    session: DbSession,
    _authorized: RequireViewer,
    # Explicit camelCase aliases. The alias generator on `CamelCaseModel`
    # covers request *bodies*; query parameters are plain function arguments
    # and get no such treatment, so without these the API would speak
    # camelCase everywhere except here -- and a caller sending `categoryId`
    # would silently resolve against no category at all.
    product_id: Annotated[uuid.UUID | None, Query(alias="productId")] = None,
    variant_id: Annotated[uuid.UUID | None, Query(alias="variantId")] = None,
    store_id: Annotated[uuid.UUID | None, Query(alias="storeId")] = None,
    category_id: Annotated[str | None, Query(alias="categoryId", max_length=64)] = None,
) -> RuleResolutionRead:
    resolution = await GlobalRuleService(session).resolve_pricing(
        product_id=product_id,
        variant_id=variant_id,
        store_id=store_id,
        category_id=category_id,
    )
    return _resolution_read(resolution)


@router.post("/preview", response_model=PreviewResponse, summary="Calculate a live preview")
async def preview(
    session: DbSession, payload: PreviewRequest, _authorized: RequireViewer
) -> PreviewResponse:
    """Read-only. Prices a transient product that is never added to the
    session, so no product, rule or history row can be written by this call.

    Available to viewers: seeing what a rule *would* do changes nothing.
    """
    result = await GlobalRuleService(session).preview(
        PreviewInputs(
            item_cost=payload.item_cost,
            shipping_cost=payload.shipping_cost,
            currency=payload.currency,
        ),
        product_id=payload.product_id,
        variant_id=payload.variant_id,
        store_id=payload.store_id,
        category_id=payload.category_id,
        rule_id=payload.rule_id,
    )
    landed = result.calculation.landed
    rule = result.resolution.rule
    return PreviewResponse(
        resolution=_resolution_read(result.resolution),
        item_cost=landed.item_cost,
        shipping_cost=landed.shipping_cost,
        fees=landed.fees,
        landed_cost=landed.amount,
        profit_basis=landed.profit_basis,
        separate_shipping_charge=landed.separate_shipping_charge,
        proposed_price=result.calculation.price,
        compare_at_price=result.calculation.compare_at,
        profit=result.calculation.profit,
        markup_percent=result.calculation.markup_percent,
        margin_percent=result.calculation.margin_percent,
        rounding=rule.rounding if rule is not None else PriceRounding.NONE,
        needs_review=result.calculation.needs_review,
        review_reasons=list(result.calculation.review_reasons),
    )
