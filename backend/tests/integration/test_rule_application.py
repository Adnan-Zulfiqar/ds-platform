"""M3A-3 — draft impact preview and confirmed rule application.

The guarantees here are about what is and is not written, so most assertions
read the database rather than the response: "preview writes nothing" and
"published products are untouched" are only meaningful as row-level facts.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import set_tenant_id
from app.core.tokens import create_access_token
from app.models.pricing import PricingRule
from app.models.product import Product, ProductSource, ProductStatus, ProductVariant
from app.models.rule_application import RuleApplication, RuleApplicationItem
from app.models.shopify import ListingSyncStatus, StoreListing
from app.models.store import Store, StorePlatform
from tests.integration.test_products import auth_header, register

pytestmark = pytest.mark.integration

BASE = "/api/v1/global-rules"
IMPACT = f"{BASE}/drafts/impact"
APPLY = f"{BASE}/drafts/apply"


async def seed_tenant(client: AsyncClient) -> tuple[dict[str, str], uuid.UUID]:
    body = await register(client)
    tenant_id = uuid.UUID(str(body["identity"]["tenant"]["id"]))
    set_tenant_id(tenant_id)
    return auth_header(body), tenant_id


def viewer_header(tenant_id: uuid.UUID) -> dict[str, str]:
    token = create_access_token(user_id=uuid.uuid4(), tenant_id=tenant_id, roles=("viewer",))
    return {"Authorization": f"Bearer {token.token}"}


async def seed_draft(
    db_session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    cost: str | None = "10.00",
    shipping: str | None = "4.00",
    currency: str | None = "USD",
    sell_price: str | None = None,
    title: str = "Draft product",
) -> Product:
    product = Product(
        tenant_id=tenant_id,
        source=ProductSource.MANUAL,
        external_id=f"m3a3-{uuid.uuid4().hex[:10]}",
        title=title,
        status=ProductStatus.DRAFT,
        cost_price_min=None if cost is None else Decimal(cost),
        shipping_cost=None if shipping is None else Decimal(shipping),
        currency=currency,
        sell_price=None if sell_price is None else Decimal(sell_price),
    )
    db_session.add(product)
    await db_session.flush()
    return product


async def publish(db_session: AsyncSession, product: Product) -> None:
    store = Store(
        tenant_id=product.tenant_id,
        name="Test store",
        slug=f"store-{uuid.uuid4().hex[:8]}",
        platform=StorePlatform.SHOPIFY,
        currency="USD",
    )
    db_session.add(store)
    await db_session.flush()
    db_session.add(
        StoreListing(
            tenant_id=product.tenant_id,
            store_id=store.id,
            product_id=product.id,
            external_product_id="gid://shopify/Product/1",
            status=ListingSyncStatus.SYNCED,
        )
    )
    await db_session.flush()


async def create_rule(client: AsyncClient, headers: dict[str, str], **overrides: object) -> dict:
    payload: dict[str, object] = {
        "name": f"Global {uuid.uuid4().hex[:6]}",
        "scope": "global",
        "strategy": "percentage_markup",
        "markupPercent": "50",
    }
    payload.update(overrides)
    response = await client.post(f"{BASE}/pricing", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


class TestImpactPreview:
    async def test_preview_reports_every_figure(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        await seed_draft(db_session, tenant_id, sell_price="30.00")

        response = await client.get(IMPACT, headers=headers)
        assert response.status_code == 200, response.text
        item = response.json()["items"][0]

        assert Decimal(item["itemCost"]) == Decimal("10")
        assert Decimal(item["supplierShippingCost"]) == Decimal("4")
        assert Decimal(item["landedCost"]) == Decimal("14")
        assert Decimal(item["proposedPrice"]) == Decimal("21")
        assert Decimal(item["currentPrice"]) == Decimal("30")
        assert Decimal(item["profit"]) == Decimal("7")
        assert Decimal(item["markupPercent"]) == Decimal("50")
        assert Decimal(item["marginPercent"]) == Decimal("33.33")
        assert item["pricingRuleVersion"] == 1
        assert item["pricingRuleScope"] == "global"
        assert item["ruleReason"]
        assert item["canApply"] is True

    async def test_preview_writes_nothing(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")

        async def counts() -> tuple[int, int, int]:
            return (
                (
                    await db_session.execute(select(func.count()).select_from(RuleApplication))
                ).scalar_one(),
                (
                    await db_session.execute(select(func.count()).select_from(RuleApplicationItem))
                ).scalar_one(),
                (
                    await db_session.execute(select(func.count()).select_from(PricingRule))
                ).scalar_one(),
            )

        before = await counts()
        before_price = product.sell_price
        before_updated = product.updated_at

        assert (await client.get(IMPACT, headers=headers)).status_code == 200

        await db_session.refresh(product)
        assert await counts() == before
        assert product.sell_price == before_price
        assert product.updated_at == before_updated

    async def test_preview_is_paginated(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        for index in range(5):
            await seed_draft(db_session, tenant_id, title=f"Draft {index}")

        response = await client.get(IMPACT, params={"page": 1, "size": 2}, headers=headers)
        body = response.json()
        assert len(body["items"]) == 2
        assert body["total"] == 5
        assert body["size"] == 2

    async def test_preview_rejects_an_unbounded_page_size(self, client: AsyncClient) -> None:
        """A settings screen must not be able to ask for the whole catalogue."""
        headers, _ = await seed_tenant(client)
        response = await client.get(IMPACT, params={"size": 5000}, headers=headers)
        assert response.status_code == 422

    async def test_preview_search_filters(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        await seed_draft(db_session, tenant_id, title="Blue widget")
        await seed_draft(db_session, tenant_id, title="Red gadget")

        response = await client.get(IMPACT, params={"search": "widget"}, headers=headers)
        assert response.json()["total"] == 1
        assert "Blue widget" in response.json()["items"][0]["title"]

    async def test_a_viewer_may_preview(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        await seed_draft(db_session, tenant_id)
        response = await client.get(IMPACT, headers=viewer_header(tenant_id))
        assert response.status_code == 200

    async def test_another_tenants_drafts_are_not_listed(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        owner, owner_tenant = await seed_tenant(client)
        await create_rule(client, owner)
        product = await seed_draft(db_session, owner_tenant)

        intruder, _ = await seed_tenant(client)
        response = await client.get(IMPACT, headers=intruder)
        assert all(item["productId"] != str(product.id) for item in response.json()["items"])

    async def test_missing_cost_is_flagged_not_priced(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        await seed_draft(db_session, tenant_id, cost=None)

        item = (await client.get(IMPACT, headers=headers)).json()["items"][0]
        assert item["proposedPrice"] is None
        assert item["needsReview"] is True
        assert item["canApply"] is False
        assert "supplier_cost_unknown" in item["reviewReasons"]

    async def test_a_published_product_is_marked_not_applicable(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id)
        await publish(db_session, product)

        item = (await client.get(IMPACT, headers=headers)).json()["items"][0]
        assert item["published"] is True
        assert item["canApply"] is False

    async def test_variants_are_priced_from_their_own_cost(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id)
        for cost in ("10.00", "20.00"):
            db_session.add(
                ProductVariant(
                    tenant_id=tenant_id,
                    product_id=product.id,
                    external_variant_id=f"sku-{uuid.uuid4().hex[:6]}",
                    cost_price=Decimal(cost),
                    currency="USD",
                )
            )
        await db_session.flush()

        item = (await client.get(IMPACT, headers=headers)).json()["items"][0]
        prices = sorted(Decimal(v["proposedPrice"]) for v in item["variants"])
        # (10 + 4) * 1.5 = 21 ; (20 + 4) * 1.5 = 36
        assert prices == [Decimal("21"), Decimal("36")]
        # The product's "from" price is derived from the variants.
        assert Decimal(item["proposedPrice"]) == Decimal("21")

    async def test_a_failed_variant_is_reported_not_dropped(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id)
        db_session.add(
            ProductVariant(
                tenant_id=tenant_id,
                product_id=product.id,
                external_variant_id="no-cost",
                cost_price=None,
                currency="USD",
            )
        )
        await db_session.flush()

        item = (await client.get(IMPACT, headers=headers)).json()["items"][0]
        assert len(item["variants"]) == 1
        assert item["variants"][0]["proposedPrice"] is None
        assert "supplier_cost_unknown" in item["variants"][0]["reviewReasons"]


class TestConfirmedApplication:
    async def test_a_confirmed_application_writes_the_price(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")

        response = await client.post(
            APPLY,
            json={"productIds": [str(product.id)], "idempotencyKey": "run-1"},
            headers=headers,
        )
        assert response.status_code == 202, response.text
        body = response.json()
        assert body["appliedCount"] == 1
        assert body["status"] == "completed"

        await db_session.refresh(product)
        assert product.sell_price == Decimal("21.0000")
        assert product.applied_pricing_rule_version == 1
        assert product.landed_cost == Decimal("14.0000")

    async def test_no_confirmation_writes_nothing(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Previewing is not confirming. The only way to change a price is
        the explicit apply call."""
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")

        await client.get(IMPACT, headers=headers)
        await db_session.refresh(product)
        assert product.sell_price == Decimal("30.0000")
        assert (
            await db_session.execute(select(func.count()).select_from(RuleApplication))
        ).scalar_one() == 0

    async def test_reusing_an_idempotency_key_returns_the_same_run(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")
        payload = {"productIds": [str(product.id)], "idempotencyKey": "retry-me"}

        first = await client.post(APPLY, json=payload, headers=headers)
        second = await client.post(APPLY, json=payload, headers=headers)
        assert first.status_code == 202 and second.status_code == 202
        assert first.json()["id"] == second.json()["id"]

        # And the retry did not write a second set of result rows.
        items = (
            await db_session.execute(select(func.count()).select_from(RuleApplicationItem))
        ).scalar_one()
        assert items == first.json()["appliedCount"]

    async def test_the_same_key_with_a_different_payload_is_a_conflict(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A retry carrying different work is a client bug, not a retry."""
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        first_product = await seed_draft(db_session, tenant_id)
        second_product = await seed_draft(db_session, tenant_id)

        await client.post(
            APPLY,
            json={"productIds": [str(first_product.id)], "idempotencyKey": "shared"},
            headers=headers,
        )
        clash = await client.post(
            APPLY,
            json={"productIds": [str(second_product.id)], "idempotencyKey": "shared"},
            headers=headers,
        )
        assert clash.status_code == 409, clash.text

    async def test_a_published_product_is_skipped_with_a_reason(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")
        await publish(db_session, product)
        before = product.sell_price

        response = await client.post(
            APPLY,
            json={"productIds": [str(product.id)], "idempotencyKey": "published-run"},
            headers=headers,
        )
        assert response.status_code == 202, response.text
        outcomes = {item["outcome"] for item in response.json()["items"]}
        assert outcomes == {"published"}

        await db_session.refresh(product)
        assert product.sell_price == before

    async def test_a_partial_failure_does_not_roll_back_the_successes(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        good = await seed_draft(db_session, tenant_id, sell_price="30.00")
        bad = await seed_draft(db_session, tenant_id, cost=None)

        response = await client.post(
            APPLY,
            json={"productIds": [str(good.id), str(bad.id)], "idempotencyKey": "partial"},
            headers=headers,
        )
        body = response.json()
        assert body["status"] == "partial"
        assert body["appliedCount"] == 1
        assert body["reviewCount"] == 1

        await db_session.refresh(good)
        await db_session.refresh(bad)
        assert good.sell_price == Decimal("21.0000")
        assert bad.needs_review is True
        assert "supplier_cost_unknown" in bad.pricing_review_reasons

    async def test_a_changed_rule_version_marks_items_stale(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        rule = await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")

        # The merchant previewed against version 1; the rule then changed.
        await client.patch(
            f"{BASE}/pricing/{rule['id']}",
            json={
                "name": rule["name"],
                "scope": "global",
                "strategy": "percentage_markup",
                "markupPercent": "90",
                "expectedUpdatedAt": rule["updatedAt"],
            },
            headers=headers,
        )

        response = await client.post(
            APPLY,
            json={
                "productIds": [str(product.id)],
                "idempotencyKey": "stale-run",
                "expectedRuleId": rule["id"],
                "expectedRuleVersion": 1,
            },
            headers=headers,
        )
        assert response.status_code == 202
        assert {i["outcome"] for i in response.json()["items"]} == {"stale"}

        await db_session.refresh(product)
        assert product.sell_price == Decimal("30.0000")

    async def test_an_already_correct_price_is_skipped(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="21.00")

        response = await client.post(
            APPLY,
            json={"productIds": [str(product.id)], "idempotencyKey": "noop"},
            headers=headers,
        )
        assert {i["outcome"] for i in response.json()["items"]} == {"skipped"}

    async def test_a_foreign_product_id_is_recorded_not_raised(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """One bad id must not roll back every draft that priced correctly."""
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        good = await seed_draft(db_session, tenant_id, sell_price="30.00")

        response = await client.post(
            APPLY,
            json={
                "productIds": [str(good.id), str(uuid.uuid4())],
                "idempotencyKey": "mixed",
            },
            headers=headers,
        )
        body = response.json()
        assert body["appliedCount"] == 1
        assert body["failedCount"] == 1

    async def test_supplier_snapshots_are_untouched(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")
        product.supplier_title = "Supplier original"
        product.supplier_description = "<p>Supplier copy</p>"
        await db_session.flush()

        await client.post(
            APPLY,
            json={"productIds": [str(product.id)], "idempotencyKey": "snapshot"},
            headers=headers,
        )
        await db_session.refresh(product)
        assert product.supplier_title == "Supplier original"
        assert product.supplier_description == "<p>Supplier copy</p>"
        assert product.cost_price_min == Decimal("10.0000")


class TestApplicationAccessAndLifecycle:
    async def test_a_viewer_cannot_start_an_application(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id)
        response = await client.post(
            APPLY,
            json={"productIds": [str(product.id)], "idempotencyKey": "viewer"},
            headers=viewer_header(tenant_id),
        )
        assert response.status_code == 403

    async def test_a_viewer_may_read_results(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")
        created = await client.post(
            APPLY,
            json={"productIds": [str(product.id)], "idempotencyKey": "readable"},
            headers=headers,
        )
        response = await client.get(
            f"{BASE}/applications/{created.json()['id']}", headers=viewer_header(tenant_id)
        )
        assert response.status_code == 200

    async def test_another_tenants_application_is_not_exposed(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        owner, owner_tenant = await seed_tenant(client)
        await create_rule(client, owner)
        product = await seed_draft(db_session, owner_tenant, sell_price="30.00")
        created = await client.post(
            APPLY,
            json={"productIds": [str(product.id)], "idempotencyKey": "private"},
            headers=owner,
        )
        intruder, _ = await seed_tenant(client)
        response = await client.get(f"{BASE}/applications/{created.json()['id']}", headers=intruder)
        assert response.status_code == 404

    async def test_a_completed_application_cannot_be_cancelled(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """The writes already landed; pretending they can be taken back would
        misrepresent the catalogue."""
        headers, tenant_id = await seed_tenant(client)
        await create_rule(client, headers)
        product = await seed_draft(db_session, tenant_id, sell_price="30.00")
        created = await client.post(
            APPLY,
            json={"productIds": [str(product.id)], "idempotencyKey": "done"},
            headers=headers,
        )
        response = await client.post(
            f"{BASE}/applications/{created.json()['id']}/cancel", headers=headers
        )
        assert response.status_code == 409, response.text

    async def test_an_empty_selection_is_rejected(self, client: AsyncClient) -> None:
        headers, _ = await seed_tenant(client)
        response = await client.post(
            APPLY, json={"productIds": [], "idempotencyKey": "empty"}, headers=headers
        )
        assert response.status_code == 422
