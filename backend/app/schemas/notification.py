"""Notification API schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import Field

from app.models.notification import NotificationKind
from app.schemas.base import CamelCaseModel


class NotificationRead(CamelCaseModel):
    id: uuid.UUID
    user_id: uuid.UUID | None
    kind: NotificationKind
    title: str
    body: str
    href: str | None
    payload: dict[str, Any]
    is_read: bool
    read_at: datetime | None
    created_at: datetime


class NotificationUnreadCount(CamelCaseModel):
    unread: int


class NotificationEmailPreferencesRead(CamelCaseModel):
    """Track E3: the kinds this user gets by email, and every kind they could."""

    kinds: list[str]
    available: list[str]


class NotificationEmailPreferencesUpdate(CamelCaseModel):
    kinds: list[str] = Field(default_factory=list, max_length=32)
