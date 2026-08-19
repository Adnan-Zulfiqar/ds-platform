"""M3A-3 acceptance fix — global rules applied on the real import path.

These drive ``POST /api/v1/products/import`` end to end: the HTTP pipeline,
``ProductImportService.import_product``, the tenant-scoped repositories and a
migration-built database. Only the AliExpress network boundary is replaced,
with the same captured real payload ``test_products.py`` uses. Nothing here
calls ``calculate_for``, the preview or the apply endpoint -- the point is to
prove the *import* wires pricing, which the M3A-3 suite could not show.

**On shipping cost.** ``mapper.map_product`` returns ``shipping_cost: None``
because ``aliexpress.ds.product.get`` carries no freight quote, and the
calculation refuses to invent one. That is tested directly, unstubbed, in
:class:`TestAliExpressReportsNoShipping`. It also means no import through the
real mapper can ever produce a price, so the tests that need a *priced* import
stub ``map_product`` to add a freight figure -- simulating the supplier
capability a freight integration will provide, and changing nothing else about
the import. Where that stub is used it is named ``freight``.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from fakeredis import aioredis as fake_aioredis
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import set_tenant_id
from app.integrations.aliexpress import service as service_module
from app.models.pricing import PriceChange, PricingRule, PricingScope, PricingStrategy
from app.models.product import Product, ProductImport, ProductStatus, ProductVariant
from app.models.rule_application import RuleApplication
from app.models.shopify import StoreListing
from app.models.store import Store, StorePlatform
from app.services import product_import as import_module
from tests.integration.test_products import REAL_PRODUCT_ID, connected_tenant

pytestmark = pytest.mark.integration

IMPORT_URL = "/api/v1/products/import"
RULES = "/api/v1/global-rules"

#: From the captured payload: `cost_price_min` 3.14, twelve SKUs, USD.
FIXTURE_COST = Decimal("3.14")
FIXTURE_SHIPPING = Decimal("2.00")


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> fake_aioredis.FakeRedis:
    """In-process Redis for the OAuth state store.

    Replicated rather than imported: an `autouse` fixture has to live in the
    module pytest is collecting.
    """
    redis = fake_aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(service_module, "get_redis", lambda _purpose: redis)
    return redis


@pytest.fixture(autouse=True)
def _allow_outbound(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bypass the outbound rate limiter, which has its own tests."""
    from app.integrations.rate_limiter import RateLimitDecision

    async def _allow(self: Any, tenant_id: str) -> RateLimitDecision:
        return RateLimitDecision(allowed=True, remaining=99, retry_after_seconds=0)

    monkeypatch.setattr("app.integrations.rate_limiter.OutboundRateLimiter.acquire", _allow)


def freight(
    monkeypatch: pytest.MonkeyPatch,
    *,
    shipping: Decimal | None = FIXTURE_SHIPPING,
    cost: Decimal | None = FIXTURE_COST,
    currency: str | None = "USD",
) -> None:
    """Stand in for a supplier that reports freight.

    Wraps the real mapper and overrides only the three supplier figures the
    calculation reads, so everything else about the import -- parsing,
    variants, images, the upsert -- stays genuine. `None` is passed through
    deliberately: the fail-closed tests need a supplier that reports nothing.
    """
    original = import_module.map_product

    def mapped(detail: Any) -> dict[str, Any]:
        values = original(detail)
        values["shipping_cost"] = shipping
        values["cost_price_min"] = cost
        values["currency"] = currency
        return values

    monkeypatch.setattr(import_module, "map_product", mapped)


async def tenant_with_rule(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    **rule_overrides: object,
) -> tuple[dict[str, str], dict[str, Any]]:
    headers = await connected_tenant(client, monkeypatch)
    rule = await create_rule(client, headers, **rule_overrides)
    return headers, rule


