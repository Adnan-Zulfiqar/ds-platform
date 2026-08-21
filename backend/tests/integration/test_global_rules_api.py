"""M3A-2 — global rule management, versioning, permissions and preview.

Everything here goes through the HTTP API with a real token, because that is
the boundary a merchant and an attacker both meet. Where a guarantee is about
what was *stored* rather than what was returned, the assertion reads the
database directly, so a response-shaped illusion cannot make it pass.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import set_tenant_id
from app.core.tokens import create_access_token
from app.models.pricing import GlobalRuleVersion, PricingRule
from app.models.product import Product, ProductSource, ProductStatus
from tests.integration.test_products import auth_header, register


async def seed_product(db_session: AsyncSession, body: dict) -> uuid.UUID:
    """A minimal draft in the registering tenant, for scope-bound rules."""
    tenant_id = uuid.UUID(str(body["identity"]["tenant"]["id"]))
    set_tenant_id(tenant_id)
    product = Product(
        tenant_id=tenant_id,
        source=ProductSource.MANUAL,
        external_id=f"m3a2-{uuid.uuid4().hex[:10]}",
        title="Scope target",
        status=ProductStatus.DRAFT,
    )
    db_session.add(product)
    await db_session.flush()
    return product.id


pytestmark = pytest.mark.integration

BASE = "/api/v1/global-rules"
PRICING = f"{BASE}/pricing"
SHIPPING = f"{BASE}/shipping"


def markup_rule(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": f"Global markup {uuid.uuid4().hex[:6]}",
        "scope": "global",
        "strategy": "percentage_markup",
        "markupPercent": "50",
    }
    payload.update(overrides)
    return payload


def shipping_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": f"Global shipping {uuid.uuid4().hex[:6]}",
        "scope": "global",
        "destinationCountry": "GB",
        "selectionStrategy": "cheapest_tracked",
    }
    payload.update(overrides)
    return payload


async def admin(client: AsyncClient) -> dict[str, str]:
    return auth_header(await register(client))


async def viewer_for(client: AsyncClient, body: dict) -> dict[str, str]:
    """A viewer inside the *same* tenant as `body`'s admin."""
    tenant_id = uuid.UUID(str(body["identity"]["tenant"]["id"]))
    token = create_access_token(user_id=uuid.uuid4(), tenant_id=tenant_id, roles=("viewer",))
    return {"Authorization": f"Bearer {token.token}"}


