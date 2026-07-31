"""Store connection endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, status

from app.api.deps import DbSession, RequireAdmin, RequireViewer
from app.schemas.common import ListQueryParams, Page, list_query_params
from app.schemas.store import (
    StoreCreate,
    StoreRead,
    StoreStatisticsRead,
    StoreUpdate,
)
from app.services.store_service import StoreService

router = APIRouter(prefix="/stores", tags=["stores"])


@router.get("", response_model=Page[StoreRead])
async def list_stores(
    session: DbSession,
    params: Annotated[ListQueryParams, Depends(list_query_params)],
    _authorized: RequireViewer,
) -> Page[StoreRead]:
    stores, total = await StoreService(session).list(params)
    return Page[StoreRead].build(
        items=[StoreRead.model_validate(s) for s in stores],
        page=params.page,
        size=params.size,
        total_items=total,
    )


@router.get("/statistics", response_model=StoreStatisticsRead)
async def store_statistics(session: DbSession, _authorized: RequireViewer) -> StoreStatisticsRead:
    return await StoreService(session).statistics()


@router.post("", response_model=StoreRead, status_code=status.HTTP_201_CREATED)
async def create_store(
    session: DbSession,
    principal: RequireAdmin,
    payload: StoreCreate,
) -> StoreRead:
    store = await StoreService(session).create(payload, connected_by_user_id=principal.user_id)
    return StoreRead.model_validate(store)


@router.get("/{store_id}", response_model=StoreRead)
async def get_store(
    session: DbSession,
    _authorized: RequireViewer,
    store_id: Annotated[uuid.UUID, Path()],
) -> StoreRead:
    store = await StoreService(session).get(store_id)
    return StoreRead.model_validate(store)


@router.patch("/{store_id}", response_model=StoreRead)
async def update_store(
    session: DbSession,
    _authorized: RequireAdmin,
    store_id: Annotated[uuid.UUID, Path()],
    payload: StoreUpdate,
) -> StoreRead:
    store = await StoreService(session).update(store_id, payload)
    return StoreRead.model_validate(store)


@router.delete("/{store_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_store(
    session: DbSession,
    _authorized: RequireAdmin,
    store_id: Annotated[uuid.UUID, Path()],
) -> None:
    await StoreService(session).delete(store_id)


@router.get("/{store_id}/health")
async def store_health(
    session: DbSession,
    _authorized: RequireViewer,
    store_id: Annotated[uuid.UUID, Path()],
) -> dict[str, Any]:
    return await StoreService(session).health(store_id)
