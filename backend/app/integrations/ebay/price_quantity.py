"""EBAY-C4: keep a published eBay listing's price and quantity current.

One call per product, ``bulkUpdatePriceQuantity``, carrying absolute values —
so a task that runs twice (at-least-once delivery) sends the same numbers
twice and nothing drifts. Title, description and images are not touched:
those change only through a publish.

Triggered after a committed change that can move price or stock (product
edit, supplier sync, inventory sync) and on demand from the editor. A product
with no eBay listing costs one indexed query and no eBay call.

What is recorded, and when:
* a refusal eBay states for one listing (``errors[].message``), or a local
  reason it cannot be sent (several variants, no price, wrong currency) →
  that listing is marked ERROR with the reason, and the call returns
  normally so the record is committed;
* eBay unreachable or 5xx → raised, so the Celery task retries with backoff
  and the listing is left as it was;
* a revoked grant → every listing marked ERROR "reconnect", and the call
  returns normally so that record (and the connection's reconnect state)
  is committed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.logging import get_logger
from app.domain.money import normalise_currency
from app.integrations.ebay.connection import EbayConnectionService
from app.integrations.ebay.exceptions import EbayTokenRevokedError
from app.integrations.ebay.listing_content import OfferTerms, offer_terms
from app.integrations.ebay.seller_setup import EBAY_MARKETPLACES, EbaySellerClient
from app.models.product import Product
from app.models.shopify import ListingSyncStatus, StoreListing
from app.models.store import Store, StorePlatform
from app.repositories.product import ProductRepository
from app.repositories.shopify import StoreListingRepository
from app.repositories.store import StoreRepository

logger = get_logger(__name__)

RECONNECT_MESSAGE = "eBay needs to be reconnected before price and stock can be sent."


@dataclass(slots=True)
class PriceQuantityOutcome:
    listings: int = 0
    synced: int = 0
    failed: int = 0
    reasons: dict[str, str] = field(default_factory=dict)


class EbayPriceQuantitySync:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.products = ProductRepository(session)
        self.listings = StoreListingRepository(session)
        self.stores = StoreRepository(session)
        self.connections = EbayConnectionService(session)

    async def push(self, product_id: uuid.UUID) -> PriceQuantityOutcome:
        outcome = PriceQuantityOutcome()
        targets = await self._ebay_listings(product_id)
        if not targets:
            return outcome
        outcome.listings = len(targets)

        product = await self._load_product(product_id)
        if product is None:  # soft-deleted since the change was queued
            return outcome
        terms = offer_terms(product)

        requests: list[dict[str, Any]] = []
        by_sku: dict[str, StoreListing] = {}
        for listing, store in targets:
            if store.sync_paused_at is not None:  # an operator paused it (D-019)
                continue
            reason = self._local_reason(terms=terms, store=store)
            if reason is not None:
                await self._fail(listing, reason, outcome)
                continue
            assert terms.price is not None and listing.external_sku and listing.external_offer_id
            marketplace = EBAY_MARKETPLACES[str(store.settings["ebayMarketplaceId"])]
            requests.append(
                {
                    "sku": listing.external_sku,
                    "shipToLocationAvailability": {"quantity": terms.quantity},
                    "offers": [
                        {
                            "offerId": listing.external_offer_id,
                            "availableQuantity": terms.quantity,
                            "price": {
                                "value": f"{terms.price:.2f}",
                                "currency": marketplace.currency,
                            },
                        }
                    ],
                }
            )
            by_sku[listing.external_sku] = listing
        if not requests:
            return outcome

        connection = await self.connections.get_connection()
        try:
            if connection is None:
                raise EbayTokenRevokedError()
            client = EbaySellerClient(await self.connections.access_token_for(connection))
            results = await client.bulk_update_price_quantity(requests)
        except EbayTokenRevokedError:
            for listing in by_sku.values():
                await self._fail(listing, RECONNECT_MESSAGE, outcome)
            return outcome

        now = datetime.now(UTC)
        answered = {result.sku: result for result in results}
        for sku, listing in by_sku.items():
            result = answered.get(sku)
            if result is None or not result.ok:
                reason = (
                    "eBay refused the update: " + " ".join(result.messages[:3])
                    if result is not None and result.messages
                    else "eBay did not confirm the update."
                )
                await self._fail(listing, reason, outcome)
                continue
            await self.listings.update(
                listing, status=ListingSyncStatus.SYNCED, last_synced_at=now, last_error=None
            )
            outcome.synced += 1
        logger.info(
            "ebay_price_quantity_pushed",
            product_id=str(product_id),
            synced=outcome.synced,
            failed=outcome.failed,
        )
        return outcome

    @staticmethod
    def _local_reason(*, terms: OfferTerms, store: Store) -> str | None:
        if terms.multiple_variants:
            return "eBay publishing supports one variant; this product now has several."
        if terms.price is None or terms.price <= 0:
            return "This product has no selling price for eBay."
        if terms.currency != normalise_currency(store.currency):
            return (
                f"The price is in {terms.currency or 'an unrecorded currency'}, "
                f"but this eBay marketplace sells in {store.currency}."
            )
        return None

    async def _fail(
        self, listing: StoreListing, reason: str, outcome: PriceQuantityOutcome
    ) -> None:
        await self.listings.update(
            listing,
            status=ListingSyncStatus.ERROR,
            last_error=reason[:2000],
            last_failed_sync_at=datetime.now(UTC),
        )
        outcome.failed += 1
        outcome.reasons[str(listing.id)] = reason

    async def _ebay_listings(self, product_id: uuid.UUID) -> list[tuple[StoreListing, Store]]:
        rows = await self.listings.list_for_product(product_id)
        out: list[tuple[StoreListing, Store]] = []
        for listing in rows:
            if not (listing.external_offer_id and listing.external_sku):
                continue
            store = await self.stores.get_by_id(listing.store_id)
            if store is not None and store.platform is StorePlatform.EBAY:
                out.append((listing, store))
        return out

    async def _load_product(self, product_id: uuid.UUID) -> Product | None:
        result = await self.session.execute(
            self.products._base_query()
            .where(Product.id == product_id)
            .options(selectinload(Product.variants))
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()


__all__ = ["RECONNECT_MESSAGE", "EbayPriceQuantitySync", "PriceQuantityOutcome"]
