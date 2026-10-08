"""Response schemas for the operator console's data views (D-019).

Separate from ``platform_admin`` (operator identity) because these describe
workspace data. Every model lists its fields explicitly: an encrypted
credential, a password hash or a token hash has no field here, so it cannot
reach a response even by accident.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from app.schemas.base import CamelCaseModel


class DailyCountRead(CamelCaseModel):
    day: date
    count: int


class SystemHealthRead(CamelCaseModel):
    database: bool
    redis: bool
    migration_revision: str | None


class PlatformDashboardRead(CamelCaseModel):
    generated_at: datetime
    tenants_by_status: dict[str, int]
    tenants_new_7d: int
    tenants_new_30d: int
    users_active: int
    users_new_7d: int
    stores_by_status: dict[str, int]
    stores_by_platform: dict[str, int]
    products_total: int
    listings_by_status: dict[str, int]
    orders_24h: int
    orders_7d: int
    subscriptions_by_plan: dict[str, int]
    subscriptions_by_status: dict[str, int]
    trials_ending_7d: int
    failed_24h: dict[str, int]
    stuck: dict[str, int]
    operator_sessions_open: int
    security_failures_24h: int
    signups_30d: list[DailyCountRead]
    orders_14d: list[DailyCountRead]
    system: SystemHealthRead


class SubscriptionSummaryRead(CamelCaseModel):
    plan: str | None
    status: str
    ai_addon: bool
    trial_ends_at: datetime
    current_period_end: datetime | None
    cancel_at_period_end: bool
    has_stripe_customer: bool


class WorkspaceHealthRead(CamelCaseModel):
    window_hours: int
    failed_order_syncs: int
    failed_inventory_syncs: int
    listings_in_error: int
    failed_notification_emails: int


class WorkspaceOverviewRead(CamelCaseModel):
    id: uuid.UUID
    name: str
    slug: str
    status: str
    is_active: bool
    timezone: str | None
    default_currency: str | None
    created_at: datetime
    users: int
    active_users: int
    stores_by_status: dict[str, int]
    products: dict[str, int]
    orders_by_status: dict[str, int]
    listings_by_status: dict[str, int]
    subscription: SubscriptionSummaryRead | None
    health: WorkspaceHealthRead


__all__ = [
    "DailyCountRead",
    "PlatformDashboardRead",
    "SubscriptionSummaryRead",
    "SystemHealthRead",
    "WorkspaceHealthRead",
    "WorkspaceOverviewRead",
]
