"""Track E6a — subscription billing through Stripe, end to end on a fake Stripe.

Real Postgres (migrations). Stripe is an ``httpx.MockTransport`` that keeps
customers and subscriptions in memory and records every form body, so the
exact parameters DropPilot sends are asserted, not assumed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import parse_qsl

import httpx
import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.billing import router as billing_router
from app.core.config import settings
from app.integrations.stripe import client as stripe_client
from app.models.billing import TenantSubscription
from tests.integration.test_ebay_c1_api import auth_header, register, token_with_roles

pytestmark = pytest.mark.integration

WEBHOOK_SECRET = "whsec_integration_test"
PRICES = {
    "droppilot_starter_monthly": "price_starter",
    "droppilot_growth_monthly": "price_growth",
    "droppilot_pro_monthly": "price_pro",
    "droppilot_ai_addon_starter_monthly": "price_ai_starter",
    "droppilot_ai_addon_growth_monthly": "price_ai_growth",
    "droppilot_ai_addon_pro_monthly": "price_ai_pro",
}


class FakeStripe:
    def __init__(self) -> None:
        self.posts: list[tuple[str, dict[str, str]]] = []
        self.customers: dict[str, dict[str, Any]] = {}
        self.subscriptions: dict[str, dict[str, Any]] = {}

    def subscription(
        self, customer: str, tenant_id: str, prices: list[str], status: str = "active"
    ) -> dict[str, Any]:
        sub_id = f"sub_{len(self.subscriptions) + 1}"
        sub = {
            "id": sub_id,
            "customer": customer,
            "status": status,
            "created": int(time.time()),
            "cancel_at_period_end": False,
            "metadata": {"tenant_id": tenant_id},
            "items": {
                "data": [
                    {
                        "id": f"si_{sub_id}_{i}",
                        "price": {"id": p},
                        "current_period_end": int(time.time()) + 30 * 86400,
                    }
                    for i, p in enumerate(prices)
                ]
            },
        }
        self.subscriptions[sub_id] = sub
        return sub

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        form = dict(parse_qsl(request.content.decode())) if request.content else {}
        if request.method == "POST":
            self.posts.append((path, form))
        if path == "/prices":
            wanted = [v for k, v in request.url.params.multi_items() if k.startswith("lookup_keys")]
            data = [{"id": PRICES[k], "lookup_key": k} for k in wanted if k in PRICES]
            return httpx.Response(200, json={"data": data})
        if path == "/customers":
            cid = f"cus_{len(self.customers) + 1}"
            self.customers[cid] = {"id": cid, "metadata": form}
            return httpx.Response(200, json={"id": cid})
        if path == "/checkout/sessions":
            return httpx.Response(
                200, json={"id": "cs_1", "url": "https://checkout.stripe.test/cs_1"}
            )
        if path == "/billing_portal/sessions":
            return httpx.Response(200, json={"url": "https://billing.stripe.test/p_1"})
        if path == "/subscriptions" and request.method == "GET":
            customer = request.url.params.get("customer")
            subs = [s for s in self.subscriptions.values() if s["customer"] == customer]
            return httpx.Response(200, json={"data": subs})
        if path.startswith("/subscriptions/"):
            sub = self.subscriptions.get(path.rsplit("/", 1)[1])
            if sub is None:
                return httpx.Response(404, json={"error": {"message": "No such subscription"}})
            if request.method == "POST":
                kept = [
                    i
                    for i in sub["items"]["data"]
                    if not any(
                        form.get(f"items[{n}][id]") == i["id"]
                        and form.get(f"items[{n}][deleted]") == "true"
                        for n in range(10)
                    )
                ]
                for n in range(10):
                    price = form.get(f"items[{n}][price]")
                    if price:
                        kept.append(
                            {"id": f"si_new_{n}", "price": {"id": price}, "current_period_end": 0}
                        )
                sub["items"]["data"] = kept
            return httpx.Response(200, json=sub)
        return httpx.Response(404, json={"error": {"message": f"unmocked {path}"}})


@pytest.fixture
def stripe(monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession) -> FakeStripe:
    fake = FakeStripe()
    monkeypatch.setattr(stripe_client, "_transport", httpx.MockTransport(fake.handle))
    monkeypatch.setattr(settings.stripe, "secret_key", SecretStr("sk_test_integration"))
    monkeypatch.setattr(settings.stripe, "webhook_secret", SecretStr(WEBHOOK_SECRET))

    @asynccontextmanager
    async def shared() -> AsyncIterator[AsyncSession]:
        yield db_session
        await db_session.flush()

    monkeypatch.setattr(billing_router, "transaction", shared)
    return fake


def signed(event: dict[str, Any]) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(event).encode()
    ts = int(time.time())
    sig = hmac.new(WEBHOOK_SECRET.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return body, {"Stripe-Signature": f"t={ts},v1={sig}", "Content-Type": "application/json"}


async def test_a_new_workspace_is_on_a_30_day_trial_without_ai(
    client: AsyncClient, stripe: FakeStripe
) -> None:
    owner = await register(client)
    status = (await client.get("/api/v1/billing", headers=auth_header(owner))).json()
    assert status["onTrial"] is True and status["paid"] is False
    assert status["canWrite"] is True and status["canUseAi"] is False
    assert status["listingLimit"] == 450 and status["listingsUsed"] == 0
    assert [p["key"] for p in status["plans"]] == ["starter", "growth", "pro"]
    assert [p["aiAddonUsd"] for p in status["plans"]] == [8, 12, 17]


async def test_checkout_sends_the_plan_add_on_tenant_and_remaining_trial(
    client: AsyncClient, stripe: FakeStripe
) -> None:
    owner = await register(client)
    tenant_id = owner["identity"]["tenant"]["id"]
    response = await client.post(
        "/api/v1/billing/checkout",
        json={"plan": "growth", "aiAddon": True},
        headers=auth_header(owner),
    )
    assert response.status_code == 200, response.text
    assert response.json()["url"] == "https://checkout.stripe.test/cs_1"
    [(_, customer), (_, session)] = [
        p for p in stripe.posts if p[0] in ("/customers", "/checkout/sessions")
    ]
    assert customer["metadata[tenant_id]"] == tenant_id
    assert session["mode"] == "subscription"
    assert session["line_items[0][price]"] == "price_growth"
    assert session["line_items[1][price]"] == "price_ai_growth"
    assert session["subscription_data[metadata][tenant_id]"] == tenant_id
    assert int(session["subscription_data[trial_end]"]) > time.time() + 29 * 86400
    assert session["success_url"].endswith("/settings/billing?checkout=success")


async def test_returning_from_checkout_syncs_the_paid_plan(
    client: AsyncClient, stripe: FakeStripe
) -> None:
    owner = await register(client)
    headers = auth_header(owner)
    await client.post("/api/v1/billing/checkout", json={"plan": "pro"}, headers=headers)
    stripe.subscription("cus_1", owner["identity"]["tenant"]["id"], ["price_pro", "price_ai_pro"])
    status = (await client.post("/api/v1/billing/sync", headers=headers)).json()
    assert status["paid"] is True and status["plan"] == "pro"
    assert status["canUseAi"] is True and status["listingLimit"] == 1000


async def test_changing_plan_swaps_items_and_drops_the_add_on(
    client: AsyncClient, stripe: FakeStripe
) -> None:
    owner = await register(client)
    headers = auth_header(owner)
    await client.post(
        "/api/v1/billing/checkout", json={"plan": "starter", "aiAddon": True}, headers=headers
    )
    stripe.subscription(
        "cus_1", owner["identity"]["tenant"]["id"], ["price_starter", "price_ai_starter"]
    )
    await client.post("/api/v1/billing/sync", headers=headers)
    changed = await client.post(
        "/api/v1/billing/change", json={"plan": "growth", "aiAddon": False}, headers=headers
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["plan"] == "growth" and changed.json()["aiAddon"] is False
    assert changed.json()["listingLimit"] == 450


async def test_a_signed_webhook_updates_the_workspace_from_stripes_copy(
    client: AsyncClient, db_session: AsyncSession, stripe: FakeStripe
) -> None:
    owner = await register(client)
    tenant_id = owner["identity"]["tenant"]["id"]
    await client.post(
        "/api/v1/billing/checkout", json={"plan": "starter"}, headers=auth_header(owner)
    )
    sub = stripe.subscription("cus_1", tenant_id, ["price_starter"], status="past_due")
    # The event body claims "active"; Stripe's own copy says past_due. Stripe wins.
    body, headers = signed(
        {
            "id": "evt_1",
            "type": "customer.subscription.updated",
            "data": {"object": {**sub, "status": "active"}},
        }
    )
    response = await client.post("/api/v1/billing/webhooks/stripe", content=body, headers=headers)
    assert response.status_code == 200, response.text
    row = await db_session.scalar(
        sa.select(TenantSubscription).where(TenantSubscription.tenant_id == uuid.UUID(tenant_id))
    )
    assert row is not None and row.status == "past_due" and row.plan == "starter"


async def test_a_bad_signature_is_refused_and_writes_nothing(
    client: AsyncClient, stripe: FakeStripe
) -> None:
    body, headers = signed(
        {"id": "evt_2", "type": "customer.subscription.updated", "data": {"object": {}}}
    )
    headers["Stripe-Signature"] = headers["Stripe-Signature"][:-2] + "00"
    response = await client.post("/api/v1/billing/webhooks/stripe", content=body, headers=headers)
    assert response.status_code == 400
    assert response.json()["code"] == "stripe_signature_invalid"


async def test_a_subscription_naming_another_workspace_is_ignored(
    client: AsyncClient, db_session: AsyncSession, stripe: FakeStripe
) -> None:
    victim = await register(client)
    victim_id = victim["identity"]["tenant"]["id"]
    await client.get("/api/v1/billing", headers=auth_header(victim))
    # A subscription whose metadata names the victim, but on a customer the
    # victim's workspace never created.
    sub = stripe.subscription("cus_attacker", victim_id, ["price_pro"])
    body, headers = signed(
        {"id": "evt_3", "type": "customer.subscription.created", "data": {"object": sub}}
    )
    assert (
        await client.post("/api/v1/billing/webhooks/stripe", content=body, headers=headers)
    ).status_code == 200
    row = await db_session.scalar(
        sa.select(TenantSubscription).where(TenantSubscription.tenant_id == uuid.UUID(victim_id))
    )
    assert row is not None and row.plan is None and row.status == "none"


async def test_only_owners_manage_billing_and_unconfigured_servers_say_so(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, stripe: FakeStripe
) -> None:
    owner = await register(client)
    admin = token_with_roles(owner, "admin")
    assert (
        await client.post("/api/v1/billing/checkout", json={"plan": "pro"}, headers=admin)
    ).status_code == 403
    assert (await client.get("/api/v1/billing", headers=admin)).status_code == 200
    monkeypatch.setattr(settings.stripe, "secret_key", None)
    refused = await client.post(
        "/api/v1/billing/checkout", json={"plan": "pro"}, headers=auth_header(owner)
    )
    assert refused.status_code == 503 and refused.json()["code"] == "billing_not_configured"
