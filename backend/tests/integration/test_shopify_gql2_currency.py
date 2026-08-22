"""GQL-2 — the shop-currency authority, now read over the shared client.

``Store.currency`` is what every price in the catalogue is expressed in, so the
rules that mattered before the migration matter unchanged after it: the code
comes from ``shop.currencyCode`` and nowhere else, a store is never silently
assumed to sell in USD, and a failed refresh leaves a previously trusted
currency alone rather than clearing it.

These drive the real ``ShopifyService`` against a real database, with only
Shopify's wire faked — see ``tests/integration/shopify_gql2_live``.

No Shopify credential is used and no live request is made.
"""

from __future__ import annotations

import httpx
import pytest
import sqlalchemy as sa

from app.core.exceptions import ShopifyCurrencyRefreshError, ValidationError
from app.integrations.shopify import service as shopify_service_module
from app.integrations.shopify.exceptions import ShopifyNotConnectedError
from app.integrations.shopify.service import ShopifyService
from app.models.store import Store
from tests.integration.shopify_gql2_live import (
    CountingShopify,
    LiveStore,
    live_stores,
    own_connection,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def shopify(monkeypatch: pytest.MonkeyPatch) -> CountingShopify:
    double = CountingShopify()
    monkeypatch.setattr(shopify_service_module, "ShopifyGraphQLClient", double.client)
    return double


async def stored_currency(live: LiveStore) -> tuple[str | None, object]:
    async with live.factory() as session:
        row = (
            await session.execute(
                sa.select(Store.currency, Store.currency_last_synced_at).where(
                    Store.id == live.store_id
                )
            )
        ).one()
    return row[0], row[1]


class TestCurrencyRefresh:
    async def test_the_shop_currency_is_read_over_graphql_and_persisted(
        self, shopify: CountingShopify
    ) -> None:
        shopify.currency = "GBP"
        async with live_stores() as (live,):
            async with own_connection(live) as session:
                store = await ShopifyService(session).refresh_shop_currency(live.store_id)
                await session.commit()
                assert store.currency == "GBP"
            currency, synced_at = await stored_currency(live)

        assert currency == "GBP"
        assert synced_at is not None
        assert shopify.currency_reads == 1
        assert shopify.operations == ["ShopAuthority"]
        assert shopify.paths == ["/admin/api/2026-07/graphql.json"]

    async def test_a_lowercase_code_is_normalised_by_the_money_layer(
        self, shopify: CountingShopify
    ) -> None:
        shopify.currency = "eur"
        async with live_stores() as (live,):
            async with own_connection(live) as session:
                await ShopifyService(session).refresh_shop_currency(live.store_id)
                await session.commit()
            currency, _ = await stored_currency(live)
        assert currency == "EUR"

    async def test_a_refresh_is_repeatable(self, shopify: CountingShopify) -> None:
        async with live_stores() as (live,):
            async with own_connection(live) as session:
                service = ShopifyService(session)
                await service.refresh_shop_currency(live.store_id)
                await session.commit()
            shopify.currency = "USD"
            async with own_connection(live) as session:
                await ShopifyService(session).refresh_shop_currency(live.store_id)
                await session.commit()
            currency, _ = await stored_currency(live)
        assert currency == "USD"
        assert shopify.currency_reads == 2


class TestFailureNeverInvents:
    @pytest.mark.parametrize("code", [None, "", "GB", "GBPP"])
    async def test_an_unusable_currency_never_becomes_a_trusted_one(
        self, shopify: CountingShopify, code: object
    ) -> None:
        """Never a guess, and never promoted to an authority.

        ``Store.currency`` carries a column default of ``USD``; what makes it
        *trusted* is ``currency_last_synced_at``, which is set only after a
        successful ``shop.currencyCode`` read. So the invariant to assert is not
        that the string changed — it is that the timestamp stayed NULL, leaving
        the value where the pricing layer still refuses to price against it. A
        store silently promoted to "verified USD" would misprice a whole
        catalogue, and the mistake would stay invisible until a customer was
        charged.
        """
        shopify.currency = code
        async with live_stores() as (live,):
            before_currency, before_synced = await stored_currency(live)
            assert before_synced is None, "a fresh store starts untrusted"

            async with own_connection(live) as session:
                with pytest.raises((ShopifyCurrencyRefreshError, ValidationError)):
                    await ShopifyService(session).refresh_shop_currency(live.store_id)
                await session.rollback()
            currency, synced_at = await stored_currency(live)

        assert synced_at is None, "an unusable response must not stamp a trusted currency"
        assert currency == before_currency, "and must not rewrite the stored value either"

    async def test_a_failed_refresh_retains_a_previously_trusted_currency(
        self, shopify: CountingShopify
    ) -> None:
        """Clearing it would break pricing for a store that was already fine."""
        shopify.currency = "GBP"
        async with live_stores() as (live,):
            async with own_connection(live) as session:
                await ShopifyService(session).refresh_shop_currency(live.store_id)
                await session.commit()
            before_currency, before_synced = await stored_currency(live)
            assert before_currency == "GBP"

            shopify.fail_currency_with = httpx.ReadTimeout(
                "shopify went quiet", request=httpx.Request("POST", "https://x.test")
            )
            async with own_connection(live) as session:
                with pytest.raises(ShopifyCurrencyRefreshError) as raised:
                    await ShopifyService(session).refresh_shop_currency(live.store_id)
                await session.rollback()

            after_currency, after_synced = await stored_currency(live)

        assert after_currency == before_currency
        assert after_synced == before_synced
        assert raised.value.details["had_trusted_currency"] is True
        assert raised.value.details["retained_currency"] == "GBP"


class TestTenantIsolation:
    async def test_a_foreign_store_is_not_found_and_makes_no_request(
        self, shopify: CountingShopify
    ) -> None:
        async with live_stores(2) as (owner, other):
            async with own_connection(other) as session:
                with pytest.raises((ShopifyNotConnectedError, Exception)) as raised:
                    await ShopifyService(session).refresh_shop_currency(owner.store_id)
                # Whatever the typed failure, it must never be a 403: confirming
                # the store exists is how another tenant's ids get enumerated.
                assert getattr(raised.value, "status_code", 404) != 403

        assert shopify.currency_reads == 0
        assert shopify.paths == []
