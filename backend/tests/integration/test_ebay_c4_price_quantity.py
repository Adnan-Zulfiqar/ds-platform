"""EBAY-C4 — price and quantity reach a published eBay listing.

Builds on the C3 publish harness: a product is published to the fake eBay,
then its price or stock changes and the push runs through the manual
endpoint (the same service the background task calls). Nothing reaches eBay.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product import Product
from app.models.shopify import ListingSyncStatus, StoreListing
from tests.integration.ebay_c1_live import install
from tests.integration.test_ebay_c3_publish import PUBLISH_URL, PublishFakeEbay, prepared
from tests.integration.test_ebay_c3_publish import (
    fresh_caches as fresh_caches,  # autouse fixture, re-exported so it runs here
)
from tests.integration.test_ebay_c3_publish import (
    fresh_redis as fresh_redis,  # autouse: a Redis client per test event loop
)

pytestmark = pytest.mark.integration


def sync_url(product_id: uuid.UUID) -> str:
    return f"/api/v1/integrations/ebay/products/{product_id}/sync-price-quantity"


class SyncFakeEbay(PublishFakeEbay):
    def __init__(self) -> None:
        super().__init__()
        self.bulk_requests: list[dict[str, Any]] = []
        self.bulk_error: str | None = None

    async def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sell/inventory/v1/bulk_update_price_quantity":
            body = json.loads(request.content)
            self.bulk_requests.append(body)
            sku = body["requests"][0]["sku"]
            if self.bulk_error:
                return httpx.Response(
                    207,
                    json={
                        "responses": [
                            {
                                "sku": sku,
                                "statusCode": 400,
                                "errors": [{"message": self.bulk_error}],
                            }
                        ]
                    },
                )
            return httpx.Response(200, json={"responses": [{"sku": sku, "statusCode": 200}]})
        return await super().handler(request)


@pytest.fixture
def ebay(monkeypatch: pytest.MonkeyPatch) -> SyncFakeEbay:
    fake = SyncFakeEbay()
    install(monkeypatch, fake)
    return fake


async def published(
    client: AsyncClient, db_session: AsyncSession
) -> tuple[dict[str, str], uuid.UUID]:
    headers, product_id, store_id = await prepared(client, db_session)
    response = await client.post(
        PUBLISH_URL, json={"productId": str(product_id), "storeId": str(store_id)}, headers=headers
    )
    assert response.status_code == 200, response.text
    return headers, product_id


async def listing_for(db_session: AsyncSession, product_id: uuid.UUID) -> StoreListing:
    listing = await db_session.scalar(
        sa.select(StoreListing)
        .where(StoreListing.product_id == product_id)
        .execution_options(populate_existing=True)
    )
    assert listing is not None
    return listing


async def test_new_price_and_stock_are_sent_as_absolute_values(
    client: AsyncClient, db_session: AsyncSession, ebay: SyncFakeEbay
) -> None:
    headers, product_id = await published(client, db_session)
    product = await db_session.get(Product, product_id)
    assert product is not None
    product.sell_price = Decimal("14.00")
    product.stock_quantity = 3
    await db_session.flush()

    response = await client.post(sync_url(product_id), headers=headers)

    assert response.status_code == 200, response.text
    assert "sent to 1 eBay listing" in response.json()["message"]
    (sent,) = ebay.bulk_requests
    (line,) = sent["requests"]
    assert line["sku"] == f"dp-{product_id}"
    assert line["shipToLocationAvailability"] == {"quantity": 3}
    assert line["offers"] == [
        {
            "offerId": "OFFER-1",
            "availableQuantity": 3,
            "price": {"value": "14.00", "currency": "USD"},
        }
    ]
    assert (await listing_for(db_session, product_id)).status is ListingSyncStatus.SYNCED


async def test_eBays_refusal_of_one_line_is_recorded_on_the_listing(
    client: AsyncClient, db_session: AsyncSession, ebay: SyncFakeEbay
) -> None:
    headers, product_id = await published(client, db_session)
    ebay.bulk_error = "The price is below the minimum for this category."

    response = await client.post(sync_url(product_id), headers=headers)

    assert response.status_code == 200
    assert "below the minimum" in response.json()["message"]
    listing = await listing_for(db_session, product_id)
    assert listing.status is ListingSyncStatus.ERROR
    assert listing.last_error is not None and "below the minimum" in listing.last_error


async def test_a_wrong_currency_is_never_sent(
    client: AsyncClient, db_session: AsyncSession, ebay: SyncFakeEbay
) -> None:
    headers, product_id = await published(client, db_session)
    product = await db_session.get(Product, product_id)
    assert product is not None
    product.currency = "EUR"
    await db_session.flush()

    response = await client.post(sync_url(product_id), headers=headers)

    assert response.status_code == 200
    assert ebay.bulk_requests == []
    listing = await listing_for(db_session, product_id)
    assert listing.status is ListingSyncStatus.ERROR
    assert listing.last_error is not None and "EUR" in listing.last_error


async def test_a_product_without_an_eBay_listing_makes_no_call(
    client: AsyncClient, db_session: AsyncSession, ebay: SyncFakeEbay
) -> None:
    headers, product_id, _ = await prepared(client, db_session)

    response = await client.post(sync_url(product_id), headers=headers)

    assert response.status_code == 200
    assert response.json()["message"] == "This product has no eBay listing."
    assert ebay.bulk_requests == []


async def test_another_workspaces_product_is_404(
    client: AsyncClient, db_session: AsyncSession, ebay: SyncFakeEbay
) -> None:
    _, product_id = await published(client, db_session)
    from tests.integration.test_ebay_c1_api import auth_header, register

    other = auth_header(await register(client))
    assert (await client.post(sync_url(product_id), headers=other)).status_code == 404
