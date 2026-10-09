"""Keep a published WooCommerce product's price and stock current (Track E7, W3).

Mirrors EBAY-C4. One ``PUT /products/{id}`` per WooCommerce listing,
carrying **absolute** values (``regular_price``, ``stock_quantity``). A task
that runs twice (at-least-once delivery) sends the same numbers twice and
nothing drifts. Title, description and images change only through a publish.

What is recorded, and when:

* A local reason it cannot be sent (several variants, no price, wrong
  currency), or the store's own refusal → that listing is marked ERROR with
  the reason. The call returns normally so the record is committed.
* A store that is disconnected or was never verified → ERROR "reconnect".
* Store unreachable or 5xx → raised, so the Celery task retries with backoff
  and the listing is left as it was.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.logging import get_logger
from app.domain.money import normalise_currency
from app.integrations.ebay.listing_content import OfferTerms, offer_terms
from app.integrations.ebay.price_quantity import PriceQuantityOutcome
from app.integrations.woocommerce.client import WooCommerceAuthError, WooCommerceRejectedError
from app.integrations.woocommerce.connection import WooCommerceConnectionService, is_usable
from app.models.product import Product
from app.models.shopify import ListingSyncStatus, StoreListing
from app.models.store import Store, StorePlatform
from app.repositories.product import ProductRepository
from app.repositories.shopify import StoreListingRepository
from app.repositories.store import StoreRepository

logger = get_logger(__name__)

RECONNECT_MESSAGE = (
    "This WooCommerce store needs to be reconnected before price and stock can be sent."
)


class WooCommercePriceQuantitySync:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.products = ProductRepository(session)
        self.listings = StoreListingRepository(session)
        self.stores = StoreRepository(session)
        self.connections = WooCommerceConnectionService(session)

    async def push(self, product_id: uuid.UUID) -> PriceQuantityOutcome:
        outcome = PriceQuantityOutcome()
        targets = await self._woocommerce_listings(product_id)
        if not targets:
            return outcome
        outcome.listings = len(targets)

        product = await self._load_product(product_id)
        if product is None:  # soft-deleted since the change was queued
            return outcome
        terms = offer_terms(product)

        for listing, store in targets:
            if store.sync_paused_at is not None:  # an operator paused it (D-019)
                continue
            reason = self._local_reason(terms=terms, store=store)
            if reason is not None:
                await self._fail(listing, reason, outcome)
                continue
            if not is_usable(store):
                await self._fail(listing, RECONNECT_MESSAGE, outcome)
                continue
            client = self.connections.client_for(store)
            try:
                await client.put(
                    f"/products/{listing.external_product_id}",
                    {
                        "regular_price": f"{terms.price:.2f}",
                        "manage_stock": True,
                        "stock_quantity": terms.quantity,
                    },
                )
            except WooCommerceAuthError:
                await self._fail(listing, RECONNECT_MESSAGE, outcome)
                continue
            except WooCommerceRejectedError as exc:
                await self._fail(listing, exc.message, outcome)
                continue
            await self.listings.update(
                listing,
                status=ListingSyncStatus.SYNCED,
                last_synced_at=datetime.now(UTC),
                last_error=None,
            )
            outcome.synced += 1
        logger.info(
            "woocommerce_price_quantity_pushed",
            product_id=str(product_id),
            synced=outcome.synced,
            failed=outcome.failed,
        )
        return outcome

    @staticmethod
    def _local_reason(*, terms: OfferTerms, store: Store) -> str | None:
        if terms.multiple_variants:
            return "WooCommerce publishing supports one variant; this product now has several."
        if terms.price is None or terms.price <= 0:
            return "This product has no selling price for this store."
        if terms.currency != normalise_currency(store.currency):
            return (
                f"The price is in {terms.currency or 'an unrecorded currency'}, "
                f"but this WooCommerce store sells in {store.currency}."
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

    async def _woocommerce_listings(
        self, product_id: uuid.UUID
    ) -> list[tuple[StoreListing, Store]]:
        rows = await self.listings.list_for_product(product_id)
        out: list[tuple[StoreListing, Store]] = []
        for listing in rows:
            if not listing.external_product_id:
                continue
            store = await self.stores.get_by_id(listing.store_id)
            if store is not None and store.platform is StorePlatform.WOOCOMMERCE:
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


__all__ = ["RECONNECT_MESSAGE", "WooCommercePriceQuantitySync"]
