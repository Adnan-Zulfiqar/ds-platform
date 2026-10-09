"""Server-authoritative publish readiness.

One evaluation path for both the readiness review endpoint and the final
publish call, per channel (Shopify; eBay since EBAY-C3, D-C3-1). Shared
checks run for every channel; platform checks are chosen by the store.
Frontend checklist advice must not duplicate these
rules or invent blockers the provider/business layer does not enforce.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.domain.money import normalise_currency
from app.integrations.aliexpress.countries import country_display_name
from app.integrations.ebay.connection import EbayConnectionService
from app.integrations.ebay.exceptions import EbayRateLimitedError, EbaySellerApiUnavailableError
from app.integrations.ebay.listing_content import (
    EBAY_TITLE_MAX,
    image_urls,
    listing_text,
    offer_terms,
)
from app.integrations.ebay.product_details import EbayProductDetailsService
from app.integrations.shopify.service import ShopifyService
from app.integrations.shopify.sync import ShopifySyncService
from app.integrations.woocommerce.connection import is_usable as woocommerce_usable
from app.models.integration import IntegrationStatus
from app.models.product import Product
from app.models.store import Store, StorePlatform, StoreStatus
from app.repositories.ebay import EbayListingDefaultsRepository
from app.repositories.product import ProductRepository
from app.repositories.store import StoreRepository
from app.services.base import BaseService
from app.services.import_destination import country_from_store_settings

CHANNEL_SHOPIFY = "shopify"
CHANNEL_EBAY = "ebay"
CHANNEL_WOOCOMMERCE = "woocommerce"
_CHANNEL_PLATFORM = {
    CHANNEL_SHOPIFY: StorePlatform.SHOPIFY,
    CHANNEL_EBAY: StorePlatform.EBAY,
    CHANNEL_WOOCOMMERCE: StorePlatform.WOOCOMMERCE,
}

# Stable machine-readable codes. Clients branch on these; copy may change.
CODE_STORE_REQUIRED = "store_required"
CODE_UNSUPPORTED_CHANNEL = "unsupported_channel"
CODE_STORE_DISCONNECTED = "store_disconnected"
#: An operator paused writes to the store (D-019).
CODE_STORE_PAUSED = "store_paused"
CODE_DESTINATION_MISMATCH = "destination_mismatch"
CODE_SELLING_CURRENCY_MISMATCH = "selling_currency_mismatch"
CODE_DRAFT_VERSION_STALE = "draft_version_stale"
CODE_TITLE_THIN = "title_thin"
CODE_DESCRIPTION_EMPTY = "description_empty"
CODE_IMAGES_MISSING = "images_missing"
# EBAY-C3
CODE_EBAY_SETUP_MISSING = "ebay_listing_setup_missing"
CODE_EBAY_CATEGORY_MISSING = "ebay_category_missing"
CODE_EBAY_ASPECTS_MISSING = "ebay_aspects_missing"
CODE_EBAY_REQUIREMENTS_UNAVAILABLE = "ebay_requirements_unavailable"
CODE_EBAY_MULTIPLE_VARIANTS = "ebay_multiple_variants"
CODE_WOOCOMMERCE_MULTIPLE_VARIANTS = "woocommerce_multiple_variants"
CODE_EBAY_TITLE_TOO_LONG = "ebay_title_too_long"
CODE_PRICE_MISSING = "price_missing"
CODE_QUANTITY_MISSING = "quantity_missing"


@dataclass(frozen=True, slots=True)
class PublishCheckItem:
    """One blocker or recommendation from the publish authority."""

    code: str
    message: str
    field: str | None = None
    section: str | None = None
    action: str | None = None


@dataclass(frozen=True, slots=True)
class PublishReadinessResult:
    """Authoritative publish check for a draft + channel destination."""

    channel: str
    store_id: uuid.UUID | None
    draft_id: uuid.UUID
    draft_updated_at: datetime
    can_publish: bool
    blockers: tuple[PublishCheckItem, ...]
    recommendations: tuple[PublishCheckItem, ...]
    checked_at: datetime


def _sorted_items(items: list[PublishCheckItem]) -> tuple[PublishCheckItem, ...]:
    return tuple(sorted(items, key=lambda item: (item.code, item.field or "", item.message)))


def _strip_html(value: str | None) -> str:
    if not value:
        return ""
    text = value
    while "<" in text and ">" in text:
        start = text.find("<")
        end = text.find(">", start)
        if end == -1:
            break
        text = text[:start] + " " + text[end + 1 :]
    return " ".join(text.split())


class PublishReadinessService(BaseService):
    """Evaluate publication blockers and non-blocking advice.

    Blockers are limited to rules already enforced (or required) by the
    Shopify publish path: tenant-scoped identity, channel support, connection
    state, import destination vs store market, and verified sell-currency
    match. Marketing/SEO quality stays advisory.
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.products = ProductRepository(session)
        self.stores = StoreRepository(session)
        self.shopify = ShopifyService(session)
        # Reuse the proven assert helpers; do not maintain a second rule list.
        self._sync = ShopifySyncService(session)

    async def _load_product(self, product_id: uuid.UUID) -> Product:
        query = (
            self.products._base_query()
            .where(Product.id == product_id)
            .options(
                selectinload(Product.variants),
                selectinload(Product.images),
            )
        )
        result = await self.session.execute(query)
        product = result.scalar_one_or_none()
        if product is None:
            raise NotFoundError("Product not found.")
        return product

    def _recommendations_for(self, product: Product) -> list[PublishCheckItem]:
        items: list[PublishCheckItem] = []
        if len((product.title or "").strip()) < 8:
            items.append(
                PublishCheckItem(
                    code=CODE_TITLE_THIN,
                    message="Add a clearer product title.",
                    field="title",
                    section="overview",
                    action="Go to Overview",
                )
            )
        if not _strip_html(getattr(product, "description", None)):
            items.append(
                PublishCheckItem(
                    code=CODE_DESCRIPTION_EMPTY,
                    message="Add a product description.",
                    field="description",
                    section="description",
                    action="Go to Description",
                )
            )
        live_images = [
            image
            for image in (getattr(product, "images", None) or [])
            if getattr(image, "url", None) and getattr(image, "deleted_at", None) is None
        ]
        if not live_images:
            items.append(
                PublishCheckItem(
                    code=CODE_IMAGES_MISSING,
                    message="Add at least one product image.",
                    field="images",
                    section="media",
                    action="Go to Media",
                )
            )
        return items

    def _destination_blocker(self, *, product: Product, store: Store) -> PublishCheckItem | None:
        store_country = country_from_store_settings(store.settings)
        import_country = product.import_ship_to_country
        if not store_country or not import_country or store_country == import_country:
            return None
        return PublishCheckItem(
            code=CODE_DESTINATION_MISMATCH,
            message=(
                f"This draft was imported for {country_display_name(import_country)}, "
                f"but the store market is {country_display_name(store_country)}. "
                f"Refresh supplier data for {country_display_name(store_country)} "
                "before publishing."
            ),
            field="importShipToCountry",
            section="shipping",
            action="Go to Shipping",
        )

    def _currency_blocker(self, *, product: Product, store: Store) -> PublishCheckItem | None:
        try:
            self._sync._assert_variant_prices_match_store_currency(product=product, store=store)
        except ValidationError as exc:
            details: dict[str, Any] = getattr(exc, "details", {}) or {}
            variant_id = details.get("variant_id")
            return PublishCheckItem(
                code=CODE_SELLING_CURRENCY_MISMATCH,
                message=str(exc.message),
                field="sellPrice" if variant_id else None,
                section="pricing",
                action="Go to Pricing",
            )
        return None

    def _woocommerce_blockers(self, *, product: Product, store: Store) -> list[PublishCheckItem]:
        """What a WooCommerce publish needs (Track E7, W2).

        Same content rules as eBay (one variant, the merchant's own selling
        price in the store's verified currency, stock on hand), through the
        same ``listing_content`` helpers the publish uses. Images are not
        required: WooCommerce accepts a product without one.
        """
        if not woocommerce_usable(store):
            return [
                PublishCheckItem(
                    code=CODE_STORE_DISCONNECTED,
                    message=(
                        "This WooCommerce store is not connected. Connect it in "
                        "Integrations before publishing."
                    ),
                    field="storeId",
                    section="publishing",
                    action="Open Integrations",
                )
            ]
        items: list[PublishCheckItem] = []
        terms = offer_terms(product)
        if terms.multiple_variants:
            items.append(
                PublishCheckItem(
                    code=CODE_WOOCOMMERCE_MULTIPLE_VARIANTS,
                    message=(
                        "WooCommerce publishing supports one variant for now. Disable the "
                        "other variants, or publish this product to Shopify."
                    ),
                    field="variants",
                    section="variants",
                    action="Go to Options & variants",
                )
            )
        else:
            if terms.price is None or terms.price <= 0:
                items.append(
                    PublishCheckItem(
                        code=CODE_PRICE_MISSING,
                        message="Set a selling price on the Pricing tab for this store.",
                        field="sellPrice",
                        section="pricing",
                        action="Go to Pricing",
                    )
                )
            elif terms.currency != normalise_currency(store.currency):
                items.append(
                    PublishCheckItem(
                        code=CODE_SELLING_CURRENCY_MISMATCH,
                        message=(
                            f"The price is in {terms.currency or 'an unrecorded currency'}, "
                            f"but this WooCommerce store sells in {store.currency}. "
                            "Recalculate pricing for this store on the Pricing tab."
                        ),
                        field="sellPrice",
                        section="pricing",
                        action="Go to Pricing",
                    )
                )
            if terms.quantity <= 0:
                items.append(
                    PublishCheckItem(
                        code=CODE_QUANTITY_MISSING,
                        message="Add at least one item in stock before publishing.",
                        field="stockQuantity",
                        section="inventory",
                        action="Go to Stock",
                    )
                )
        title, _ = listing_text(product)
        if not title:
            items.append(
                PublishCheckItem(
                    code=CODE_TITLE_THIN,
                    message="Give the product a title before publishing.",
                    field="title",
                    section="overview",
                    action="Go to Overview",
                )
            )
        destination = self._destination_blocker(product=product, store=store)
        if destination is not None:
            items.append(destination)
        return items

    async def _ebay_blockers(self, *, product: Product, store: Store) -> list[PublishCheckItem]:
        """What eBay would refuse, checked before an offer is sent (EBAY-C3).

        Uses the same ``listing_content`` helpers as the publish, so a product
        that passes here is the product that is sent.
        """
        items: list[PublishCheckItem] = []
        marketplace_id = str((store.settings or {}).get("ebayMarketplaceId") or "")

        connection = await EbayConnectionService(self.session).get_connection()
        if (
            connection is None
            or not connection.is_usable
            or store.status is not StoreStatus.CONNECTED
        ):
            items.append(
                PublishCheckItem(
                    code=CODE_STORE_DISCONNECTED,
                    message="eBay is not connected. Reconnect it in Settings before publishing.",
                    field="storeId",
                    section="publishing",
                    action="Open Integrations",
                )
            )
            return items

        defaults = await EbayListingDefaultsRepository(self.session).get_for_marketplace(
            marketplace_id
        )
        if (
            defaults is None
            or defaults.store_id != store.id
            or defaults.connection_id != connection.id
        ):
            items.append(
                PublishCheckItem(
                    code=CODE_EBAY_SETUP_MISSING,
                    message=(
                        "Choose the shipping, payment and return policies and the warehouse "
                        "for this eBay marketplace first (Integrations → eBay → Listing setup)."
                    ),
                    field="storeId",
                    section="publishing",
                    action="Open Integrations",
                )
            )

        title, _ = listing_text(product)
        if len(title) > EBAY_TITLE_MAX:
            items.append(
                PublishCheckItem(
                    code=CODE_EBAY_TITLE_TOO_LONG,
                    message=(
                        f"eBay titles are at most {EBAY_TITLE_MAX} characters; "
                        f"this one has {len(title)}."
                    ),
                    field="title",
                    section="overview",
                    action="Go to Overview",
                )
            )
        if not image_urls(product):
            items.append(
                PublishCheckItem(
                    code=CODE_IMAGES_MISSING,
                    message="eBay needs at least one product image (https).",
                    field="images",
                    section="media",
                    action="Go to Media",
                )
            )

        terms = offer_terms(product)
        if terms.multiple_variants:
            items.append(
                PublishCheckItem(
                    code=CODE_EBAY_MULTIPLE_VARIANTS,
                    message=(
                        "eBay publishing supports one variant for now. Disable the other "
                        "variants, or publish this product to Shopify."
                    ),
                    field="variants",
                    section="variants",
                    action="Go to Options & variants",
                )
            )
        else:
            if terms.price is None or terms.price <= 0:
                items.append(
                    PublishCheckItem(
                        code=CODE_PRICE_MISSING,
                        message="Set a selling price on the Pricing tab for this eBay store.",
                        field="sellPrice",
                        section="pricing",
                        action="Go to Pricing",
                    )
                )
            elif terms.currency != normalise_currency(store.currency):
                items.append(
                    PublishCheckItem(
                        code=CODE_SELLING_CURRENCY_MISMATCH,
                        message=(
                            f"The price is in {terms.currency or 'an unrecorded currency'}, "
                            f"but this eBay marketplace sells in {store.currency}. "
                            "Recalculate pricing for "
                            "this store on the Pricing tab."
                        ),
                        field="sellPrice",
                        section="pricing",
                        action="Go to Pricing",
                    )
                )
            if terms.quantity <= 0:
                items.append(
                    PublishCheckItem(
                        code=CODE_QUANTITY_MISSING,
                        message="eBay needs at least one item in stock.",
                        field="stockQuantity",
                        section="inventory",
                        action="Go to Stock",
                    )
                )

        destination = self._destination_blocker(product=product, store=store)
        if destination is not None:
            items.append(destination)

        try:
            has_category, missing = await EbayProductDetailsService(
                self.session
            ).missing_required_aspects(product.id, marketplace_id)
        except (EbaySellerApiUnavailableError, EbayRateLimitedError):
            items.append(
                PublishCheckItem(
                    code=CODE_EBAY_REQUIREMENTS_UNAVAILABLE,
                    message="Could not check eBay's requirements for the category. Try again.",
                    field=None,
                    section="publishing",
                    action=None,
                )
            )
        else:
            if not has_category:
                items.append(
                    PublishCheckItem(
                        code=CODE_EBAY_CATEGORY_MISSING,
                        message="Choose an eBay category in eBay details.",
                        field="ebayCategory",
                        section="publishing",
                        action=None,
                    )
                )
            elif missing:
                items.append(
                    PublishCheckItem(
                        code=CODE_EBAY_ASPECTS_MISSING,
                        message=f"eBay requires: {', '.join(missing)}.",
                        field="ebayAspects",
                        section="publishing",
                        action=None,
                    )
                )
        return items

    async def evaluate(
        self,
        *,
        channel: str,
        product_id: uuid.UUID,
        store_id: uuid.UUID | None,
        expected_updated_at: datetime | None = None,
        enforce_version: bool = False,
    ) -> PublishReadinessResult:
        """Return blockers/recommendations for a draft publish attempt.

        Foreign product/store ids raise :class:`NotFoundError` (non-disclosing).
        When ``enforce_version`` is true and the client supplied a stale
        ``expected_updated_at``, raises :class:`ConflictError` so publish never
        reaches the provider.
        """
        product = await self._load_product(product_id)
        checked_at = datetime.now(UTC)
        blockers: list[PublishCheckItem] = []
        recommendations = self._recommendations_for(product)

        if expected_updated_at is not None and product.updated_at != expected_updated_at:
            if enforce_version:
                raise ConflictError(
                    (
                        "This draft changed somewhere else. "
                        "Review the latest version before publishing."
                    ),
                    details={"reason": CODE_DRAFT_VERSION_STALE},
                )
            blockers.append(
                PublishCheckItem(
                    code=CODE_DRAFT_VERSION_STALE,
                    message=(
                        "This draft changed somewhere else. "
                        "Review the latest version before publishing."
                    ),
                    field="updatedAt",
                    section="publishing",
                    action="Reload draft",
                )
            )

        normalised_channel = (channel or "").strip().lower()
        if normalised_channel not in _CHANNEL_PLATFORM:
            blockers.append(
                PublishCheckItem(
                    code=CODE_UNSUPPORTED_CHANNEL,
                    message=(
                        "Only Shopify, eBay and WooCommerce publishing are supported right now."
                    ),
                    field="channel",
                    section="publishing",
                    action=None,
                )
            )
            return PublishReadinessResult(
                channel=normalised_channel or channel,
                store_id=store_id,
                draft_id=product.id,
                draft_updated_at=product.updated_at,
                can_publish=False,
                blockers=_sorted_items(blockers),
                recommendations=_sorted_items(recommendations),
                checked_at=checked_at,
            )

        if store_id is None:
            blockers.append(
                PublishCheckItem(
                    code=CODE_STORE_REQUIRED,
                    message="Select where you want to publish this product.",
                    field="storeId",
                    section="publishing",
                    action="Choose a store",
                )
            )
            return PublishReadinessResult(
                channel=normalised_channel,
                store_id=None,
                draft_id=product.id,
                draft_updated_at=product.updated_at,
                can_publish=False,
                blockers=_sorted_items(blockers),
                recommendations=_sorted_items(recommendations),
                checked_at=checked_at,
            )

        store = await self.stores.get_by_id_or_raise(store_id)
        if store.sync_paused_at is not None:
            blockers.append(
                PublishCheckItem(
                    code=CODE_STORE_PAUSED,
                    message=(
                        "DropPilot support has paused updates to this store. "
                        "Publishing resumes when the pause is lifted."
                    ),
                    field="storeId",
                    section="publishing",
                )
            )
        if store.platform is not _CHANNEL_PLATFORM[normalised_channel]:
            blockers.append(
                PublishCheckItem(
                    code=CODE_UNSUPPORTED_CHANNEL,
                    message=(
                        f"This is not a {normalised_channel.capitalize()} store. "
                        "Choose a store for this channel."
                    ),
                    field="storeId",
                    section="publishing",
                    action="Choose a store",
                )
            )
        elif store.platform is StorePlatform.EBAY:
            blockers.extend(await self._ebay_blockers(product=product, store=store))
        elif store.platform is StorePlatform.WOOCOMMERCE:
            blockers.extend(self._woocommerce_blockers(product=product, store=store))
        elif store.platform is StorePlatform.SHOPIFY:
            connection = await self.shopify.connections.get_by_store(store_id)
            if connection is None or connection.status is not IntegrationStatus.CONNECTED:
                blockers.append(
                    PublishCheckItem(
                        code=CODE_STORE_DISCONNECTED,
                        message=(
                            "This Shopify store is not connected. "
                            "Reconnect it in Settings before publishing."
                        ),
                        field="storeId",
                        section="publishing",
                        action="Open Integrations",
                    )
                )

            destination = self._destination_blocker(product=product, store=store)
            if destination is not None:
                blockers.append(destination)

            currency = self._currency_blocker(product=product, store=store)
            if currency is not None:
                blockers.append(currency)
        else:
            # Track E7: Shopify is a named case, not the fallback. A channel
            # added to _CHANNEL_PLATFORM without its own rules is refused here
            # rather than silently checked against Shopify's.
            blockers.append(
                PublishCheckItem(
                    code=CODE_UNSUPPORTED_CHANNEL,
                    message="Publishing to this kind of store is not supported yet.",
                    field="storeId",
                    section="publishing",
                    action=None,
                )
            )

        ordered_blockers = _sorted_items(blockers)
        return PublishReadinessResult(
            channel=normalised_channel,
            store_id=store_id,
            draft_id=product.id,
            draft_updated_at=product.updated_at,
            can_publish=len(ordered_blockers) == 0,
            blockers=ordered_blockers,
            recommendations=_sorted_items(recommendations),
            checked_at=checked_at,
        )

    async def require_publishable(
        self,
        *,
        channel: str,
        product_id: uuid.UUID,
        store_id: uuid.UUID,
        expected_updated_at: datetime | None = None,
    ) -> PublishReadinessResult:
        """Evaluate and raise when publication must not reach the provider."""
        result = await self.evaluate(
            channel=channel,
            product_id=product_id,
            store_id=store_id,
            expected_updated_at=expected_updated_at,
            enforce_version=True,
        )
        if result.can_publish:
            return result
        first = result.blockers[0] if result.blockers else None
        message = (
            first.message
            if first is not None
            else "This product cannot be published yet. Fix the issues below and try again."
        )
        raise ValidationError(
            message,
            details={
                "reason": "publish_blocked",
                "blocker_codes": ",".join(item.code for item in result.blockers),
            },
        )


__all__ = [
    "CHANNEL_EBAY",
    "CHANNEL_SHOPIFY",
    "CHANNEL_WOOCOMMERCE",
    "CODE_DESCRIPTION_EMPTY",
    "CODE_DESTINATION_MISMATCH",
    "CODE_DRAFT_VERSION_STALE",
    "CODE_IMAGES_MISSING",
    "CODE_SELLING_CURRENCY_MISMATCH",
    "CODE_STORE_DISCONNECTED",
    "CODE_STORE_REQUIRED",
    "CODE_TITLE_THIN",
    "CODE_UNSUPPORTED_CHANNEL",
    "PublishCheckItem",
    "PublishReadinessResult",
    "PublishReadinessService",
]
