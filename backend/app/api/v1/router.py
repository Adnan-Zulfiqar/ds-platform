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

from app.api.v1 import (
    ai,
    analytics,
    auth,
    automation,
    drafts,
    global_rules,
    integrations,
    inventory,
    notifications,
    orders,
    pricing,
    products,
    stores,
    users,
)

api_router = APIRouter()

# Domain routers. Each declares its own prefix and OpenAPI tag, so this file
# stays a manifest rather than a place where routing rules accumulate.
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(integrations.router)
api_router.include_router(products.router)
api_router.include_router(drafts.router)
api_router.include_router(ai.router)
api_router.include_router(stores.router)
api_router.include_router(orders.router)
api_router.include_router(inventory.router)
api_router.include_router(global_rules.router)
api_router.include_router(pricing.router)
api_router.include_router(automation.router)
api_router.include_router(notifications.router)
api_router.include_router(analytics.router)

__all__ = ["api_router"]
