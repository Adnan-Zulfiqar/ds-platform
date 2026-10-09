"""Subscription billing (Track E6, owner decisions 2026-10-04).

**Plans** (monthly, USD; Stripe prices found by lookup key):

| Plan | Price | Listings | AI add-on |
|---|---|---|---|
| Starter | $12 | 200 | $8 |
| Growth | $30 | 450 | $12 |
| Pro | $70 | 1000 | $17 |

The AI add-on unlocks unlimited AI generation for its plan; without it there
is no AI. Listings are counted by ``StoreListingRepository.listings_used``:
each published product counts once per enabled variant, per store.

**Trial.** Every workspace gets ``STRIPE_TRIAL_DAYS`` (30) free from creation,
no card, on trial terms (``TRIAL``). It is granted once per store as well
(E6b, ``TrialFingerprint``). Subscribing during the trial keeps the
remaining free days: Stripe is told to start charging when the trial ends.

**After the trial without a subscription**, the workspace is read-only:
existing listings keep syncing, nothing is deleted, but no new imports,
publishes or AI runs.

**Stripe is the source of truth.** Checkout and the customer portal are
Stripe-hosted pages. A subscription's state reaches DropPilot in two ways,
and both re-read it from Stripe with the secret key:

* the signed webhook, treated as a doorbell (as for WooCommerce, D-014);
* an explicit sync when the merchant returns from checkout. This works
  before webhooks can reach a local machine.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.context import clear_context, require_tenant_id, set_tenant_id
from app.core.exceptions import ConflictError, ValidationError
from app.integrations.stripe.client import StripeClient, StripeRequestError
from app.models.billing import TenantSubscription
from app.repositories.billing import TenantSubscriptionRepository
from app.repositories.shopify import StoreListingRepository
from app.repositories.tenant import TenantRepository
from app.services.base import BaseService


@dataclass(frozen=True, slots=True)
class Plan:
    key: str
    name: str
    price_usd: int
    listing_limit: int
    ai_addon_usd: int

    @property
    def price_lookup_key(self) -> str:
        return f"droppilot_{self.key}_monthly"

    @property
    def ai_lookup_key(self) -> str:
        return f"droppilot_ai_addon_{self.key}_monthly"


PLANS: Final[dict[str, Plan]] = {
    "starter": Plan("starter", "Starter", 12, 200, 8),
    "growth": Plan("growth", "Growth", 30, 450, 12),
    "pro": Plan("pro", "Pro", 70, 1000, 17),
}
#: What the free trial allows: Growth's listings, no AI. AI calls cost real
#: money per request; the add-on is how they are paid for.
TRIAL_LISTING_LIMIT: Final = 450
#: Stripe statuses under which the paid plan applies. ``past_due`` keeps
#: access while Stripe retries the card (its dunning window).
_PAID: Final = frozenset({"active", "trialing", "past_due"})
#: Stripe refuses a Checkout trial_end closer than 48 hours away.
_MIN_TRIAL_HANDOFF: Final = timedelta(hours=48)


@dataclass(frozen=True, slots=True)
class Entitlement:
    plan: str | None
    status: str
    ai_addon: bool
    trial_ends_at: datetime
    on_trial: bool
    paid: bool
    listing_limit: int
    listings_used: int
    can_write: bool
    can_use_ai: bool
    cancel_at_period_end: bool
    current_period_end: datetime | None
    has_customer: bool
    #: Set while an operator's plan override applies (D-019).
    plan_override_until: datetime | None = None


def _ts(value: Any) -> datetime | None:
    return datetime.fromtimestamp(int(value), tz=UTC) if isinstance(value, int | float) else None


class BillingService(BaseService):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.rows = TenantSubscriptionRepository(session)

    # --- state ---------------------------------------------------------------

    async def _row(self, *, lock: bool = False) -> TenantSubscription:
        row = await self.rows.current(lock=lock)
        if row is not None:
            return row
        tenant = await TenantRepository(self.session).get_by_id(require_tenant_id())
        started = tenant.created_at if tenant is not None else datetime.now(UTC)
        return await self.rows.create(
            trial_ends_at=started + timedelta(days=settings.stripe.trial_days),
            status="none",
        )

    async def entitlement(self) -> Entitlement:
        row = await self._row()
        now = datetime.now(UTC)
        # An operator's override (D-019) decides while it lasts, whatever
        # Stripe says: it exists to grant or correct access by hand.
        overridden = (
            row.plan_override in PLANS
            and row.plan_override_until is not None
            and row.plan_override_until > now
        )
        paid = overridden or (row.status in _PAID and row.plan in PLANS)
        on_trial = not paid and row.trial_ends_at > now
        if overridden:
            plan = PLANS[str(row.plan_override)]
            limit, ai = plan.listing_limit, row.plan_override_ai
        elif paid:
            plan = PLANS[str(row.plan)]
            limit, ai = plan.listing_limit, row.ai_addon
        elif on_trial:
            limit, ai = TRIAL_LISTING_LIMIT, False
        else:
            limit, ai = 0, False
        return Entitlement(
            plan=(row.plan_override if overridden else row.plan) if paid else None,
            status=row.status,
            ai_addon=row.ai_addon,
            trial_ends_at=row.trial_ends_at,
            on_trial=on_trial,
            paid=paid,
            listing_limit=limit,
            listings_used=await StoreListingRepository(self.session).listings_used(),
            can_write=paid or on_trial,
            can_use_ai=ai,
            cancel_at_period_end=row.cancel_at_period_end,
            current_period_end=row.current_period_end,
            has_customer=row.stripe_customer_id is not None,
            plan_override_until=row.plan_override_until if overridden else None,
        )

    # --- checkout, changes, portal ------------------------------------------------

    def _plan(self, key: str) -> Plan:
        plan = PLANS.get(key)
        if plan is None:
            raise ValidationError("Unknown plan.", details={"plan": key})
        return plan

    async def _customer(self, row: TenantSubscription, client: StripeClient, email: str) -> str:
        if row.stripe_customer_id:
            return row.stripe_customer_id
        tenant_id = str(require_tenant_id())
        customer = await client.post(
            "/customers",
            {"email": email, "metadata": {"tenant_id": tenant_id}},
            idempotency_key=f"droppilot-customer-{tenant_id}",
        )
        await self.rows.update(row, stripe_customer_id=str(customer["id"]))
        return str(customer["id"])

    async def start_checkout(self, *, plan_key: str, ai_addon: bool, email: str) -> str:
        """A Stripe Checkout URL for a new subscription."""
        plan = self._plan(plan_key)
        row = await self._row(lock=True)
        if row.status in _PAID:
            raise ConflictError(
                "This workspace already has a subscription. Change the plan instead."
            )
        client = StripeClient()
        prices = await client.prices_by_lookup_key([plan.price_lookup_key, plan.ai_lookup_key])
        if plan.price_lookup_key not in prices or (ai_addon and plan.ai_lookup_key not in prices):
            raise StripeRequestError("The plan's price is not set up in Stripe.")
        customer = await self._customer(row, client, email)
        tenant_id = str(require_tenant_id())
        items: list[dict[str, Any]] = [{"price": prices[plan.price_lookup_key], "quantity": 1}]
        if ai_addon:
            items.append({"price": prices[plan.ai_lookup_key], "quantity": 1})
        base = settings.email.app_base_url.rstrip("/")
        subscription_data: dict[str, Any] = {"metadata": {"tenant_id": tenant_id}}
        if row.trial_ends_at - datetime.now(UTC) > _MIN_TRIAL_HANDOFF:
            subscription_data["trial_end"] = int(row.trial_ends_at.timestamp())
        session = await client.post(
            "/checkout/sessions",
            {
                "mode": "subscription",
                "customer": customer,
                "client_reference_id": tenant_id,
                "line_items": items,
                "subscription_data": subscription_data,
                "metadata": {"tenant_id": tenant_id},
                "success_url": f"{base}/settings/billing?checkout=success",
                "cancel_url": f"{base}/settings/billing?checkout=cancelled",
            },
        )
        self.logger.info("billing_checkout_started", plan=plan.key, ai_addon=ai_addon)
        return str(session["url"])

    async def change_plan(self, *, plan_key: str, ai_addon: bool) -> Entitlement:
        """Switch plan and/or add-on on an existing subscription, prorated."""
        plan = self._plan(plan_key)
        row = await self._row(lock=True)
        if row.status not in _PAID or not row.stripe_subscription_id:
            raise ConflictError("There is no active subscription to change. Subscribe first.")
        client = StripeClient()
        keys = [p.price_lookup_key for p in PLANS.values()] + [
            p.ai_lookup_key for p in PLANS.values()
        ]
        prices = await client.prices_by_lookup_key(keys)
        by_price = {price_id: key for key, price_id in prices.items()}
        subscription = await client.get(f"/subscriptions/{row.stripe_subscription_id}")
        items: list[dict[str, Any]] = []
        for item in subscription.get("items", {}).get("data", []):
            # Remove every DropPilot item; the new set is added below.
            if by_price.get(str(item.get("price", {}).get("id"))):
                items.append({"id": item["id"], "deleted": True})
        items.append({"price": prices[plan.price_lookup_key], "quantity": 1})
        if ai_addon:
            items.append({"price": prices[plan.ai_lookup_key], "quantity": 1})
        updated = await client.post(
            f"/subscriptions/{row.stripe_subscription_id}",
            {"items": items, "proration_behavior": "create_prorations"},
        )
        await self._apply(row, updated, by_price)
        self.logger.info("billing_plan_changed", plan=plan.key, ai_addon=ai_addon)
        return await self.entitlement()

    async def portal_url(self) -> str:
        row = await self._row()
        if not row.stripe_customer_id:
            raise ConflictError("Subscribe first; there is nothing to manage yet.")
        session = await StripeClient().post(
            "/billing_portal/sessions",
            {
                "customer": row.stripe_customer_id,
                "return_url": f"{settings.email.app_base_url.rstrip('/')}/settings/billing",
            },
        )
        return str(session["url"])

    # --- sync from Stripe ---------------------------------------------------------

    async def sync(self) -> Entitlement:
        """Re-read this workspace's subscription from Stripe."""
        row = await self._row(lock=True)
        if row.stripe_customer_id:
            client = StripeClient()
            listed = await client.get(
                "/subscriptions",
                {"customer": row.stripe_customer_id, "status": "all", "limit": 10},
            )
            subs = [s for s in listed.get("data", []) if isinstance(s, dict)]
            live = [s for s in subs if s.get("status") in _PAID] or subs
            if live:
                newest = max(live, key=lambda s: int(s.get("created") or 0))
                await self._apply(row, newest, await self._price_map(client))
        return await self.entitlement()

    async def _price_map(self, client: StripeClient) -> dict[str, str]:
        keys = [p.price_lookup_key for p in PLANS.values()] + [
            p.ai_lookup_key for p in PLANS.values()
        ]
        return {
            price_id: key for key, price_id in (await client.prices_by_lookup_key(keys)).items()
        }

    async def _apply(
        self, row: TenantSubscription, subscription: dict[str, Any], by_price: dict[str, str]
    ) -> None:
        plan: str | None = None
        ai_addon = False
        period_end: datetime | None = None
        for item in subscription.get("items", {}).get("data", []):
            key = by_price.get(str((item.get("price") or {}).get("id")), "")
            for candidate in PLANS.values():
                if key == candidate.price_lookup_key:
                    plan = candidate.key
                elif key == candidate.ai_lookup_key:
                    ai_addon = True
            period_end = _ts(item.get("current_period_end")) or period_end
        await self.rows.update(
            row,
            stripe_subscription_id=str(subscription.get("id")),
            plan=plan,
            ai_addon=ai_addon,
            status=str(subscription.get("status") or "none")[:32],
            current_period_end=period_end or _ts(subscription.get("current_period_end")),
            cancel_at_period_end=bool(subscription.get("cancel_at_period_end")),
            stripe_synced_at=datetime.now(UTC),
        )
        self.logger.info(
            "billing_subscription_synced", plan=plan, status=subscription.get("status")
        )