async def create_rule(
    client: AsyncClient, headers: dict[str, str], **overrides: object
) -> dict[str, Any]:
    payload: dict[str, object] = {
        "name": f"Rule {uuid.uuid4().hex[:6]}",
        "scope": "global",
        "strategy": "percentage_markup",
        "markupPercent": "50",
    }
    payload.update(overrides)
    response = await client.post(f"{RULES}/pricing", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    result: dict[str, Any] = response.json()
    return result


async def import_product(client: AsyncClient, headers: dict[str, str]) -> dict[str, Any]:
    response = await client.post(IMPORT_URL, json={"externalId": REAL_PRODUCT_ID}, headers=headers)
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def load(db_session: AsyncSession, product_id: str) -> Product:
    product = (
        await db_session.execute(select(Product).where(Product.id == uuid.UUID(product_id)))
    ).scalar_one()
    await db_session.refresh(product)
    return product


async def variants_of(db_session: AsyncSession, product_id: str) -> list[ProductVariant]:
    rows = (
        await db_session.execute(
            select(ProductVariant)
            .where(ProductVariant.product_id == uuid.UUID(product_id))
            .where(ProductVariant.deleted_at.is_(None))
            .order_by(ProductVariant.external_variant_id)
        )
    ).scalars()
    return list(rows)


class TestNoRuleConfigured:
    """A tenant that has not opted in must see exactly pre-M3A behaviour."""

    async def test_an_import_with_no_rule_prices_nothing(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        freight(monkeypatch)
        headers = await connected_tenant(client, monkeypatch)

        imported = await import_product(client, headers)
        product = await load(db_session, imported["id"])

        assert product.sell_price is None
        assert product.applied_pricing_rule_id is None
        assert product.pricing_calculated_at is None

    async def test_an_import_with_no_rule_raises_no_review_flags(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The absence of a rule is not a problem to report."""
        freight(monkeypatch)
        headers = await connected_tenant(client, monkeypatch)

        product = await load(db_session, (await import_product(client, headers))["id"])

        assert product.needs_review is False
        assert product.pricing_review_reasons == []

    async def test_a_rule_that_opts_out_of_new_imports_changes_nothing(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        freight(monkeypatch)
        headers, _ = await tenant_with_rule(client, monkeypatch, appliesToNewImports=False)

        product = await load(db_session, (await import_product(client, headers))["id"])

        assert product.sell_price is None
        assert product.applied_pricing_rule_id is None
        assert product.needs_review is False
        assert product.pricing_review_reasons == []


class TestAliExpressReportsNoShipping:
    """The real mapper, unstubbed. This is today's actual import behaviour."""

    async def test_a_missing_freight_quote_produces_shipping_cost_unknown(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`ds.product.get` carries no freight, so `map_product` returns None
        and the calculation refuses rather than assuming zero."""
        headers, _ = await tenant_with_rule(client, monkeypatch)

        product = await load(db_session, (await import_product(client, headers))["id"])

        assert product.shipping_cost is None, "the mapper must not invent a quote"
        assert product.needs_review is True
        assert product.pricing_review_reasons == ["shipping_cost_unknown"]

    async def test_no_price_is_invented_from_an_unknown_shipping_cost(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers, _ = await tenant_with_rule(client, monkeypatch)

        product = await load(db_session, (await import_product(client, headers))["id"])

        assert product.sell_price is None
        assert all(v.sell_price is None for v in await variants_of(db_session, str(product.id)))

    async def test_the_evidence_is_still_recorded(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ "Why is this draft unpriced" must be answerable from the row."""
        headers, rule = await tenant_with_rule(client, monkeypatch)

        product = await load(db_session, (await import_product(client, headers))["id"])

        assert str(product.applied_pricing_rule_id) == rule["id"]
        assert product.applied_pricing_rule_version == 1
        assert product.pricing_calculated_at is not None

    async def test_the_draft_is_still_created_and_the_import_succeeds(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A flagged draft is worth more than no draft."""
        headers, _ = await tenant_with_rule(client, monkeypatch)

        product = await load(db_session, (await import_product(client, headers))["id"])

        record = (
            await db_session.execute(
                select(ProductImport).where(ProductImport.product_id == product.id)
            )
        ).scalar_one()
        assert record.status.value == "succeeded"
        assert product.status is ProductStatus.DRAFT


class TestRuleAppliesToANewImport:
    async def test_an_active_global_rule_prices_the_import(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        freight(monkeypatch)
        headers, _ = await tenant_with_rule(client, monkeypatch)

        product = await load(db_session, (await import_product(client, headers))["id"])

        # Variants carry their own costs, so the product price is the lowest
        # of theirs, not (3.14 + 2.00) * 1.5.
        assert product.sell_price is not None
        assert product.needs_review is False

    async def test_the_applied_rule_id_and_version_are_persisted(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        freight(monkeypatch)
        headers, rule = await tenant_with_rule(client, monkeypatch)

        product = await load(db_session, (await import_product(client, headers))["id"])

        assert str(product.applied_pricing_rule_id) == rule["id"]
        assert product.applied_pricing_rule_version == 1

    async def test_the_landed_cost_components_are_persisted(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        freight(monkeypatch)
        headers, _ = await tenant_with_rule(client, monkeypatch, feesFixed="1.00")

        product = await load(db_session, (await import_product(client, headers))["id"])

        # 3.14 + 2.00 + 1.00 fixed fee
        assert product.landed_cost == Decimal("6.1400")
        assert product.landed_cost_fees == Decimal("1.0000")
        assert product.pricing_calculated_at is not None

    async def test_each_variant_is_priced_from_its_own_supplier_cost(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        freight(monkeypatch)
        headers, _ = await tenant_with_rule(client, monkeypatch)

        imported = await import_product(client, headers)
        variants = await variants_of(db_session, imported["id"])

        assert len(variants) == 12
        assert all(v.sell_price is not None for v in variants)
        for variant in variants:
            assert variant.cost_price is not None
            expected = (variant.cost_price + FIXTURE_SHIPPING) * Decimal("1.5")
            assert variant.sell_price == expected.quantize(Decimal("0.0001"))
        # Distinct supplier costs must not collapse to one price.
        assert len({v.sell_price for v in variants}) > 1

    async def test_the_product_price_is_the_lowest_variant_price(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        freight(monkeypatch)
        headers, _ = await tenant_with_rule(client, monkeypatch)

        imported = await import_product(client, headers)
        product = await load(db_session, imported["id"])
        variants = await variants_of(db_session, imported["id"])

        assert product.sell_price == min(v.sell_price for v in variants if v.sell_price is not None)

    async def test_the_draft_is_not_published_by_pricing(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        freight(monkeypatch)
        headers, _ = await tenant_with_rule(client, monkeypatch)

        product = await load(db_session, (await import_product(client, headers))["id"])

        assert product.status is ProductStatus.DRAFT
        listings = (
            await db_session.execute(
                select(func.count())
                .select_from(StoreListing)
                .where(StoreListing.product_id == product.id)
            )
        ).scalar_one()
        assert listings == 0

    async def test_supplier_snapshots_survive_pricing_unchanged(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pricing writes `sell_price`, never the supplier's own figures."""
        freight(monkeypatch)
        headers = await connected_tenant(client, monkeypatch)

        # Import once with no rule to capture the untouched snapshot...
        before = await load(db_session, (await import_product(client, headers))["id"])
        snapshot = {
            "supplier_title": before.supplier_title,
            "supplier_description": before.supplier_description,
            "supplier_brand": before.supplier_brand,
            "supplier_native_currency": before.supplier_native_currency,
            "cost_price_min": before.cost_price_min,
            "external_id": before.external_id,
        }

        # ...then again with a rule active.
        await create_rule(client, headers)
        after = await load(db_session, (await import_product(client, headers))["id"])

        assert after.sell_price is not None, "the rule did price it"
        for field, value in snapshot.items():
            assert getattr(after, field) == value, field


class TestScopeHierarchyAtImport:
    """Narrowest scope wins, resolved at import exactly as in preview.

    Product- and variant-scoped rules need ids that only exist once a draft
    does, so each of these imports, adds the narrower rule, then re-imports --
    which is also the refresh path a merchant actually hits.
    """

    async def test_a_product_scoped_rule_beats_the_global_rule(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        freight(monkeypatch)
        headers, _ = await tenant_with_rule(client, monkeypatch, markupPercent="50")
        imported = await import_product(client, headers)

        await create_rule(
            client,
            headers,
            scope="product",
            productId=imported["id"],
            markupPercent="100",
        )
        await import_product(client, headers)

        variants = await variants_of(db_session, imported["id"])
        cheapest = min(v.cost_price for v in variants if v.cost_price is not None)
        expected = (cheapest + FIXTURE_SHIPPING) * Decimal("2")
        product = await load(db_session, imported["id"])
        assert product.sell_price == expected.quantize(Decimal("0.0001"))

    async def test_a_variant_scoped_rule_wins_for_that_variant_only(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        freight(monkeypatch)
        headers, _ = await tenant_with_rule(client, monkeypatch, markupPercent="50")
        imported = await import_product(client, headers)
        variants = await variants_of(db_session, imported["id"])
        target = variants[0]

        await create_rule(
            client,
            headers,
            scope="variant",
            variantId=str(target.id),
            markupPercent="300",
        )
        await import_product(client, headers)

        refreshed = {v.id: v for v in await variants_of(db_session, imported["id"])}
        assert target.cost_price is not None
        assert refreshed[target.id].sell_price == (
            (target.cost_price + FIXTURE_SHIPPING) * Decimal("4")
        ).quantize(Decimal("0.0001"))
        for other in variants[1:]:
            assert other.cost_price is not None
            assert refreshed[other.id].sell_price == (
                (other.cost_price + FIXTURE_SHIPPING) * Decimal("1.5")
            ).quantize(Decimal("0.0001"))

    async def test_a_category_scoped_rule_beats_the_global_rule(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The fixture product carries category 380230."""
        freight(monkeypatch)
        headers, _ = await tenant_with_rule(client, monkeypatch, markupPercent="50")
        category_rule = await create_rule(
            client, headers, scope="category", categoryId="380230", markupPercent="200"
        )

        imported = await import_product(client, headers)
        product = await load(db_session, imported["id"])

        assert product.category_id == "380230"
        assert str(product.applied_pricing_rule_id) == category_rule["id"]

    async def test_a_store_scoped_rule_beats_the_global_rule(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        freight(monkeypatch)
        headers, _ = await tenant_with_rule(client, monkeypatch, markupPercent="50")
        imported = await import_product(client, headers)
        product = await load(db_session, imported["id"])

        store = Store(
            tenant_id=product.tenant_id,
            name="Rule store",
            slug=f"store-{uuid.uuid4().hex[:8]}",
            platform=StorePlatform.SHOPIFY,
            currency="USD",
        )
        db_session.add(store)
        await db_session.flush()
        product.store_id = store.id
        await db_session.flush()

        store_rule = await create_rule(
            client, headers, scope="store", storeId=str(store.id), markupPercent="80"
        )
        await import_product(client, headers)

        refreshed = await load(db_session, imported["id"])
        assert str(refreshed.applied_pricing_rule_id) == store_rule["id"]

    async def test_a_shipping_rule_is_recorded_on_the_draft(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Which shipping rule governed is evidence, even when it could not
        choose a method -- AliExpress supplies no quotes to choose from."""
        freight(monkeypatch)
        headers, _ = await tenant_with_rule(client, monkeypatch)
        response = await client.post(
            f"{RULES}/shipping",
            json={
                "name": "Global shipping",
                "scope": "global",
                "selectionStrategy": "cheapest",
                "destinationCountry": "US",
            },
            headers=headers,
        )
        assert response.status_code == 201, response.text
        shipping_rule = response.json()

        product = await load(db_session, (await import_product(client, headers))["id"])

        assert str(product.applied_shipping_rule_id) == shipping_rule["id"]
        assert product.applied_shipping_rule_version == 1
        assert "no_shipping_quotes_available" in product.pricing_review_reasons


class TestRepeatedImport:
    async def test_a_repeated_import_updates_one_product(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Existing idempotency is untouched by pricing."""
        freight(monkeypatch)
        headers, _ = await tenant_with_rule(client, monkeypatch)

        first = await import_product(client, headers)
        second = await import_product(client, headers)

        assert first["id"] == second["id"]
        count = (
            await db_session.execute(
                select(func.count())
                .select_from(Product)
                .where(Product.external_id == REAL_PRODUCT_ID)
            )
        ).scalar_one()
        assert count == 1

    async def test_a_repeated_import_does_not_duplicate_the_pricing_outcome(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        freight(monkeypatch)
        headers, _ = await tenant_with_rule(client, monkeypatch)

        imported = await import_product(client, headers)
        once = await load(db_session, imported["id"])
        price_after_first = once.sell_price
        reasons_after_first = list(once.pricing_review_reasons)

        await import_product(client, headers)
        twice = await load(db_session, imported["id"])

        assert twice.sell_price == price_after_first
        assert twice.pricing_review_reasons == reasons_after_first
        assert twice.applied_pricing_rule_version == 1

    async def test_a_repeated_import_creates_no_application_or_price_change_rows(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Import pricing is not a bulk application and writes no audit run.

        A ``RuleApplication`` records a *confirmed* bulk reprice. If import
        created one, every refresh would add a run to the merchant's history
        that they never asked for.
        """
        freight(monkeypatch)
        headers, _ = await tenant_with_rule(client, monkeypatch)

        await import_product(client, headers)
        await import_product(client, headers)

        for model in (RuleApplication, PriceChange):
            count = (await db_session.execute(select(func.count()).select_from(model))).scalar_one()
            assert count == 0, model.__name__

    async def test_a_repeated_import_does_not_reprice_a_published_product(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        freight(monkeypatch)
        headers = await connected_tenant(client, monkeypatch)
        imported = await import_product(client, headers)
        product = await load(db_session, imported["id"])

        store = Store(
            tenant_id=product.tenant_id,
            name="Live store",
            slug=f"store-{uuid.uuid4().hex[:8]}",
            platform=StorePlatform.SHOPIFY,
            currency="USD",
        )
        db_session.add(store)
        await db_session.flush()
        from app.models.shopify import ListingSyncStatus

        db_session.add(
            StoreListing(
                tenant_id=product.tenant_id,
                store_id=store.id,
                product_id=product.id,
                external_product_id="gid://shopify/Product/9",
                status=ListingSyncStatus.SYNCED,
            )
        )
        await db_session.flush()
        before = product.sell_price

        await create_rule(client, headers)
        await import_product(client, headers)

        refreshed = await load(db_session, imported["id"])
        assert refreshed.sell_price == before


class TestFailClosedAtImport:
    async def test_a_missing_supplier_item_cost_is_flagged(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        freight(monkeypatch, cost=None)
        headers, _ = await tenant_with_rule(client, monkeypatch)

        product = await load(db_session, (await import_product(client, headers))["id"])

        assert product.sell_price is None
        assert product.needs_review is True
        assert "supplier_cost_unknown" in product.pricing_review_reasons

    async def test_a_missing_supplier_currency_is_flagged(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        freight(monkeypatch, currency=None)
        headers, _ = await tenant_with_rule(client, monkeypatch)

        product = await load(db_session, (await import_product(client, headers))["id"])

        assert product.needs_review is True
        assert "supplier_currency_unknown" in product.pricing_review_reasons

    async def test_a_rule_in_another_currency_fails_closed(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No FX rate is available to this path, so it refuses to price
        rather than apply a GBP floor to a USD cost."""
        freight(monkeypatch, currency="USD")
        headers, _ = await tenant_with_rule(client, monkeypatch, currency="GBP")

        product = await load(db_session, (await import_product(client, headers))["id"])

        assert product.sell_price is None
        assert "fx_rate_unavailable" in product.pricing_review_reasons

    async def test_a_rule_in_the_same_currency_prices_normally(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The currency guard must not flag the ordinary matched case."""
        freight(monkeypatch, currency="USD")
        headers, _ = await tenant_with_rule(client, monkeypatch, currency="USD")

        product = await load(db_session, (await import_product(client, headers))["id"])

        assert product.sell_price is not None
        assert "fx_rate_unavailable" not in product.pricing_review_reasons

    async def test_no_acceptable_shipping_quote_is_flagged(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        freight(monkeypatch)
        headers, _ = await tenant_with_rule(client, monkeypatch)
        response = await client.post(
            f"{RULES}/shipping",
            json={
                "name": "Tracked only",
                "scope": "global",
                "selectionStrategy": "cheapest",
                "destinationCountry": "US",
                "trackingRequired": True,
            },
            headers=headers,
        )
        assert response.status_code == 201, response.text

        product = await load(db_session, (await import_product(client, headers))["id"])

        assert product.sell_price is None
        assert "no_shipping_quotes_available" in product.pricing_review_reasons

    async def test_an_unevaluatable_rule_flags_instead_of_failing_the_import(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A percentage rule with no percentage cannot be evaluated.

        Written directly rather than through the API, which rejects it -- the
        point is that a row in this state (a backfill, an older writer) leaves
        the merchant with a flagged draft, not a failed import.
        """
        freight(monkeypatch)
        headers = await connected_tenant(client, monkeypatch)
        me = await client.get("/api/v1/auth/me", headers=headers)
        tenant_id = uuid.UUID(str(me.json()["tenant"]["id"]))
        set_tenant_id(tenant_id)

        db_session.add(
            PricingRule(
                tenant_id=tenant_id,
                name="Broken",
                scope=PricingScope.GLOBAL,
                strategy=PricingStrategy.PERCENTAGE_MARKUP,
                markup_percent=None,
                is_active=True,
                applies_to_new_imports=True,
            )
        )
        await db_session.flush()

        imported = await import_product(client, headers)
        product = await load(db_session, imported["id"])

        assert product.sell_price is None
        assert product.needs_review is True
        assert "pricing_calculation_failed" in product.pricing_review_reasons

    async def test_one_unpriceable_variant_does_not_disappear(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The other variants still price; the costless one stays visible.

        The missing cost comes from the supplier payload rather than being
        written onto the row afterwards -- a re-import would simply restore
        anything written directly, so that would have tested nothing.
        """
        freight(monkeypatch)
        original = import_module.map_variants

        def one_without_cost(detail: Any) -> list[dict[str, Any]]:
            variants = original(detail)
            variants[0]["cost_price"] = None
            return variants

        monkeypatch.setattr(import_module, "map_variants", one_without_cost)
        headers, _ = await tenant_with_rule(client, monkeypatch)

        imported = await import_product(client, headers)
        variants = await variants_of(db_session, imported["id"])

        assert len(variants) == 12, "the costless variant is still imported"
        costless = [v for v in variants if v.cost_price is None]
        assert len(costless) == 1

        # Fail-closed, and deliberately all-or-nothing: the product's price is
        # the *lowest* variant price, so pricing eleven of twelve would
        # advertise a "from" price derived from an incomplete set -- and the
        # missing one could be the cheapest. Nothing is priced, and the reason
        # is carried on the product where the merchant will see it.
        product = await load(db_session, imported["id"])
        assert product.sell_price is None
        assert all(v.sell_price is None for v in variants)
        assert product.needs_review is True
        assert "supplier_cost_unknown" in product.pricing_review_reasons

    async def test_a_flagged_import_still_creates_the_draft(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        freight(monkeypatch, cost=None)
        headers, _ = await tenant_with_rule(client, monkeypatch)

        imported = await import_product(client, headers)
        product = await load(db_session, imported["id"])

        assert product.id is not None
        assert product.status is ProductStatus.DRAFT
        assert len(await variants_of(db_session, imported["id"])) == 12

    async def test_a_failed_calculation_never_publishes(
        self, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        freight(monkeypatch, cost=None)
        headers, _ = await tenant_with_rule(client, monkeypatch)

        product = await load(db_session, (await import_product(client, headers))["id"])

        assert product.status is ProductStatus.DRAFT
        listings = (
            await db_session.execute(
                select(func.count())
                .select_from(StoreListing)
                .where(StoreListing.product_id == product.id)
            )
        ).scalar_one()
        assert listings == 0
