"""In-app notification endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from app.api.deps import DbSession, RequireViewer
from app.schemas.common import ListQueryParams, Page, list_query_params
from app.schemas.notification import (
    NotificationEmailPreferencesRead,
    NotificationEmailPreferencesUpdate,
    NotificationRead,
    NotificationUnreadCount,
)
from app.services.notification_email import EMAILABLE_KINDS, NotificationEmailService
from app.services.notification_service import NotificationService

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=Page[NotificationRead])
async def list_notifications(
    session: DbSession,
    params: Annotated[ListQueryParams, Depends(list_query_params)],
    principal: RequireViewer,
    unread_only: Annotated[bool, Query(alias="unreadOnly")] = False,
) -> Page[NotificationRead]:
    _ = principal
    rows, total = await NotificationService(session).list(params, unread_only=unread_only)
    return Page[NotificationRead].build(
        items=[NotificationRead.model_validate(r) for r in rows],
        page=params.page,
        size=params.size,
        total_items=total,
    )


@router.get("/email-preferences", response_model=NotificationEmailPreferencesRead)
async def get_email_preferences(
    session: DbSession, principal: RequireViewer
) -> NotificationEmailPreferencesRead:
    """Track E3: the signed-in user's own email choices (failures by default)."""
    kinds = await NotificationEmailService(session).preferences_for(principal.user_id)
    return NotificationEmailPreferencesRead(kinds=kinds, available=list(EMAILABLE_KINDS))


@router.put("/email-preferences", response_model=NotificationEmailPreferencesRead)
async def set_email_preferences(
    payload: NotificationEmailPreferencesUpdate, session: DbSession, principal: RequireViewer
) -> NotificationEmailPreferencesRead:
    """Each user sets only their own; any role may, because it is their inbox."""
    kinds = await NotificationEmailService(session).set_preferences(
        principal.user_id, payload.kinds
    )
    return NotificationEmailPreferencesRead(kinds=kinds, available=list(EMAILABLE_KINDS))


@router.get("/unread-count", response_model=NotificationUnreadCount)
async def unread_count(
    session: DbSession,
    principal: RequireViewer,
) -> NotificationUnreadCount:
    count = await NotificationService(session).unread_count(user_id=principal.user_id)
    return NotificationUnreadCount(unread=count)


@router.post("/{notification_id}/read", response_model=NotificationRead)
async def mark_read(
    session: DbSession,
    principal: RequireViewer,
    notification_id: Annotated[uuid.UUID, Path()],
) -> NotificationRead:
    _ = principal
    row = await NotificationService(session).mark_read(notification_id)
    return NotificationRead.model_validate(row)


@router.post("/read-all", response_model=NotificationUnreadCount)
async def mark_all_read(
    session: DbSession,
    principal: RequireViewer,
) -> NotificationUnreadCount:
    await NotificationService(session).mark_all_read(user_id=principal.user_id)
    return NotificationUnreadCount(unread=0)
