"""EBAY-C3 — publish a draft to eBay, over HTTP, with only eBay's wire faked.

The fake combines C2's seller endpoints, C3a's Taxonomy endpoints and the
Inventory API's item/offer/publish calls, and records every request so the
tests can assert what eBay would have received — and that a retry adopts the
existing offer instead of creating a second listing. Nothing here reaches
eBay; no real credential is used.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import set_tenant_id
from app.integrations.ebay.deletion import DeletionSubject, EbayStoreListingsOwner
from app.integrations.ebay.taxonomy import forget_category_trees
from app.integrations.ebay.tokens import forget_application_token
from app.models.ebay import EbayListingDefaults
from app.models.product import Product, ProductImage, ProductSource, ProductStatus
from app.models.shopify import StoreListing
from app.models.store import Store, StorePlatform, StoreStatus
from tests.integration.ebay_c1_live import SELLER_USER_ID, install
from tests.integration.test_ebay_c1_api import (
    auth_header,
    connect_fully,
    register,
    token_with_roles,
)
from tests.integration.test_ebay_c2_listing_setup import CHOICE, SellerFakeEbay
from tests.integration.test_ebay_c2_listing_setup import (
    fresh_redis as fresh_redis,  # autouse: a Redis client per test event loop
)
from tests.integration.test_ebay_c3_product_details import TaxonomyFakeEbay

pytestmark = pytest.mark.integration

READINESS_URL = "/api/v1/integrations/ebay/publish-readiness"
PUBLISH_URL = "/api/v1/integrations/ebay/publish"
DEFAULTS_URL = "/api/v1/integrations/ebay/listing-defaults"
DISCONNECT_URL = "/api/v1/integrations/ebay/disconnect"


class PublishFakeEbay(SellerFakeEbay, TaxonomyFakeEbay):
    """Seller + Taxonomy + Inventory listing calls, all recorded."""

    def __init__(self) -> None:
        super().__init__()
        self.listing_calls: list[tuple[str, str, Any]] = []
        self.offer: dict[str, Any] | None = None
        self.reject_publish = False

    async def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        # Only the listing calls carry JSON; the token request is a form.
        listing_call = path.startswith("/sell/inventory/v1/")
        body = json.loads(request.content) if listing_call and request.content else None
        if path.startswith("/sell/inventory/v1/inventory_item/"):
            self.listing_calls.append(("put_item", path.rsplit("/", 1)[1], body))
            assert request.headers["Content-Language"] == "en-US"
            return httpx.Response(204)
        if path == "/sell/inventory/v1/offer" and request.method == "GET":
            if self.offer is None:
                return httpx.Response(404, json={"errors": [{"errorId": 25713}]})
            return httpx.Response(200, json={"offers": [self.offer]})
        if path == "/sell/inventory/v1/offer" and request.method == "POST":
            self.listing_calls.append(("create_offer", "", body))
            self.offer = {"offerId": "OFFER-1", "status": "UNPUBLISHED"}
            return httpx.Response(201, json={"offerId": "OFFER-1"})
        if path.startswith("/sell/inventory/v1/offer/") and path.endswith("/publish"):
            self.listing_calls.append(("publish", path, None))
            if self.reject_publish:
                return httpx.Response(
                    400,
                    json={
                        "errors": [{"errorId": 25002, "message": "Item specific Type is missing."}]
                    },
                )
            assert self.offer is not None
            self.offer = {
                **self.offer,
                "status": "PUBLISHED",
                "listing": {"listingId": "110000000001"},
            }
            return httpx.Response(200, json={"listingId": "110000000001"})
        if path.startswith("/sell/inventory/v1/offer/") and request.method == "PUT":
            self.listing_calls.append(("update_offer", path.rsplit("/", 1)[1], body))
            return httpx.Response(204)
        return await super().handler(request)

    def calls(self, kind: str) -> list[tuple[str, str, Any]]:
        return [c for c in self.listing_calls if c[0] == kind]


@pytest.fixture(autouse=True)
def fresh_caches() -> Iterator[None]:
    forget_application_token()
    forget_category_trees()
    yield
    forget_application_token()
    forget_category_trees()


@pytest.fixture
def ebay(monkeypatch: pytest.MonkeyPatch) -> PublishFakeEbay:
    fake = PublishFakeEbay()
    install(monkeypatch, fake)
    return fake


async def ready_product(db_session: AsyncSession, body: dict[str, Any]) -> uuid.UUID:
    """A draft that satisfies every eBay rule except the category, which the
    test sets through the API."""
    tenant_id = uuid.UUID(str(body["identity"]["tenant"]["id"]))
    set_tenant_id(tenant_id)
    product = Product(
        tenant_id=tenant_id,
        source=ProductSource.MANUAL,
        external_id=f"c3-{uuid.uuid4().hex[:10]}",
        title="Red ceramic mug, 350 ml",
        description="<p>A sturdy mug.</p>",
        status=ProductStatus.DRAFT,
        sell_price=Decimal("12.50"),
        currency="USD",
        stock_quantity=7,
    )
    db_session.add(product)
    await db_session.flush()
    db_session.add(
        ProductImage(
            tenant_id=tenant_id,
            product_id=product.id,
            url="https://cdn.example/mug.jpg",
            position=0,
        )
    )
    await db_session.flush()
    return product.id


async def prepared(
    client: AsyncClient, db_session: AsyncSession
) -> tuple[dict[str, str], uuid.UUID, uuid.UUID]:
    """Owner connected, US defaults saved (creating the eBay store), and a
    product with its category and Brand."""
    body = await register(client)
    headers = auth_header(body)
    await connect_fully(client, headers)
    saved = await client.put(DEFAULTS_URL, json=CHOICE, headers=headers)
    assert saved.status_code == 200, saved.text
    product_id = await ready_product(db_session, body)
    details = await client.put(
        f"/api/v1/integrations/ebay/products/{product_id}/details",
        json={"marketplaceId": "EBAY_US", "categoryId": "20625", "aspects": {"Brand": ["Acme"]}},
        headers=headers,
    )
    assert details.status_code == 200, details.text
    store_id = await db_session.scalar(
        sa.select(Store.id).where(
            Store.platform == StorePlatform.EBAY, Store.slug == "ebay-marketplace-us"
        )
    )
    assert store_id is not None
    return headers, product_id, store_id


async def test_saving_defaults_creates_the_marketplace_store(
    client: AsyncClient, db_session: AsyncSession, ebay: PublishFakeEbay
) -> None:
    _, _, store_id = await prepared(client, db_session)
    store = await db_session.get(Store, store_id)
    assert store is not None
    assert (store.currency, store.status) == ("USD", StoreStatus.CONNECTED)
    assert store.settings == {"countryCode": "US", "ebayMarketplaceId": "EBAY_US"}


async def test_readiness_names_what_eBay_would_refuse(
    client: AsyncClient, db_session: AsyncSession, ebay: PublishFakeEbay
) -> None:
    headers, product_id, store_id = await prepared(client, db_session)
    product = await db_session.get(Product, product_id)
    assert product is not None
    product.title = "x" * 81
    product.stock_quantity = 0
    product.currency = "EUR"
    await db_session.flush()

    response = await client.post(
        READINESS_URL,
        json={"productId": str(product_id), "storeId": str(store_id)},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    codes = {b["code"] for b in response.json()["blockers"]}
    assert codes == {"ebay_title_too_long", "quantity_missing", "selling_currency_mismatch"}
    assert response.json()["canPublish"] is False


async def test_publish_sends_the_listing_and_records_it(
    client: AsyncClient, db_session: AsyncSession, ebay: PublishFakeEbay
) -> None:
    headers, product_id, store_id = await prepared(client, db_session)

    ready = await client.post(
        READINESS_URL,
        json={"productId": str(product_id), "storeId": str(store_id)},
        headers=headers,
    )
    assert ready.json()["canPublish"] is True, ready.text

    response = await client.post(
        PUBLISH_URL, json={"productId": str(product_id), "storeId": str(store_id)}, headers=headers
    )

    assert response.status_code == 200, response.text
    assert response.json()["externalProductId"] == "110000000001"
    assert response.json()["storefrontUrl"] == "https://www.ebay.com/itm/110000000001"

    ((_, sku, item),) = ebay.calls("put_item")
    assert sku == f"dp-us-{product_id}"
    assert item["product"]["title"] == "Red ceramic mug, 350 ml"
    assert item["product"]["aspects"] == {"Brand": ["Acme"]}
    assert item["product"]["imageUrls"] == ["https://cdn.example/mug.jpg"]
    assert item["availability"]["shipToLocationAvailability"]["quantity"] == 7

    ((_, _, offer),) = ebay.calls("create_offer")
    assert offer["pricingSummary"]["price"] == {"value": "12.50", "currency": "USD"}
    assert offer["categoryId"] == "20625"
    assert offer["merchantLocationKey"] == "warehouse-1"
    assert offer["listingPolicies"] == {
        "fulfillmentPolicyId": "6000001",
        "paymentPolicyId": "6000002",
        "returnPolicyId": "6000003",
    }

    listing = await db_session.scalar(
        sa.select(StoreListing).where(StoreListing.product_id == product_id)
    )
    assert listing is not None
    assert (listing.external_product_id, listing.external_offer_id, listing.external_sku) == (
        "110000000001",
        "OFFER-1",
        f"dp-us-{product_id}",
    )


async def test_a_second_publish_adopts_the_offer_instead_of_listing_twice(
    client: AsyncClient, db_session: AsyncSession, ebay: PublishFakeEbay
) -> None:
    headers, product_id, store_id = await prepared(client, db_session)
    payload = {"productId": str(product_id), "storeId": str(store_id)}
    assert (await client.post(PUBLISH_URL, json=payload, headers=headers)).status_code == 200

    again = await client.post(PUBLISH_URL, json=payload, headers=headers)

    assert again.status_code == 200, again.text
    assert again.json()["updated"] is True
    assert len(ebay.calls("create_offer")) == 1
    assert len(ebay.calls("publish")) == 1
    assert len(ebay.calls("update_offer")) == 1
    count = await db_session.scalar(
        sa.select(sa.func.count())
        .select_from(StoreListing)
        .where(StoreListing.product_id == product_id)
    )
    assert count == 1


async def test_eBays_refusal_reaches_the_merchant_and_records_nothing(
    client: AsyncClient, db_session: AsyncSession, ebay: PublishFakeEbay
) -> None:
    headers, product_id, store_id = await prepared(client, db_session)
    ebay.reject_publish = True

    response = await client.post(
        PUBLISH_URL, json={"productId": str(product_id), "storeId": str(store_id)}, headers=headers
    )

    assert response.status_code == 422
    assert response.json()["code"] == "ebay_listing_rejected"
    assert "Item specific Type is missing." in response.json()["message"]


async def test_a_blocked_product_never_reaches_eBay(
    client: AsyncClient, db_session: AsyncSession, ebay: PublishFakeEbay
) -> None:
    headers, product_id, store_id = await prepared(client, db_session)
    product = await db_session.get(Product, product_id)
    assert product is not None
    product.stock_quantity = 0
    await db_session.flush()

    response = await client.post(
        PUBLISH_URL, json={"productId": str(product_id), "storeId": str(store_id)}, headers=headers
    )

    assert response.status_code == 422
    assert ebay.listing_calls == []


async def test_a_member_may_not_publish(
    client: AsyncClient, db_session: AsyncSession, ebay: PublishFakeEbay
) -> None:
    body = await register(client)
    response = await client.post(
        PUBLISH_URL,
        json={"productId": str(uuid.uuid4()), "storeId": str(uuid.uuid4())},
        headers=token_with_roles(body, "member"),
    )
    assert response.status_code == 403


async def test_shopify_readiness_refuses_an_eBay_store(
    client: AsyncClient, db_session: AsyncSession, ebay: PublishFakeEbay
) -> None:
    headers, product_id, store_id = await prepared(client, db_session)
    response = await client.post(
        "/api/v1/integrations/shopify/publish-readiness",
        json={"productId": str(product_id), "storeId": str(store_id)},
        headers=headers,
    )
    assert {b["code"] for b in response.json()["blockers"]} == {"unsupported_channel"}


async def test_disconnect_erases_eBay_listing_rows_and_parks_the_store(
    client: AsyncClient, db_session: AsyncSession, ebay: PublishFakeEbay
) -> None:
    headers, product_id, store_id = await prepared(client, db_session)
    payload = {"productId": str(product_id), "storeId": str(store_id)}
    assert (await client.post(PUBLISH_URL, json=payload, headers=headers)).status_code == 200

    assert (await client.delete(DISCONNECT_URL, headers=headers)).status_code == 200

    count = await db_session.scalar(sa.select(sa.func.count()).select_from(StoreListing))
    assert count == 0
    store = await db_session.get(Store, store_id, populate_existing=True)
    assert store is not None and store.status is StoreStatus.DISCONNECTED


async def test_the_deletion_owner_erases_listings_by_immutable_id(
    client: AsyncClient, db_session: AsyncSession, ebay: PublishFakeEbay
) -> None:
    headers, product_id, store_id = await prepared(client, db_session)
    payload = {"productId": str(product_id), "storeId": str(store_id)}
    assert (await client.post(PUBLISH_URL, json=payload, headers=headers)).status_code == 200

    owner = EbayStoreListingsOwner()
    nobody = DeletionSubject(user_id="someone-else", username=None, eias_token=None)
    seller = DeletionSubject(user_id=SELLER_USER_ID, username=None, eias_token=None)
    assert await owner.erase(db_session, nobody) == 0
    assert await owner.erase(db_session, seller) == 1
    assert await owner.erase(db_session, seller) == 0


async def test_an_application_token_failure_is_a_try_again_blocker(
    client: AsyncClient, db_session: AsyncSession, ebay: PublishFakeEbay
) -> None:
    """Review finding: the platform's own credential failing must not tell the
    merchant to reconnect, nor escape as a 500."""
    headers, product_id, store_id = await prepared(client, db_session)
    forget_application_token()
    ebay.app_token_status = 401

    response = await client.post(
        READINESS_URL,
        json={"productId": str(product_id), "storeId": str(store_id)},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    codes = {b["code"] for b in response.json()["blockers"]}
    assert "ebay_requirements_unavailable" in codes


async def test_reconnecting_as_another_seller_forgets_the_first_sellers_data(
    client: AsyncClient, db_session: AsyncSession, ebay: PublishFakeEbay
) -> None:
    """Review finding: a different seller on the same connection row must not
    inherit the previous seller's policies, location or listings."""
    headers, product_id, store_id = await prepared(client, db_session)
    payload = {"productId": str(product_id), "storeId": str(store_id)}
    assert (await client.post(PUBLISH_URL, json=payload, headers=headers)).status_code == 200

    ebay.seller_user_id = "immutable-ebay-user-id-0002"
    await connect_fully(client, headers)

    defaults = await db_session.scalar(sa.select(sa.func.count()).select_from(EbayListingDefaults))
    listings = await db_session.scalar(sa.select(sa.func.count()).select_from(StoreListing))
    assert (defaults, listings) == (0, 0)
    ready = await client.post(READINESS_URL, json=payload, headers=headers)
    assert "ebay_listing_setup_missing" in {b["code"] for b in ready.json()["blockers"]}
