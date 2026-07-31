"""Automation rules and runs."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, status

from app.api.deps import DbSession, RequireAdmin, RequireViewer
from app.schemas.automation import (
    AutomationRuleCreate,
    AutomationRuleRead,
    AutomationRuleUpdate,
    AutomationRunRead,
)
from app.schemas.common import ListQueryParams, Page, list_query_params
from app.services.automation_service import AutomationService

router = APIRouter(prefix="/automation", tags=["automation"])


@router.get("/rules", response_model=Page[AutomationRuleRead])
async def list_rules(
    session: DbSession,
    params: Annotated[ListQueryParams, Depends(list_query_params)],
    _authorized: RequireViewer,
) -> Page[AutomationRuleRead]:
    rules, total = await AutomationService(session).list_rules(params)
    return Page[AutomationRuleRead].build(
        items=[AutomationRuleRead.model_validate(r) for r in rules],
        page=params.page,
        size=params.size,
        total_items=total,
    )


@router.post("/rules", response_model=AutomationRuleRead, status_code=status.HTTP_201_CREATED)
async def create_rule(
    session: DbSession,
    _authorized: RequireAdmin,
    payload: AutomationRuleCreate,
) -> AutomationRuleRead:
    rule = await AutomationService(session).create_rule(payload)
    return AutomationRuleRead.model_validate(rule)


@router.patch("/rules/{rule_id}", response_model=AutomationRuleRead)
async def update_rule(
    session: DbSession,
    _authorized: RequireAdmin,
    rule_id: Annotated[uuid.UUID, Path()],
    payload: AutomationRuleUpdate,
) -> AutomationRuleRead:
    rule = await AutomationService(session).update_rule(rule_id, payload)
    return AutomationRuleRead.model_validate(rule)


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(
    session: DbSession,
    _authorized: RequireAdmin,
    rule_id: Annotated[uuid.UUID, Path()],
) -> None:
    await AutomationService(session).delete_rule(rule_id)


@router.post(
    "/rules/{rule_id}/run",
    response_model=AutomationRunRead,
    status_code=status.HTTP_201_CREATED,
)
async def run_rule(
    session: DbSession,
    _authorized: RequireAdmin,
    rule_id: Annotated[uuid.UUID, Path()],
) -> AutomationRunRead:
    run = await AutomationService(session).run_rule(rule_id, trigger="manual")
    return AutomationRunRead.model_validate(run)


@router.get("/runs", response_model=Page[AutomationRunRead])
async def list_runs(
    session: DbSession,
    params: Annotated[ListQueryParams, Depends(list_query_params)],
    _authorized: RequireViewer,
) -> Page[AutomationRunRead]:
    runs, total = await AutomationService(session).list_runs(params)
    return Page[AutomationRunRead].build(
        items=[AutomationRunRead.model_validate(r) for r in runs],
        page=params.page,
        size=params.size,
        total_items=total,
    )