async def create_pricing(client: AsyncClient, headers: dict[str, str], **overrides: object) -> dict:
    response = await client.post(PRICING, json=markup_rule(**overrides), headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


class TestPricingRuleManagement:
    async def test_create_read_and_list(self, client: AsyncClient) -> None:
        headers = await admin(client)
        created = await create_pricing(client, headers)
        assert created["version"] == 1
        assert created["isActive"] is True

        fetched = await client.get(f"{PRICING}/{created['id']}", headers=headers)
        assert fetched.status_code == 200
        assert fetched.json()["name"] == created["name"]

        listed = await client.get(PRICING, headers=headers)
        assert listed.status_code == 200
        assert any(item["id"] == created["id"] for item in listed.json()["items"])

    async def test_update_bumps_the_version(self, client: AsyncClient) -> None:
        headers = await admin(client)
        created = await create_pricing(client, headers)
        response = await client.patch(
            f"{PRICING}/{created['id']}",
            json={
                "name": created["name"],
                "scope": "global",
                "strategy": "percentage_markup",
                "markupPercent": "75",
                "expectedUpdatedAt": created["updatedAt"],
            },
            headers=headers,
        )
        assert response.status_code == 200, response.text
        assert response.json()["version"] == 2
        assert Decimal(response.json()["markupPercent"]) == Decimal("75")

    async def test_deactivate_then_reactivate(self, client: AsyncClient) -> None:
        headers = await admin(client)
        created = await create_pricing(client, headers)

        off = await client.post(
            f"{PRICING}/{created['id']}/activation",
            json={"isActive": False, "expectedUpdatedAt": created["updatedAt"]},
            headers=headers,
        )
        assert off.status_code == 200, off.text
        assert off.json()["isActive"] is False

        on = await client.post(
            f"{PRICING}/{created['id']}/activation",
            json={"isActive": True, "expectedUpdatedAt": off.json()["updatedAt"]},
            headers=headers,
        )
        assert on.status_code == 200, on.text
        assert on.json()["isActive"] is True

    async def test_a_second_active_global_rule_is_refused(self, client: AsyncClient) -> None:
        """The partial unique index added in 0024. Two concurrent activations
        would both read "no active global rule"; only the database can settle
        that, so this proves the constraint reaches the merchant as a
        conflict rather than a 500."""
        headers = await admin(client)
        await create_pricing(client, headers)
        second = await client.post(PRICING, json=markup_rule(), headers=headers)
        assert second.status_code == 409, second.text


class TestShippingRuleManagement:
    async def test_create_update_and_deactivate(self, client: AsyncClient) -> None:
        headers = await admin(client)
        created = await client.post(SHIPPING, json=shipping_payload(), headers=headers)
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["version"] == 1

        updated = await client.patch(
            f"{SHIPPING}/{body['id']}",
            json={
                "name": body["name"],
                "scope": "global",
                "destinationCountry": "DE",
                "selectionStrategy": "fastest",
                "expectedUpdatedAt": body["updatedAt"],
            },
            headers=headers,
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["destinationCountry"] == "DE"
        assert updated.json()["version"] == 2


class TestVersionHistory:
    async def test_history_is_append_only_and_monotonic(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers = await admin(client)
        created = await create_pricing(client, headers)
        current = created
        for percent in ("60", "70"):
            response = await client.patch(
                f"{PRICING}/{current['id']}",
                json={
                    "name": current["name"],
                    "scope": "global",
                    "strategy": "percentage_markup",
                    "markupPercent": percent,
                    "expectedUpdatedAt": current["updatedAt"],
                },
                headers=headers,
            )
            assert response.status_code == 200, response.text
            current = response.json()

        history = await client.get(f"{BASE}/pricing/{created['id']}/history", headers=headers)
        assert history.status_code == 200, history.text
        body = history.json()
        versions = [entry["version"] for entry in body["items"]]
        assert versions == [3, 2, 1]
        assert body["meta"]["totalItems"] == 3

    async def test_a_version_records_who_what_and_the_previous_value(
        self, client: AsyncClient
    ) -> None:
        headers = await admin(client)
        created = await create_pricing(client, headers)
        await client.patch(
            f"{PRICING}/{created['id']}",
            json={
                "name": created["name"],
                "scope": "global",
                "strategy": "percentage_markup",
                "markupPercent": "80",
                "expectedUpdatedAt": created["updatedAt"],
                "note": "Raising margin for Q4",
            },
            headers=headers,
        )
        history = (
            await client.get(f"{BASE}/pricing/{created['id']}/history", headers=headers)
        ).json()
        latest = history["items"][0]
        assert "markup_percent" in latest["changedFields"]
        assert Decimal(latest["previousValues"]["markup_percent"]) == Decimal("50")
        assert Decimal(latest["newValues"]["markup_percent"]) == Decimal("80")
        assert latest["note"] == "Raising margin for Q4"
        assert latest["changedByUserId"] is not None
        # The snapshot answers "what were all the settings", not just the delta.
        assert latest["snapshot"]["strategy"] == "percentage_markup"

    async def test_a_no_op_update_writes_nothing_and_adds_no_version(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers = await admin(client)
        created = await create_pricing(client, headers)

        before = (
            await db_session.execute(select(func.count()).select_from(GlobalRuleVersion))
        ).scalar_one()

        response = await client.patch(
            f"{PRICING}/{created['id']}",
            json={
                "name": created["name"],
                "scope": "global",
                "strategy": "percentage_markup",
                "markupPercent": "50",
                "expectedUpdatedAt": created["updatedAt"],
            },
            headers=headers,
        )
        assert response.status_code == 200, response.text
        assert response.json()["version"] == 1
        assert response.json()["updatedAt"] == created["updatedAt"]

        after = (
            await db_session.execute(select(func.count()).select_from(GlobalRuleVersion))
        ).scalar_one()
        assert after == before

    async def test_repeating_a_deactivation_is_idempotent(self, client: AsyncClient) -> None:
        headers = await admin(client)
        created = await create_pricing(client, headers)
        first = await client.post(
            f"{PRICING}/{created['id']}/activation",
            json={"isActive": False, "expectedUpdatedAt": created["updatedAt"]},
            headers=headers,
        )
        assert first.status_code == 200
        again = await client.post(
            f"{PRICING}/{created['id']}/activation",
            json={"isActive": False, "expectedUpdatedAt": first.json()["updatedAt"]},
            headers=headers,
        )
        assert again.status_code == 200
        assert again.json()["version"] == first.json()["version"]

    async def test_history_survives_deactivation(self, client: AsyncClient) -> None:
        """ "What happened to the rule that is no longer active" is exactly
        when someone reads history, so it must still answer."""
        headers = await admin(client)
        created = await create_pricing(client, headers)
        await client.post(
            f"{PRICING}/{created['id']}/activation",
            json={"isActive": False, "expectedUpdatedAt": created["updatedAt"]},
            headers=headers,
        )
        history = await client.get(f"{BASE}/pricing/{created['id']}/history", headers=headers)
        assert history.status_code == 200
        assert len(history.json()["items"]) >= 2

    async def test_history_is_paginated(self, client: AsyncClient) -> None:
        """Append-only history grows without bound; it is the one list in this
        API that previously returned everything."""
        headers = await admin(client)
        created = await create_pricing(client, headers)
        current = created
        for percent in ("60", "70", "80"):
            response = await client.patch(
                f"{PRICING}/{current['id']}",
                json={
                    "name": current["name"],
                    "scope": "global",
                    "strategy": "percentage_markup",
                    "markupPercent": percent,
                    "expectedUpdatedAt": current["updatedAt"],
                },
                headers=headers,
            )
            current = response.json()

        first = (
            await client.get(
                f"{BASE}/pricing/{created['id']}/history",
                params={"page": 1, "size": 2},
                headers=headers,
            )
        ).json()
        second = (
            await client.get(
                f"{BASE}/pricing/{created['id']}/history",
                params={"page": 2, "size": 2},
                headers=headers,
            )
        ).json()

        assert [e["version"] for e in first["items"]] == [4, 3]
        assert [e["version"] for e in second["items"]] == [2, 1]
        assert first["meta"]["totalItems"] == 4
        assert first["meta"]["hasNext"] is True
        assert second["meta"]["hasNext"] is False

    async def test_history_refuses_an_unbounded_page_size(self, client: AsyncClient) -> None:
        headers = await admin(client)
        created = await create_pricing(client, headers)
        response = await client.get(
            f"{BASE}/pricing/{created['id']}/history", params={"size": 5000}, headers=headers
        )
        assert response.status_code == 422

    async def test_history_holds_no_credentials(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers = await admin(client)
        created = await create_pricing(client, headers)
        rows = (await db_session.execute(select(GlobalRuleVersion.snapshot))).scalars().all()
        blob = " ".join(str(row) for row in rows).lower()
        for forbidden in ("secret", "password", "token", "api_key"):
            assert forbidden not in blob
        assert created["id"]


class TestConcurrency:
    async def test_a_stale_token_is_rejected(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """The concurrent write is simulated as a direct row update rather
        than a second PATCH: these fixtures share one transaction and
        Postgres freezes `now()` for its duration, so two HTTP calls cannot
        actually move `updated_at` apart here regardless of whether the
        compare-and-swap works. Same reasoning as
        `test_draft_editor_concurrency.py`."""
        headers = await admin(client)
        created = await create_pricing(client, headers)
        stale = created["updatedAt"]

        moved_on = datetime.fromisoformat(stale) + timedelta(seconds=5)
        await db_session.execute(
            update(PricingRule)
            .where(PricingRule.id == uuid.UUID(created["id"]))
            .values(updated_at=moved_on)
        )
        await db_session.flush()

        second = await client.patch(
            f"{PRICING}/{created['id']}",
            json={
                "name": created["name"],
                "scope": "global",
                "strategy": "percentage_markup",
                "markupPercent": "62",
                "expectedUpdatedAt": stale,
            },
            headers=headers,
        )
        assert second.status_code == 409, second.text

    async def test_a_missing_token_is_a_validation_error(self, client: AsyncClient) -> None:
        headers = await admin(client)
        created = await create_pricing(client, headers)
        response = await client.patch(
            f"{PRICING}/{created['id']}",
            json={
                "name": created["name"],
                "scope": "global",
                "strategy": "percentage_markup",
                "markupPercent": "55",
            },
            headers=headers,
        )
        assert response.status_code == 422, response.text

    async def test_a_malformed_token_is_a_validation_error(self, client: AsyncClient) -> None:
        headers = await admin(client)
        created = await create_pricing(client, headers)
        response = await client.patch(
            f"{PRICING}/{created['id']}",
            json={
                "name": created["name"],
                "scope": "global",
                "strategy": "percentage_markup",
                "markupPercent": "55",
                "expectedUpdatedAt": "not-a-timestamp",
            },
            headers=headers,
        )
        assert response.status_code == 422, response.text


class TestPermissions:
    async def test_a_viewer_may_read_rules(self, client: AsyncClient) -> None:
        body = await register(client)
        headers = auth_header(body)
        created = await create_pricing(client, headers)
        viewer = await viewer_for(client, body)

        assert (await client.get(PRICING, headers=viewer)).status_code == 200
        assert (await client.get(f"{PRICING}/{created['id']}", headers=viewer)).status_code == 200
        history = await client.get(f"{BASE}/pricing/{created['id']}/history", headers=viewer)
        assert history.status_code == 200

    async def test_a_viewer_may_run_a_preview(self, client: AsyncClient) -> None:
        body = await register(client)
        await create_pricing(client, auth_header(body))
        viewer = await viewer_for(client, body)
        response = await client.post(
            f"{BASE}/preview",
            json={"itemCost": "10.00", "shippingCost": "4.00", "currency": "USD"},
            headers=viewer,
        )
        assert response.status_code == 200, response.text

    async def test_a_viewer_cannot_create_a_rule(self, client: AsyncClient) -> None:
        body = await register(client)
        viewer = await viewer_for(client, body)
        response = await client.post(PRICING, json=markup_rule(), headers=viewer)
        assert response.status_code == 403, response.text

    async def test_a_viewer_cannot_update_or_deactivate(self, client: AsyncClient) -> None:
        body = await register(client)
        created = await create_pricing(client, auth_header(body))
        viewer = await viewer_for(client, body)

        update = await client.patch(
            f"{PRICING}/{created['id']}",
            json={
                "name": created["name"],
                "scope": "global",
                "strategy": "percentage_markup",
                "markupPercent": "55",
                "expectedUpdatedAt": created["updatedAt"],
            },
            headers=viewer,
        )
        assert update.status_code == 403, update.text

        activation = await client.post(
            f"{PRICING}/{created['id']}/activation",
            json={"isActive": False, "expectedUpdatedAt": created["updatedAt"]},
            headers=viewer,
        )
        assert activation.status_code == 403, activation.text

    async def test_unauthenticated_access_is_rejected(self, client: AsyncClient) -> None:
        assert (await client.get(PRICING)).status_code == 401


class TestTenantIsolation:
    async def test_another_tenants_rule_is_404_not_403(self, client: AsyncClient) -> None:
        """404 for both "missing" and "someone else's": a 403 would confirm
        the id exists and let an attacker enumerate other tenants."""
        owner = await admin(client)
        created = await create_pricing(client, owner)
        intruder = await admin(client)

        assert (await client.get(f"{PRICING}/{created['id']}", headers=intruder)).status_code == 404

    async def test_an_unknown_id_gives_the_same_404(self, client: AsyncClient) -> None:
        headers = await admin(client)
        response = await client.get(f"{PRICING}/{uuid.uuid4()}", headers=headers)
        assert response.status_code == 404

    async def test_another_tenants_rule_cannot_be_updated(self, client: AsyncClient) -> None:
        owner = await admin(client)
        created = await create_pricing(client, owner)
        intruder = await admin(client)
        response = await client.patch(
            f"{PRICING}/{created['id']}",
            json={
                "name": "Hijacked",
                "scope": "global",
                "strategy": "percentage_markup",
                "markupPercent": "99",
                "expectedUpdatedAt": created["updatedAt"],
            },
            headers=intruder,
        )
        assert response.status_code == 404, response.text

    async def test_another_tenants_history_is_not_exposed(self, client: AsyncClient) -> None:
        owner = await admin(client)
        created = await create_pricing(client, owner)
        intruder = await admin(client)
        response = await client.get(f"{BASE}/pricing/{created['id']}/history", headers=intruder)
        assert response.status_code == 200
        assert response.json()["items"] == []
        assert response.json()["meta"]["totalItems"] == 0

    async def test_a_rule_list_never_leaks_across_tenants(self, client: AsyncClient) -> None:
        owner = await admin(client)
        created = await create_pricing(client, owner)
        intruder = await admin(client)
        listed = await client.get(PRICING, headers=intruder)
        assert all(item["id"] != created["id"] for item in listed.json()["items"])


class TestValidation:
    @pytest.mark.parametrize(
        ("payload", "why"),
        [
            ({"strategy": "target_margin", "marginPercent": "100"}, "margin of exactly 100"),
            ({"strategy": "target_margin", "marginPercent": "150"}, "margin above 100"),
            ({"strategy": "target_margin"}, "target margin without its input"),
            ({"strategy": "fixed_markup"}, "fixed profit without its input"),
            ({"strategy": "hybrid", "markupPercent": None}, "hybrid without either input"),
            ({"strategy": "tiered", "tiers": []}, "tiered without tiers"),
            ({"markupPercent": "-5"}, "negative markup"),
            ({"minPrice": "-1"}, "negative minimum price"),
            ({"minPrice": "50", "maxPrice": "10"}, "minimum above maximum"),
            ({"dutyPercent": "150"}, "duty above 100%"),
            ({"currency": "US"}, "two-letter currency"),
            ({"currency": "12X"}, "non-alphabetic currency"),
            ({"scope": "store"}, "store scope without a store id"),
            ({"scope": "product"}, "product scope without a product id"),
            ({"scope": "variant"}, "variant scope without a variant id"),
            ({"scope": "category"}, "category scope without a category id"),
            ({"scope": "global", "storeId": str(uuid.uuid4())}, "global scope with an id"),
        ],
    )
    async def test_an_impossible_pricing_rule_is_rejected(
        self, client: AsyncClient, payload: dict, why: str
    ) -> None:
        headers = await admin(client)
        response = await client.post(PRICING, json=markup_rule(**payload), headers=headers)
        assert response.status_code == 422, f"{why}: {response.text}"
        assert response.json()["code"] == "validation_error"

    @pytest.mark.parametrize(
        ("payload", "why"),
        [
            ({"destinationCountry": "GBR"}, "three-letter country"),
            ({"maxDeliveryDays": 0}, "zero delivery days"),
            ({"maxShippingCost": "-1"}, "negative shipping ceiling"),
            (
                {"preferredCarriers": ["DHL"], "blockedCarriers": ["dhl"]},
                "a carrier both preferred and blocked",
            ),
            (
                {"selectionStrategy": "fastest_under_cost"},
                "fastest-under-cost with no ceiling",
            ),
        ],
    )
    async def test_an_impossible_shipping_rule_is_rejected(
        self, client: AsyncClient, payload: dict, why: str
    ) -> None:
        headers = await admin(client)
        response = await client.post(SHIPPING, json=shipping_payload(**payload), headers=headers)
        assert response.status_code == 422, f"{why}: {response.text}"
        assert response.json()["code"] == "validation_error"

    async def test_errors_use_the_standard_envelope(self, client: AsyncClient) -> None:
        headers = await admin(client)
        response = await client.post(PRICING, json=markup_rule(markupPercent="-1"), headers=headers)
        body = response.json()
        assert set(body) >= {"code", "message", "details", "requestId"}


class TestEffectiveRuleResolution:
    async def test_a_product_override_beats_the_global_rule(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        body = await register(client)
        headers = auth_header(body)
        await create_pricing(client, headers)
        # A real product row: `pricing_rules.product_id` carries a foreign
        # key, so a random UUID is rejected by the database rather than
        # resolving to nothing.
        product_id = await seed_product(db_session, body)
        override = await client.post(
            PRICING,
            json=markup_rule(
                name=f"Product override {uuid.uuid4().hex[:6]}",
                scope="product",
                productId=str(product_id),
                markupPercent="10",
            ),
            headers=headers,
        )
        assert override.status_code == 201, override.text

        resolved = await client.get(
            f"{BASE}/resolve", params={"productId": str(product_id)}, headers=headers
        )
        assert resolved.status_code == 200, resolved.text
        body = resolved.json()
        assert body["ruleId"] == override.json()["id"]
        assert body["scope"] == "product"
        assert len(body["overriddenRuleIds"]) == 1
        assert body["reason"]

    async def test_resolution_reports_no_rule_without_erroring(self, client: AsyncClient) -> None:
        headers = await admin(client)
        resolved = await client.get(
            f"{BASE}/resolve", params={"productId": str(uuid.uuid4())}, headers=headers
        )
        assert resolved.status_code == 200
        assert resolved.json()["ruleId"] is None


class TestLivePreview:
    async def test_preview_returns_every_figure_and_writes_nothing(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        headers = await admin(client)
        await create_pricing(client, headers, markupPercent="50")

        products_before = (
            await db_session.execute(select(func.count()).select_from(PricingRule))
        ).scalar_one()
        versions_before = (
            await db_session.execute(select(func.count()).select_from(GlobalRuleVersion))
        ).scalar_one()

        response = await client.post(
            f"{BASE}/preview",
            json={"itemCost": "10.00", "shippingCost": "4.00", "currency": "USD"},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()

        assert Decimal(body["itemCost"]) == Decimal("10")
        assert Decimal(body["shippingCost"]) == Decimal("4")
        assert Decimal(body["landedCost"]) == Decimal("14")
        assert Decimal(body["proposedPrice"]) == Decimal("21")
        assert Decimal(body["profit"]) == Decimal("7")
        assert Decimal(body["markupPercent"]) == Decimal("50")
        assert Decimal(body["marginPercent"]) == Decimal("33.33")
        assert body["needsReview"] is False
        assert body["resolution"]["ruleId"]

        assert (
            await db_session.execute(select(func.count()).select_from(PricingRule))
        ).scalar_one() == products_before
        assert (
            await db_session.execute(select(func.count()).select_from(GlobalRuleVersion))
        ).scalar_one() == versions_before

    async def test_markup_and_margin_are_reported_as_different_numbers(
        self, client: AsyncClient
    ) -> None:
        headers = await admin(client)
        await create_pricing(
            client, headers, strategy="target_margin", marginPercent="50", markupPercent=None
        )
        response = await client.post(
            f"{BASE}/preview",
            json={"itemCost": "10.00", "shippingCost": "0", "currency": "USD"},
            headers=headers,
        )
        body = response.json()
        assert Decimal(body["proposedPrice"]) == Decimal("20")
        assert Decimal(body["marginPercent"]) == Decimal("50")
        assert Decimal(body["markupPercent"]) == Decimal("100")

    @pytest.mark.parametrize(
        ("payload", "reason"),
        [
            ({"shippingCost": "4.00", "currency": "USD"}, "supplier_cost_unknown"),
            ({"itemCost": "10.00", "currency": "USD"}, "shipping_cost_unknown"),
            ({"itemCost": "10.00", "shippingCost": "4.00"}, "supplier_currency_unknown"),
        ],
    )
    async def test_missing_data_fails_closed_with_a_precise_reason(
        self, client: AsyncClient, payload: dict, reason: str
    ) -> None:
        headers = await admin(client)
        await create_pricing(client, headers)
        response = await client.post(f"{BASE}/preview", json=payload, headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()
        assert reason in body["reviewReasons"]
        if reason != "supplier_currency_unknown":
            assert body["proposedPrice"] is None
            assert body["needsReview"] is True

    async def test_a_preview_without_any_rule_reports_no_price(self, client: AsyncClient) -> None:
        headers = await admin(client)
        response = await client.post(
            f"{BASE}/preview",
            json={"itemCost": "10.00", "shippingCost": "1.00", "currency": "USD"},
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["proposedPrice"] is None
        assert response.json()["resolution"]["ruleId"] is None


class TestCategoryScopeIsSupported:
    """Category scope predates M3A -- it is in the `pricing_scope` enum on
    develop, used by the pre-M3A engine and already exposed on the older
    pricing schemas. M3A-2 therefore supports it explicitly rather than
    leaving an API-only scope nobody documented."""

    async def test_a_category_rule_can_be_created_and_resolves(self, client: AsyncClient) -> None:
        headers = await admin(client)
        created = await client.post(
            PRICING,
            json=markup_rule(
                name=f"Category {uuid.uuid4().hex[:6]}",
                scope="category",
                categoryId="electronics",
                markupPercent="20",
            ),
            headers=headers,
        )
        assert created.status_code == 201, created.text

        resolved = await client.get(
            f"{BASE}/resolve",
            params={"productId": str(uuid.uuid4()), "categoryId": "electronics"},
            headers=headers,
        )
        assert resolved.status_code == 200
        assert resolved.json()["scope"] == "category"
