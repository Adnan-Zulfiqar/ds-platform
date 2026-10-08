"""One workspace, seen by a platform operator (Admin Control Center, D-019).

Every method here runs **inside the workspace's tenant context**, set by
``app.api.deps.platform_workspace`` after the operator's permission is
checked and the visit is audited. That is the whole design: the operator
reads through the same ``TenantScopedRepository`` classes the merchant's own
requests use, so the tenant predicate is applied exactly as it is for them,
and one operator request can never span two workspaces. No unscoped query
is added for workspace data.

Secrets never leave: responses are built from explicit schemas that do not
name encrypted columns, password hashes or token hashes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.core.context import require_tenant_id
from app.models.tenant import Tenant
from app.repositories.billing import TenantSubscriptionRepository
from app.repositories.order import OrderRepository
from app.repositories.platform_admin import PlatformTenantDirectory, TenantHealth
from app.repositories.product import ProductRepository
from app.repositories.shopify import StoreListingRepository
from app.repositories.store import StoreRepository
from app.repositories.user import UserRepository
from app.services.base import BaseService
from app.services.platform_admin import PlatformPrincipal


@dataclass(frozen=True, slots=True)
class SubscriptionSummary:
    plan: str | None
    status: str
    ai_addon: bool
    trial_ends_at: datetime
    current_period_end: datetime | None
    cancel_at_period_end: bool
    #: Whether a Stripe customer exists. The id itself stays in Stripe's
    #: dashboard; the console has no use for it.
    has_stripe_customer: bool


@dataclass(frozen=True, slots=True)
class WorkspaceOverview:
    tenant: Tenant
    users: int
    active_users: int
    stores_by_status: dict[str, int]
    products: dict[str, int]
    orders_by_status: dict[str, int]
    listings_by_status: dict[str, int]
    subscription: SubscriptionSummary | None
    health: TenantHealth


class PlatformWorkspaceService(BaseService):
    async def overview(self, tenant: Tenant) -> WorkspaceOverview:
        if require_tenant_id() != tenant.id:  # the dependency sets both; never trust drift
            raise RuntimeError("workspace context does not match the requested workspace")
        users = UserRepository(self.session)
        listings = StoreListingRepository(self.session)
        sub = await TenantSubscriptionRepository(self.session).current()
        return WorkspaceOverview(
            tenant=tenant,
            users=await users.count(),
            active_users=await users.count(filters={"is_active": True}),
            stores_by_status=await StoreRepository(self.session).count_by_status(),
            products=await ProductRepository(self.session).count_workspace(),
            orders_by_status=await OrderRepository(self.session).count_by_status(),
            listings_by_status={
                status: await listings.count(filters={"status": status})
                for status in ("pending", "synced", "error", "removed")
            },
            subscription=None
            if sub is None
            else SubscriptionSummary(
                plan=sub.plan,
                status=sub.status,
                ai_addon=sub.ai_addon,
                trial_ends_at=sub.trial_ends_at,
                current_period_end=sub.current_period_end,
                cancel_at_period_end=sub.cancel_at_period_end,
                has_stripe_customer=sub.stripe_customer_id is not None,
            ),
            health=await PlatformTenantDirectory(self.session).health(tenant.id),
        )


@dataclass(frozen=True, slots=True)
class PlatformWorkspace:
    """An operator inside one workspace, for the length of one request."""

    principal: PlatformPrincipal
    tenant: Tenant


__all__ = [
    "PlatformWorkspace",
    "PlatformWorkspaceService",
    "SubscriptionSummary",
    "WorkspaceOverview",
]
