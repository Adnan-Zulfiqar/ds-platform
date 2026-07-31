"""Automation rule API schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import Field

from app.models.automation import AutomationAction, AutomationSchedule
from app.models.order import SyncRunStatus
from app.schemas.base import CamelCaseModel


class AutomationRuleCreate(CamelCaseModel):
    name: str = Field(min_length=1, max_length=128)
    action: AutomationAction
    schedule: AutomationSchedule = AutomationSchedule.MANUAL
    store_id: uuid.UUID | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True


class AutomationRuleUpdate(CamelCaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    schedule: AutomationSchedule | None = None
    store_id: uuid.UUID | None = None
    config: dict[str, Any] | None = None
    is_active: bool | None = None


class AutomationRuleRead(CamelCaseModel):
    id: uuid.UUID
    name: str
    action: AutomationAction
    schedule: AutomationSchedule
    store_id: uuid.UUID | None
    config: dict[str, Any]
    is_active: bool
    last_run_at: datetime | None
    next_run_at: datetime | None
    consecutive_failures: int
    created_at: datetime
    updated_at: datetime


class AutomationRunRead(CamelCaseModel):
    id: uuid.UUID
    rule_id: uuid.UUID
    status: SyncRunStatus
    trigger: str
    summary: str | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
