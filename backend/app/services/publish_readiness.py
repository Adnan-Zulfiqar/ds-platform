"""Server-authoritative publish readiness.

One evaluation path for both the readiness review endpoint and the final
Shopify publish call. Frontend checklist advice must not duplicate these
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
from app.integrations.aliexpress.countries import country_display_name
from app.integrations.shopify.service import ShopifyService
from app.integrations.shopify.sync import ShopifySyncService
from app.models.integration import IntegrationStatus
from app.models.product import Product
from app.models.store import Store, StorePlatform
from app.repositories.product import ProductRepository
from app.repositories.store import StoreRepository
from app.services.base import BaseService
from app.services.import_destination import country_from_store_settings

CHANNEL_SHOPIFY = "shopify"

# Stable machine-readable codes. Clients branch on these; copy may change.
CODE_STORE_REQUIRED = "store_required"
CODE_UNSUPPORTED_CHANNEL = "unsupported_channel"
CODE_STORE_DISCONNECTED = "store_disconnected"
CODE_DESTINATION_MISMATCH = "destination_mismatch"
CODE_SELLING_CURRENCY_MISMATCH = "selling_currency_mismatch"
CODE_DRAFT_VERSION_STALE = "draft_version_stale"
CODE_TITLE_THIN = "title_thin"
CODE_DESCRIPTION_EMPTY = "description_empty"
CODE_IMAGES_MISSING = "images_missing"


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
        if normalised_channel != CHANNEL_SHOPIFY:
            blockers.append(
                PublishCheckItem(
                    code=CODE_UNSUPPORTED_CHANNEL,
                    message="Only Shopify publishing is supported right now.",
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
                channel=CHANNEL_SHOPIFY,
                store_id=None,
                draft_id=product.id,
                draft_updated_at=product.updated_at,
                can_publish=False,
                blockers=_sorted_items(blockers),
                recommendations=_sorted_items(recommendations),
                checked_at=checked_at,
            )

        store = await self.stores.get_by_id_or_raise(store_id)
        if store.platform is not StorePlatform.SHOPIFY:
            blockers.append(
                PublishCheckItem(
                    code=CODE_UNSUPPORTED_CHANNEL,
                    message="Only Shopify stores can be used with this publish action.",
                    field="storeId",
                    section="publishing",
                    action="Choose a store",
                )
            )
        else:
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

        ordered_blockers = _sorted_items(blockers)
        return PublishReadinessResult(
            channel=CHANNEL_SHOPIFY,
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
    "CHANNEL_SHOPIFY",
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
