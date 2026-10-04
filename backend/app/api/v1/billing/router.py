"""Billing endpoints (Track E6).

Owners manage the subscription (``RoleName.OWNER`` is "billing control");
every role can see the plan and usage. The Stripe webhook is public by
nature and verified by signature before anything is read.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.deps import CurrentUser, DbSession, RequireOwner, RequireViewer
from app.core.config import settings
from app.core.request_body import read_bounded_body
from app.database.session import transaction
from app.integrations.stripe.client import (
    StripeSignatureError,
    parse_event,
    verify_signature,
)
from app.schemas.billing import BillingStatusRead, PlanChoice, PlanRead, RedirectRead
from app.schemas.common import MessageResponse
from app.services.billing import PLANS, BillingService, Entitlement, handle_stripe_event

router = APIRouter(prefix="/billing", tags=["billing"])

_WEBHOOK_MAX_BYTES = 512_000


def _status(entitlement: Entitlement) -> BillingStatusRead:
    return BillingStatusRead(
        configured=settings.stripe.configured,
        plans=[
            PlanRead(
                key=p.key,
                name=p.name,
                price_usd=p.price_usd,
                listing_limit=p.listing_limit,
                ai_addon_usd=p.ai_addon_usd,
            )
            for p in PLANS.values()
        ],
        plan=entitlement.plan,
        status=entitlement.status,
        ai_addon=entitlement.ai_addon,
        trial_ends_at=entitlement.trial_ends_at,
        on_trial=entitlement.on_trial,
        paid=entitlement.paid,
        listing_limit=entitlement.listing_limit,
        listings_used=entitlement.listings_used,
        can_write=entitlement.can_write,
        can_use_ai=entitlement.can_use_ai,
        cancel_at_period_end=entitlement.cancel_at_period_end,
        current_period_end=entitlement.current_period_end,
        has_customer=entitlement.has_customer,
    )


@router.get("", response_model=BillingStatusRead, summary="Plan, trial and usage")
async def billing_status(session: DbSession, _principal: RequireViewer) -> BillingStatusRead:
    return _status(await BillingService(session).entitlement())


@router.post("/checkout", response_model=RedirectRead, summary="Start a Stripe Checkout")
async def billing_checkout(
    payload: PlanChoice, session: DbSession, _principal: RequireOwner, user: CurrentUser
) -> RedirectRead:
    url = await BillingService(session).start_checkout(
        plan_key=payload.plan, ai_addon=payload.ai_addon, email=user.email
    )
    return RedirectRead(url=url)


@router.post("/change", response_model=BillingStatusRead, summary="Change plan or AI add-on")
async def billing_change(
    payload: PlanChoice, session: DbSession, _principal: RequireOwner
) -> BillingStatusRead:
    return _status(
        await BillingService(session).change_plan(plan_key=payload.plan, ai_addon=payload.ai_addon)
    )


@router.post("/portal", response_model=RedirectRead, summary="Open the Stripe customer portal")
async def billing_portal(session: DbSession, _principal: RequireOwner) -> RedirectRead:
    return RedirectRead(url=await BillingService(session).portal_url())


@router.post("/sync", response_model=BillingStatusRead, summary="Re-read the subscription")
async def billing_sync(session: DbSession, _principal: RequireViewer) -> BillingStatusRead:
    """Called when the merchant returns from Checkout, so the new plan shows
    at once even before the webhook arrives (or where it cannot arrive)."""
    return _status(await BillingService(session).sync())


@router.post(
    "/webhooks/stripe",
    response_model=MessageResponse,
    summary="Stripe webhook (signature-verified)",
)
async def stripe_webhook(request: Request) -> MessageResponse:
    secret = settings.stripe.webhook_secret
    if secret is None or not secret.get_secret_value():
        raise StripeSignatureError("Stripe webhooks are not configured.")
    raw = await read_bounded_body(request, max_bytes=_WEBHOOK_MAX_BYTES)
    verify_signature(
        payload=raw,
        header=request.headers.get("stripe-signature", ""),
        secret=secret.get_secret_value(),
    )
    event = parse_event(raw)
    # A failure here answers non-2xx, so Stripe retries the delivery: a
    # transient error reading the subscription is retried, not lost.
    async with transaction() as session:
        await handle_stripe_event(session, event)
    return MessageResponse(message="ok")
