"""Pricing rules and apply/preview endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, status

from app.api.deps import DbSession, RequireAdmin, RequireViewer
from app.schemas.common import ListQueryParams, Page, list_query_params
from app.schemas.pricing import (
    PriceChangeRead,
    PricePreviewRequest,
    PricePreviewResponse,
    PricingApplyRequest,
    PricingRuleCreate,
    PricingRuleRead,
    PricingRuleUpdate,
)
from app.services.pricing_engine import PricingEngine
from app.tasks.integrations.ebay import push_price_quantity_after_commit

router = APIRouter(prefix="/pricing", tags=["pricing"])


@router.get("/rules", response_model=Page[PricingRuleRead])
async def list_rules(
    session: DbSession,
    params: Annotated[ListQueryParams, Depends(list_query_params)],
    _authorized: RequireViewer,
) -> Page[PricingRuleRead]:
    rules, total = await PricingEngine(session).list_rules(params)
    return Page[PricingRuleRead].build(
        items=[PricingRuleRead.model_validate(r) for r in rules],
        page=params.page,
        size=params.size,
        total_items=total,
    )


@router.post("/rules", response_model=PricingRuleRead, status_code=status.HTTP_201_CREATED)
async def create_rule(
    session: DbSession,
    _authorized: RequireAdmin,
    payload: PricingRuleCreate,
) -> PricingRuleRead:
    rule = await PricingEngine(session).create_rule(payload)
    return PricingRuleRead.model_validate(rule)


@router.patch("/rules/{rule_id}", response_model=PricingRuleRead)
async def update_rule(
    session: DbSession,
    _authorized: RequireAdmin,
    rule_id: Annotated[uuid.UUID, Path()],
    payload: PricingRuleUpdate,
) -> PricingRuleRead:
    rule = await PricingEngine(session).update_rule(rule_id, payload)
    return PricingRuleRead.model_validate(rule)


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(
    session: DbSession,
    _authorized: RequireAdmin,
    rule_id: Annotated[uuid.UUID, Path()],
) -> None:
    await PricingEngine(session).delete_rule(rule_id)


@router.post("/preview", response_model=PricePreviewResponse)
async def preview_prices(
    session: DbSession,
    _authorized: RequireViewer,
    payload: PricePreviewRequest,
) -> PricePreviewResponse:
    return await PricingEngine(session).preview(
        product_ids=payload.product_ids,
        store_id=payload.store_id,
        limit=payload.limit,
    )


@router.post("/apply", response_model=list[PriceChangeRead])
async def apply_prices(
    session: DbSession,
    principal: RequireAdmin,
    payload: PricingApplyRequest,
) -> list[PriceChangeRead]:
    changes = await PricingEngine(session).apply(payload, applied_by_user_id=principal.user_id)
    # EBAY-C4: a new price must reach the product's eBay listings.
    push_price_quantity_after_commit(session, [c.product_id for c in changes])
    return [PriceChangeRead.model_validate(c) for c in changes]


@router.get("/changes", response_model=Page[PriceChangeRead])
async def list_price_changes(
    session: DbSession,
    params: Annotated[ListQueryParams, Depends(list_query_params)],
    _authorized: RequireViewer,
) -> Page[PriceChangeRead]:
    rows, total = await PricingEngine(session).changes.list(params)
    return Page[PriceChangeRead].build(
        items=[PriceChangeRead.model_validate(r) for r in rows],
        page=params.page,
        size=params.size,
        total_items=total,
    )
