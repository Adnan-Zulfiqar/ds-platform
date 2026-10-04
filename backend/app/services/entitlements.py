"""Plan limits at the points that spend them (Track E6b, D-016).

Three questions, asked where the work starts rather than in each handler:

* **Can this workspace change anything?** Asked before an import. No after
  the trial without a subscription; existing listings keep syncing, because
  nothing calls this on the sync path.
* **Is there room for this listing?** Asked before a publish creates a new
  listing. Re-publishing an existing listing adds nothing.
* **May this workspace use AI?** Asked before any provider call, in
  ``PromptService``, the one place every AI feature goes through.

**Off unless billing is configured.** Without ``STRIPE_SECRET_KEY`` there is
no way to subscribe, so there is nothing to enforce. Development, CI and a
self-hosted copy without Stripe run with no limits.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.context import require_tenant_id
from app.core.exceptions import AppError
from app.models.product import ProductVariant
from app.repositories.shopify import StoreListingRepository
from app.services.billing import BillingService, Entitlement


class BillingInactiveError(AppError):
    code = "billing_inactive"
    status_code = 402
    message = (
        "Your free trial has ended. Choose a plan under Settings → Billing to keep "
        "importing and publishing. Existing listings keep syncing."
    )


class ListingLimitError(AppError):
    code = "listing_limit_reached"
    status_code = 402
    message = "This would go over your plan's listing limit. Upgrade under Settings → Billing."


class AiAddonRequiredError(AppError):
    code = "ai_addon_required"
    status_code = 402
    message = "AI features need the AI add-on. Add it under Settings → Billing."


class BillingGate:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def enforced() -> bool:
        return settings.stripe.configured

    async def _entitlement(self) -> Entitlement:
        return await BillingService(self.session).entitlement()

    async def require_can_write(self) -> None:
        if not self.enforced():
            return
        if not (await self._entitlement()).can_write:
            raise BillingInactiveError()

    async def require_ai(self) -> None:
        if not self.enforced():
            return
        entitlement = await self._entitlement()
        if not entitlement.can_write:
            raise BillingInactiveError()
        if not entitlement.can_use_ai:
            raise AiAddonRequiredError()

    async def require_room_for(self, *, product_id: uuid.UUID, store_id: uuid.UUID) -> None:
        """Before a publish. A product already listed on this store is a
        republish and needs no room."""
        if not self.enforced():
            return
        entitlement = await self._entitlement()
        if not entitlement.can_write:
            raise BillingInactiveError()
        existing = await StoreListingRepository(self.session).get_for_product(
            store_id=store_id, product_id=product_id
        )
        if existing is not None and existing.external_product_id:
            return
        variants = await self.session.execute(
            select(func.count(ProductVariant.id)).where(
                ProductVariant.tenant_id == require_tenant_id(),
                ProductVariant.product_id == product_id,
                ProductVariant.deleted_at.is_(None),
                ProductVariant.is_enabled.is_(True),
            )
        )
        needed = max(int(variants.scalar_one()), 1)
        if entitlement.listings_used + needed > entitlement.listing_limit:
            raise ListingLimitError(
                details={
                    "used": entitlement.listings_used,
                    "limit": entitlement.listing_limit,
                    "needed": needed,
                }
            )


__all__ = [
    "AiAddonRequiredError",
    "BillingGate",
    "BillingInactiveError",
    "ListingLimitError",
]
