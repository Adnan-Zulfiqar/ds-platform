"""Track E6b — plan limits where they are spent, and one trial per store.

Real Postgres (migrations). Billing is switched on by giving Stripe a secret
key; the gate never calls Stripe, so no transport is needed.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.context import set_tenant_id
from app.models.billing import TenantSubscription, TrialFingerprint
from app.models.product import Product, ProductSource, ProductStatus, ProductVariant
from app.models.shopify import ListingSyncStatus, StoreListing
from app.models.store import Store, StorePlatform, StoreStatus
from app.services.billing import BillingService
from app.services.entitlements import (
    AiAddonRequiredError,
    BillingGate,
    BillingInactiveError,
    ListingLimitError,
)
from app.tasks import billing as billing_tasks
from tests.integration.test_ebay_c1_api import auth_header, register

pytestmark = pytest.mark.integration


@pytest.fixture
def billing_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.stripe, "secret_key", SecretStr("sk_test_limits"))


async def workspace(client: AsyncClient) -> tuple[dict[str, Any], uuid.UUID]:
    owner = await register(client)
    tenant_id = uuid.UUID(str(owner["identity"]["tenant"]["id"]))
    set_tenant_id(tenant_id)
    return owner, tenant_id


async def set_plan(
    db_session: AsyncSession,
    *,
    plan: str | None = None,
    status: str = "none",
    ai: bool = False,
    trial_over: bool = False,
) -> None:
    service = BillingService(db_session)
    row = await service._row()
    row.plan, row.status, row.ai_addon = plan, status, ai
    if trial_over:
        row.trial_ends_at = datetime.now(UTC) - timedelta(minutes=1)
    await db_session.flush()


async def store(db_session: AsyncSession, tenant_id: uuid.UUID) -> uuid.UUID:
    row = Store(
        tenant_id=tenant_id,
        name="Shop",
        slug=f"shop-{uuid.uuid4().hex[:6]}",
        platform=StorePlatform.SHOPIFY,
        status=StoreStatus.CONNECTED,
        currency="USD",
    )
    db_session.add(row)
    await db_session.flush()
    return row.id


async def product(db_session: AsyncSession, tenant_id: uuid.UUID, *, variants: int) -> uuid.UUID:
    row = Product(
        tenant_id=tenant_id,
        source=ProductSource.MANUAL,
        external_id=f"p-{uuid.uuid4().hex[:10]}",
        title="Mug",
        status=ProductStatus.DRAFT,
        sell_price=Decimal("10"),
    )
    db_session.add(row)
    await db_session.flush()
    for i in range(variants):
        db_session.add(
            ProductVariant(
                tenant_id=tenant_id,
                product_id=row.id,
                external_variant_id=f"v{i}",
                stock_quantity=1,
                is_enabled=True,
            )
        )
    await db_session.flush()
    return row.id


async def listed(
    db_session: AsyncSession, tenant_id: uuid.UUID, store_id: uuid.UUID, product_id: uuid.UUID
) -> None:
    db_session.add(
        StoreListing(
            tenant_id=tenant_id,
            store_id=store_id,
            product_id=product_id,
            external_product_id=f"ext-{uuid.uuid4().hex[:6]}",
            status=ListingSyncStatus.SYNCED,
        )
    )
    await db_session.flush()


async def test_listings_count_variants_per_store(
    client: AsyncClient, db_session: AsyncSession, billing_on: None
) -> None:
    _, tenant_id = await workspace(client)
    shop_a, shop_b = await store(db_session, tenant_id), await store(db_session, tenant_id)
    ten = await product(db_session, tenant_id, variants=10)
    plain = await product(db_session, tenant_id, variants=0)
    await listed(db_session, tenant_id, shop_a, ten)
    await listed(db_session, tenant_id, shop_b, ten)
    await listed(db_session, tenant_id, shop_a, plain)
    entitlement = await BillingService(db_session).entitlement()
    assert entitlement.listings_used == 21  # 10 + 10 + 1


async def test_a_new_listing_must_fit_but_a_republish_is_free(
    client: AsyncClient, db_session: AsyncSession, billing_on: None
) -> None:
    _, tenant_id = await workspace(client)
    await set_plan(db_session, plan="starter", status="active")  # 200 listings
    shop = await store(db_session, tenant_id)
    for _ in range(19):
        await listed(db_session, tenant_id, shop, await product(db_session, tenant_id, variants=10))
    gate = BillingGate(db_session)
    fits = await product(db_session, tenant_id, variants=10)  # 190 + 10 = 200
    await gate.require_room_for(product_id=fits, store_id=shop)
    await listed(db_session, tenant_id, shop, fits)
    too_many = await product(db_session, tenant_id, variants=1)
    with pytest.raises(ListingLimitError) as caught:
        await gate.require_room_for(product_id=too_many, store_id=shop)
    assert caught.value.details == {"used": 200, "limit": 200, "needed": 1}
    await gate.require_room_for(product_id=fits, store_id=shop)  # republish


async def test_ai_needs_the_add_on_on_every_plan_and_never_on_trial(
    client: AsyncClient, db_session: AsyncSession, billing_on: None
) -> None:
    await workspace(client)
    gate = BillingGate(db_session)
    with pytest.raises(AiAddonRequiredError):
        await gate.require_ai()  # trial
    await set_plan(db_session, plan="pro", status="active", ai=False)
    with pytest.raises(AiAddonRequiredError):
        await gate.require_ai()
    await set_plan(db_session, plan="pro", status="active", ai=True)
    await gate.require_ai()


async def test_after_the_trial_without_a_plan_the_workspace_is_read_only(
    client: AsyncClient, db_session: AsyncSession, billing_on: None
) -> None:
    owner, _ = await workspace(client)
    await set_plan(db_session, trial_over=True)
    with pytest.raises(BillingInactiveError):
        await BillingGate(db_session).require_can_write()
    response = await client.post(
        "/api/v1/products/import",
        json={"externalId": "3256806389000685"},
        headers=auth_header(owner),
    )
    assert response.status_code == 402
    assert response.json()["code"] == "billing_inactive"


async def test_nothing_is_enforced_without_stripe(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.stripe, "secret_key", None)
    await workspace(client)
    await set_plan(db_session, trial_over=True)
    gate = BillingGate(db_session)
    await gate.require_can_write()
    await gate.require_ai()


@pytest.fixture
def shared_tx(monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession) -> None:
    @asynccontextmanager
    async def shared() -> AsyncIterator[AsyncSession]:
        yield db_session
        await db_session.flush()

    monkeypatch.setattr(billing_tasks, "transaction", shared)


async def trial_end(db_session: AsyncSession, tenant_id: uuid.UUID) -> datetime:
    value = await db_session.scalar(
        sa.select(TenantSubscription.trial_ends_at).where(TenantSubscription.tenant_id == tenant_id)
    )
    assert value is not None
    return value


async def test_a_store_gives_one_trial_across_accounts(
    client: AsyncClient, db_session: AsyncSession, shared_tx: None
) -> None:
    _, first = await workspace(client)
    await BillingService(db_session)._row()
    _, second = await workspace(client)
    await BillingService(db_session)._row()
    fingerprint = billing_tasks.store_fingerprint("shopify", "Acme.myshopify.com")

    assert await billing_tasks._claim(first, fingerprint) is False  # first use
    assert await billing_tasks._claim(first, fingerprint) is False  # same account again
    assert await billing_tasks._claim(second, fingerprint) is True  # reused store
    assert await trial_end(db_session, second) <= datetime.now(UTC)
    assert await trial_end(db_session, first) > datetime.now(UTC)
    stored = await db_session.scalar(sa.select(TrialFingerprint.fingerprint))
    assert stored == fingerprint and "acme" not in stored


async def test_a_paying_workspace_is_unaffected_and_a_deleted_holder_still_counts(
    client: AsyncClient, db_session: AsyncSession, shared_tx: None
) -> None:
    _, payer = await workspace(client)
    await set_plan(db_session, plan="growth", status="active")
    fingerprint = billing_tasks.store_fingerprint("ebay", "seller-123")
    db_session.add(TrialFingerprint(fingerprint=fingerprint, first_tenant_id=None))
    await db_session.flush()
    assert await billing_tasks._claim(payer, fingerprint) is False  # paying: no change

    _, newcomer = await workspace(client)
    await BillingService(db_session)._row()
    assert await billing_tasks._claim(newcomer, fingerprint) is True
