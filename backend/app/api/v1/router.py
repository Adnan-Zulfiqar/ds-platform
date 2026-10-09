"""API v1 aggregate router.

Every v1 domain router is mounted here, and this router is mounted once in
``app.main`` under the configured prefix. Adding a domain is a single line in
this file — no change to application startup.

**Versioning.** The version lives in the URL path rather than in a header
because a path is visible in logs, in a browser address bar and in a curl
command, which makes support and debugging far easier. When v2 is needed, add
``app/api/v2/`` alongside this package and mount both: v1 keeps working
unchanged for existing integrations while v2 evolves. That is the whole reason
for the ``v1`` directory existing before there is any v2 — retrofitting a
version prefix onto live client integrations is a breaking change.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import MaintenanceGuard
from app.api.v1 import (
    ai,
    analytics,
    auth,
    automation,
    billing,
    drafts,
    global_rules,
    integrations,
    inventory,
    notifications,
    orders,
    platform,
    pricing,
    products,
    stores,
    system,
    users,
)

api_router = APIRouter()

# Domain routers. Each declares its own prefix and OpenAPI tag, so this file
# stays a manifest rather than a place where routing rules accumulate.
# MaintenanceGuard (D-019) makes the merchant routers read-only while the
# platform is in maintenance; auth, the platform console and the public
# system status stay outside it.
api_router.include_router(auth.router)
api_router.include_router(users.router, dependencies=[MaintenanceGuard])
api_router.include_router(integrations.router, dependencies=[MaintenanceGuard])
api_router.include_router(products.router, dependencies=[MaintenanceGuard])
api_router.include_router(drafts.router, dependencies=[MaintenanceGuard])
api_router.include_router(ai.router, dependencies=[MaintenanceGuard])
api_router.include_router(stores.router, dependencies=[MaintenanceGuard])
api_router.include_router(orders.router, dependencies=[MaintenanceGuard])
api_router.include_router(inventory.router, dependencies=[MaintenanceGuard])
api_router.include_router(global_rules.router, dependencies=[MaintenanceGuard])
# Preview/apply share the `/global-rules` prefix; a second module keeps
# rule CRUD and the bulk workflow separately reviewable.
api_router.include_router(global_rules.applications_router, dependencies=[MaintenanceGuard])
api_router.include_router(global_rules.targets_router, dependencies=[MaintenanceGuard])
api_router.include_router(pricing.router, dependencies=[MaintenanceGuard])
api_router.include_router(automation.router, dependencies=[MaintenanceGuard])
api_router.include_router(notifications.router, dependencies=[MaintenanceGuard])
api_router.include_router(analytics.router, dependencies=[MaintenanceGuard])
api_router.include_router(platform.router)
api_router.include_router(billing.router, dependencies=[MaintenanceGuard])
api_router.include_router(system.router)

__all__ = ["api_router"]
