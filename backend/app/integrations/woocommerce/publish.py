"""Publish one draft to WooCommerce as a simple product (Track E7, W2).

Same shape as eBay C3: readiness first (channel ``woocommerce``) under the
product row lock, then create-or-update on the store, then record the
listing.

**Never two products for one draft.** The SKU is derived from the product
id. Before creating, the listing row's product id is tried, then the store
is searched by SKU. A retried publish — after a timeout that hid a success,
say — therefore adopts the product the first attempt made.

**Images are sent on create only.** WooCommerce downloads every image URL
it is given into the site's media library on each save. Re-sending them on
every update would fill the merchant's library with copies. Changing images
after the first publish is therefore done in WordPress for now; the
limitation is recorded in the W2 doc.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.integrations.ebay.listing_content import image_urls, listing_text, offer_terms
from app.integrations.woocommerce.client import WooCommerceClient, WooCommerceRejectedError
from app.integrations.woocommerce.connection import WooCommerceConnectionService
from app.models.product import Product
from app.models.shopify import ListingContentSource, ListingSyncStatus, StoreListing
from app.repositories.product import ProductRepository
from app.repositories.shopify import StoreListingRepository
from app.repositories.store import StoreRepository
from app.services.publish_readiness import CHANNEL_WOOCOMMERCE, PublishReadinessService

logger = get_logger(__name__)

PUBLISH_LOCK_TIMEOUT_MS = 30_000
#: WooCommerce accepts at most this many gallery images per product in one
#: request without timing out on shared hosting.
MAX_IMAGES = 10


def woocommerce_sku(product_id: uuid.UUID) -> str:
    """One SKU per draft. A WooCommerce site is one catalogue, so unlike eBay
    there is no marketplace dimension to keep apart."""
    return f"dp-{product_id}"


@dataclass(frozen=True, slots=True)
class WooCommercePublishResult:
    listing: StoreListing
    external_id: str
    storefront_url: str
    created: bool


class WooCommercePublishService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.products = ProductRepository(session)
        self.stores = StoreRepository(session)
        self.listings = StoreListingRepository(session)
        self.connections = WooCommerceConnectionService(session)

    async def publish(
        self,
        *,
        product_id: uuid.UUID,
        store_id: uuid.UUID,
        expected_updated_at: datetime | None,
    ) -> WooCommercePublishResult:
        # Track E6b: a new listing must fit the plan (republishing is free).
        from app.services.entitlements import BillingGate

        await BillingGate(self.session).require_room_for(product_id=product_id, store_id=store_id)
        locked = await self.products.lock_for_update(product_id, timeout_ms=PUBLISH_LOCK_TIMEOUT_MS)
        if locked is None:
            raise NotFoundError("Product not found.")
        await PublishReadinessService(self.session).require_publishable(
            channel=CHANNEL_WOOCOMMERCE,
            product_id=product_id,
            store_id=store_id,
            expected_updated_at=expected_updated_at,
        )
        store = await self.stores.get_by_id_or_raise(store_id)
        product = await self._load_product(product_id)
        client = self.connections.client_for(store)

        terms = offer_terms(product)
        title, description = listing_text(product)
        sku = woocommerce_sku(product.id)
        body: dict[str, Any] = {
            "name": title,
            "type": "simple",
            "status": "publish",
            "description": description,
            "sku": sku,
            "regular_price": f"{terms.price:.2f}",
            "manage_stock": True,
            "stock_quantity": terms.quantity,
        }

        existing = await self.listings.get_for_product(store_id=store.id, product_id=product.id)
        remote_id = await self._find_remote(client, existing, sku)
        if remote_id is None:
            body["images"] = [{"src": url} for url in image_urls(product)[:MAX_IMAGES]]
            remote = await client.post("/products", body)
        else:
            remote = await client.put(f"/products/{remote_id}", body)
        if not isinstance(remote, dict) or "id" not in remote:
            raise WooCommerceRejectedError("WooCommerce did not return the product.")
        external_id = str(remote["id"])
        url = str(remote.get("permalink") or store.storefront_url or "")

        now = datetime.now(UTC)
        values: dict[str, Any] = {
            "external_product_id": external_id,
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
            "woocommerce_published",
            product_id=str(product.id),
            store_id=str(store.id),
            created=remote_id is None,
        )
        return WooCommercePublishResult(
            listing=listing, external_id=external_id, storefront_url=url, created=remote_id is None
        )

    async def _find_remote(
        self, client: WooCommerceClient, existing: StoreListing | None, sku: str
    ) -> str | None:
        """The store's product for this draft, if one exists already."""
        if existing is not None and existing.external_product_id:
            try:
                found = await client.get(f"/products/{existing.external_product_id}")
            except WooCommerceRejectedError:
                found = None  # deleted in WordPress; fall through to the SKU search
            if isinstance(found, dict) and found.get("status") != "trash":
                return str(existing.external_product_id)
        matches = await client.get("/products", {"sku": sku})
        if isinstance(matches, list):
            for match in matches:
                if isinstance(match, dict) and match.get("sku") == sku and "id" in match:
                    return str(match["id"])
        return None

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


__all__ = ["WooCommercePublishResult", "WooCommercePublishService", "woocommerce_sku"]