async def handle_stripe_event(session: AsyncSession, event: dict[str, Any]) -> None:
    """A verified webhook: find the subscription it is about, re-read it from
    Stripe, and apply it to the workspace named in *Stripe's* copy of the
    subscription metadata. The event body is not trusted beyond its ids."""
    obj = (event.get("data") or {}).get("object") or {}
    kind = str(event.get("type") or "")
    subscription_id: str | None = None
    if kind.startswith("customer.subscription."):
        subscription_id = obj.get("id")
    elif kind == "checkout.session.completed":
        subscription_id = obj.get("subscription")
    elif kind.startswith("invoice."):
        parent = (obj.get("parent") or {}).get("subscription_details") or {}
        subscription_id = parent.get("subscription") or obj.get("subscription")
    if not subscription_id:
        return
    client = StripeClient()
    subscription = await client.get(f"/subscriptions/{subscription_id}")
    tenant_raw = (subscription.get("metadata") or {}).get("tenant_id")
    try:
        tenant_id = uuid.UUID(str(tenant_raw))
    except ValueError:
        return  # not one of DropPilot's subscriptions
    set_tenant_id(tenant_id)
    try:
        service = BillingService(session)
        row = await service.rows.current(lock=True)
        if row is None or row.stripe_customer_id != subscription.get("customer"):
            # The customer must be the one this workspace created; a
            # subscription naming a tenant it does not belong to is ignored.
            service.logger.warning("billing_webhook_customer_mismatch")
            return
        await service._apply(row, subscription, await service._price_map(client))
    finally:
        clear_context()


__all__ = [
    "PLANS",
    "TRIAL_LISTING_LIMIT",
    "BillingService",
    "Entitlement",
    "Plan",
    "handle_stripe_event",
]
