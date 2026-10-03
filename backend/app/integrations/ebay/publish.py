"""EBAY-C3: publish one draft to eBay as a fixed-price listing.

Inventory API, three steps: put the inventory item (by SKU), create or update
the offer for that SKU on the marketplace, publish the offer. The SKU is
derived from the product id and the offer is looked up before one is
created, so a retried publish — after a timeout, say — adopts what the first
attempt left on eBay instead of creating a second listing.

Synchronous in the request, like Shopify publish, under the same product row
lock so two concurrent publishes of one draft cannot interleave. Readiness
runs first (``PublishReadinessService``, channel ``ebay``): a product that
fails it never reaches eBay.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import EbayEnvironment, settings
from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.integrations.ebay.connection import EbayConnectionService
from app.integrations.ebay.exceptions import EbayNotConnectedError
from app.integrations.ebay.listing_content import (
    ebay_sku,
    image_urls,
    listing_text,
    offer_terms,
)
from app.integrations.ebay.seller_setup import EBAY_MARKETPLACES, EbaySellerClient
from app.models.product import Product
from app.models.shopify import ListingContentSource, ListingSyncStatus, StoreListing
from app.repositories.ebay import EbayListingDefaultsRepository
from app.repositories.product import (
    ProductMarketplaceAttributesRepository,
    ProductRepository,
)
from app.repositories.shopify import StoreListingRepository
from app.repositories.store import StoreRepository
from app.services.publish_readiness import CHANNEL_EBAY, PublishReadinessService

logger = get_logger(__name__)

#: Same bound as Shopify publish: long enough for one publish to finish.
PUBLISH_LOCK_TIMEOUT_MS = 30_000


@dataclass(frozen=True, slots=True)
class EbayPublishResult:
    listing: StoreListing
    listing_id: str
    storefront_url: str
    created: bool


def listing_url(marketplace_id: str, listing_id: str) -> str:
    if settings.ebay.environment is EbayEnvironment.SANDBOX:
        return f"https://www.sandbox.ebay.com/itm/{listing_id}"
    return f"https://{EBAY_MARKETPLACES[marketplace_id].site_host}/itm/{listing_id}"


class EbayPublishService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.products = ProductRepository(session)
        self.stores = StoreRepository(session)
        self.listings = StoreListingRepository(session)
        self.defaults = EbayListingDefaultsRepository(session)
        self.attributes = ProductMarketplaceAttributesRepository(session)
        self.connections = EbayConnectionService(session)

    async def publish(
        self,
        *,
        product_id: uuid.UUID,
        store_id: uuid.UUID,
        expected_updated_at: datetime | None,
    ) -> EbayPublishResult:
        locked = await self.products.lock_for_update(product_id, timeout_ms=PUBLISH_LOCK_TIMEOUT_MS)
        if locked is None:
            raise NotFoundError("Product not found.")
        await PublishReadinessService(self.session).require_publishable(
            channel=CHANNEL_EBAY,
            product_id=product_id,
            store_id=store_id,
            expected_updated_at=expected_updated_at,
        )

        store = await self.stores.get_by_id_or_raise(store_id)
        marketplace = EBAY_MARKETPLACES[str(store.settings["ebayMarketplaceId"])]
        product = await self._load_product(product_id)
        defaults = await self.defaults.get_for_marketplace(marketplace.id)
        attributes = await self.attributes.get_for(product_id, marketplace.id)
        connection = await self.connections.get_connection()
        if defaults is None or attributes is None or connection is None:  # pragma: no cover
            # Readiness has just checked all three under the lock.
            raise EbayNotConnectedError()

        terms = offer_terms(product)
        title, description = listing_text(product)
        sku = ebay_sku(product.id)
        existing = await self.listings.get_for_product(store_id=store.id, product_id=product.id)

        # A refusal or outage propagates as the error the merchant sees. No
        # ERROR state is written on the listing: the request's transaction
        # rolls back on the exception, so such a write would not survive, and
        # claiming otherwise would be worse than not recording it.
        client = EbaySellerClient(await self.connections.access_token_for(connection))
        await client.put_inventory_item(
            sku,
            {
                "availability": {"shipToLocationAvailability": {"quantity": terms.quantity}},
                "condition": "NEW",
                "product": {
                    "title": title,
                    "description": description,
                    "aspects": dict(attributes.aspects or {}),
                    "imageUrls": image_urls(product),
                },
            },
            content_language=marketplace.content_language,
        )
        offer_body: dict[str, Any] = {
            "availableQuantity": terms.quantity,
            "categoryId": attributes.category_id,
            "listingDescription": description,
            "listingPolicies": {
                "fulfillmentPolicyId": defaults.fulfillment_policy_id,
                "paymentPolicyId": defaults.payment_policy_id,
                "returnPolicyId": defaults.return_policy_id,
            },
            "merchantLocationKey": defaults.merchant_location_key,
            "pricingSummary": {
                "price": {"value": f"{terms.price:.2f}", "currency": marketplace.currency}
            },
        }
        offer = await client.find_offer(sku, marketplace.id)
        if offer is None:
            offer_id = await client.create_offer(
                {
                    "sku": sku,
                    "marketplaceId": marketplace.id,
                    "format": "FIXED_PRICE",
                    **offer_body,
                },
                content_language=marketplace.content_language,
            )
            published_listing_id = None
        else:
            offer_id = offer.offer_id
            await client.update_offer(
                offer_id, offer_body, content_language=marketplace.content_language
            )
            published_listing_id = offer.listing_id if offer.published else None
        listing_id = published_listing_id or await client.publish_offer(offer_id)

        now = datetime.now(UTC)
        url = listing_url(marketplace.id, listing_id)
        values: dict[str, Any] = {
            "external_product_id": listing_id,
            "external_offer_id": offer_id,
            "external_sku": sku,
            "storefront_url": url,
            "status": ListingSyncStatus.SYNCED,
            "last_synced_at": now,
            "last_error": None,
            "content_source": ListingContentSource.PRODUCT,
            "content_version_id": None,
        }
        if existing is None:
            listing = await self.listings.create(
                store_id=store.id, product_id=product.id, published_at=now, **values
            )
        else:
            if existing.published_at is None:
                values["published_at"] = now
            listing = await self.listings.update(existing, **values)
        logger.info(
            "ebay_published",
            product_id=str(product.id),
            store_id=str(store.id),
            created=existing is None,
        )
        return EbayPublishResult(
            listing=listing, listing_id=listing_id, storefront_url=url, created=existing is None
        )

    async def _load_product(self, product_id: uuid.UUID) -> Product:
        result = await self.session.execute(
            self.products._base_query()
            .where(Product.id == product_id)
            .options(selectinload(Product.variants), selectinload(Product.images))
            .execution_options(populate_existing=True)
        )
        product = result.scalar_one_or_none()
        if product is None:
            raise NotFoundError("Product not found.")
        return product


__all__ = ["PUBLISH_LOCK_TIMEOUT_MS", "EbayPublishResult", "EbayPublishService", "listing_url"]
