"""Shopify channel sync — publish, inventory, price, order import."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.sanitize import sanitize_html
from app.domain.money import normalise_currency
from app.integrations.aliexpress.countries import country_display_name
from app.integrations.shopify.client import ShopifyClient
from app.integrations.shopify.service import ShopifyService
from app.models.order import (
    FulfillmentStatus,
    OrderSource,
    PaymentStatus,
)
from app.models.product import Product, ProductVersion
from app.models.shopify import ListingContentSource, ListingSyncStatus, StoreListing
from app.models.store import Store, StorePlatform
from app.repositories.order import OrderRepository
from app.repositories.product import ProductRepository, ProductVersionRepository
from app.repositories.shopify import StoreListingRepository
from app.repositories.store import StoreRepository
from app.services.base import BaseService
from app.services.import_destination import country_from_store_settings

logger = get_logger(__name__)

#: Bound on waiting for another publish that already holds the product row.
#: Matches the webhook-reconcile pattern: long enough for a typical Admin API
#: create to finish under the lock, short enough that a wedged worker surfaces
#: ``shopify_publish_busy`` instead of hanging HTTP workers forever.
PUBLISH_LOCK_TIMEOUT_MS: Final = 30_000


@dataclass(frozen=True, slots=True, kw_only=True)
class ShopifyListingOverlay:
    """Optional title/body substitution for an approved pipeline candidate.

    SEO, tags, images, variants, and handle stay on the merchant Product.
    The pipeline sanitizes `body_html` before constructing this; the
    publisher must not sanitize again. `version_id` is the approved
    `ProductVersion` the text came from — recorded on the listing so later
    ordinary publishes know AI text is live.
    """

    title: str
    body_html: str
    version_id: uuid.UUID


def overlay_from_published_version(version: ProductVersion) -> ShopifyListingOverlay:
    """Rebuild the overlay for an AI version that is already live.

    `ProductVersion.content` is immutable, and this exact text passed the
    Stage 7 publish checks when it first went live, so rebuilding it is a
    re-send of what Shopify already shows — not a new approval.
    """
    content = version.content if isinstance(version.content, dict) else {}
    title = content.get("title")
    if type(title) is not str or not title.strip():
        # Fail closed: an unreadable live version must never degrade into
        # "send the draft text instead".
        raise ConflictError(
            "The AI version live on Shopify can no longer be read. "
            "Choose explicitly whether to replace it with your draft text.",
            details={"reason": "published_ai_content_unavailable"},
        )
    description = content.get("description")
    body_html = sanitize_html(description if isinstance(description, str) else "") or ""
    return ShopifyListingOverlay(title=title, body_html=body_html, version_id=version.id)


def _apply_listing_overlay(
    product_body: dict[str, Any], overlay: ShopifyListingOverlay | None
) -> None:
    if overlay is None:
        return
    product_body["title"] = overlay.title
    product_body["body_html"] = overlay.body_html


def _deterministic_handle(product_id: uuid.UUID) -> str:
    """A stable Shopify product handle derived from the DropPilot product id.

    Not the display title-derived slug Shopify would generate on its own —
    deliberately unrelated to `title`, so a title change never changes which
    Shopify product a given DropPilot product maps to, and a create retry
    always searches for exactly the same handle regardless of what changed
    since the first attempt.
    """
    return f"droppilot-{product_id}"


class ShopifySyncService(BaseService):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.shopify = ShopifyService(session)
        self.products = ProductRepository(session)
        self.versions = ProductVersionRepository(session)
        self.listings = StoreListingRepository(session)
        self.orders = OrderRepository(session)
        self.stores = StoreRepository(session)

    def _assert_import_destination_matches_store(self, *, product: Product, store: Store) -> None:
        """Block publish when the draft was imported for a different market.

        GB availability does not prove US availability. When the store has a
        configured ``settings.countryCode`` and the product's last successful
        import destination differs, the merchant must refresh for that
        destination first.
        """
        store_country = country_from_store_settings(store.settings)
        import_country = product.import_ship_to_country
        if not store_country or not import_country:
            return
        if store_country == import_country:
            return
        raise ValidationError(
            (
                f"This draft was imported for {country_display_name(import_country)}, "
                f"but the store market is {country_display_name(store_country)}. "
                f"Refresh supplier data for {country_display_name(store_country)} "
                "before publishing — availability is destination-specific."
            ),
            details={
                "import_ship_to_country": import_country,
                "store_country": store_country,
                "reason": "destination_mismatch",
            },
        )

    def _assert_variant_prices_match_store_currency(
        self, *, product: Product, store: Store
    ) -> None:
        """Block publish when a priced variant's currency doesn't match the store's.

        Only enforced once the store's Shopify currency is itself verified
        (``currency_last_synced_at`` set) — the same authority bar
        ``PricingEngine._resolve_selling_currency`` already requires before
        it will compute a price in that currency at all. An unverified store
        currency is a separate, pre-existing gap (M17-adjacent) this check
        does not attempt to close.

        Scoped to variants that carry ``sell_price`` — the field the draft
        pricing workspace actually writes. A variant that has never been
        priced through the workspace (relying on the pre-existing
        ``list_price``/``product.sell_price`` publish fallback) is unchanged
        by this check; broadening it there is a larger, separate change.
        """
        if store.platform is not StorePlatform.SHOPIFY:
            return
        if store.currency_last_synced_at is None or not store.currency:
            return
        store_currency = normalise_currency(store.currency)
        for variant in product.variants:
            if getattr(variant, "deleted_at", None) is not None:
                continue
            if not getattr(variant, "is_enabled", True):
                continue
            if variant.sell_price is None:
                continue
            variant_currency = (
                normalise_currency(variant.sell_price_currency)
                if variant.sell_price_currency
                else None
            )
            if variant_currency == store_currency:
                continue
            raise ValidationError(
                (
                    f"Variant {variant.label or variant.external_variant_id} was "
                    f"priced in {variant_currency or 'an unrecorded currency'}, but "
                    f"this store sells in {store_currency}. Recalculate pricing for "
                    "this store on the Pricing tab before publishing — Shopify must "
                    "never receive a price in the wrong currency."
                ),
                details={
                    "reason": "selling_currency_mismatch",
                    "variant_id": str(variant.id),
                    "variant_currency": variant_currency,
                    "store_currency": store_currency,
                },
            )

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

    async def publish_product(
        self,
        *,
        store_id: uuid.UUID,
        product_id: uuid.UUID,
        expected_updated_at: datetime | None = None,
        listing_overlay: ShopifyListingOverlay | None = None,
        replace_ai_content: bool = False,
    ) -> dict[str, Any]:
        """Create or update a Shopify product for a DropPilot catalogue product.

        Content source (review finding E-1). Approved AI text that is live on
        this store is never silently replaced. When the listing records
        ``content_source = ai_version`` and no new overlay is supplied, the
        live AI title/description are re-sent from that immutable version —
        for the editor's Publish button, for Celery, and for every retry
        alike. Only ``replace_ai_content=True``, which the HTTP layer sets
        from an explicit merchant confirmation, sends the draft text instead.
        Celery callers have no way to pass it.

        Ordering (UX-L2B-R2):

        1. Authoritative readiness + version check (no provider contact).
        2. Tenant-scoped ``SELECT ... FOR UPDATE`` on the product row so two
           workers cannot both miss ``StoreListing`` and both create remotely.
        3. Re-run readiness under the lock (state may have changed while waiting).
        4. Re-read ``StoreListing``; construct the Shopify client only after the
           lock is held.
        5. Update existing listing, or ``_create_or_adopt`` via deterministic
           handle; persist ``StoreListing`` before the request transaction
           commits (lock releases on commit/rollback).

        At-most-one concurrent provider create for the same publication
        identity, with deterministic adoption on retry. Not exactly-once
        delivery — a lost response after remote create is recovered by handle
        search on the next attempt.
        """
        from app.services.publish_readiness import CHANNEL_SHOPIFY, PublishReadinessService

        readiness = PublishReadinessService(self.session)
        # Fail fast on blockers / stale version without taking the row lock.
        await readiness.require_publishable(
            channel=CHANNEL_SHOPIFY,
            product_id=product_id,
            store_id=store_id,
            expected_updated_at=expected_updated_at,
        )

        locked = await self.products.lock_for_update(product_id, timeout_ms=PUBLISH_LOCK_TIMEOUT_MS)
        if locked is None:
            raise NotFoundError("Product not found.")

        # Revalidate under the lock: a concurrent editor or a finished peer
        # publish may have moved state while we waited.
        await readiness.require_publishable(
            channel=CHANNEL_SHOPIFY,
            product_id=product_id,
            store_id=store_id,
            expected_updated_at=expected_updated_at,
        )

        product = await self._load_product(product_id)
        listing = await self.listings.get_for_product(store_id=store_id, product_id=product_id)
        listing_overlay, content_source = await self._resolve_listing_content(
            listing=listing,
            product_id=product_id,
            listing_overlay=listing_overlay,
            replace_ai_content=replace_ai_content,
        )
        # Provider client only after auth, ownership, version, readiness, lock.
        client, connection = await self.shopify.client_for_store(store_id)

        variants_payload: list[dict[str, Any]] = []
        for variant in product.variants:
            if getattr(variant, "deleted_at", None) is not None:
                continue
            if getattr(variant, "is_enabled", True) is False:
                continue
            # Merchant sell_price wins; list_price is supplier reference only.
            price = getattr(variant, "sell_price", None) or variant.list_price or product.sell_price
            sku = (getattr(variant, "merchant_sku", None) or variant.external_variant_id)[:64]
            entry: dict[str, Any] = {
                "sku": sku,
                "price": str(price or "0"),
                "inventory_management": "shopify",
                "inventory_quantity": int(variant.stock_quantity or 0),
                "option1": variant.label or "Default",
            }
            compare_at = getattr(variant, "compare_at_price", None)
            if compare_at is not None:
                entry["compare_at_price"] = str(compare_at)
            variants_payload.append(entry)
        if not variants_payload:
            variants_payload.append(
                {
                    "sku": str(product.id)[:32],
                    "price": str(product.sell_price or "0"),
                    "inventory_management": "shopify",
                    "inventory_quantity": int(product.stock_quantity or 0),
                    "option1": "Default",
                }
            )

        images = []
        for image in getattr(product, "images", []) or []:
            if not getattr(image, "url", None):
                continue
            if getattr(image, "deleted_at", None) is not None:
                continue
            payload_img: dict[str, Any] = {"src": image.url}
            alt = getattr(image, "alt_text", None)
            if alt:
                payload_img["alt"] = alt
            images.append(payload_img)

        # Prefer merchant slug for handle when present; fall back to the
        # deterministic idempotency handle so retries never invent a new product.
        create_handle = (product.slug or "").strip() or _deterministic_handle(product_id)
        product_body: dict[str, Any] = {
            "title": product.title,
            "body_html": getattr(product, "description", None) or "",
            "vendor": product.vendor or product.brand or "",
            "product_type": product.category_name or "",
            "tags": ", ".join(product.tags or []),
            "status": "active" if product.status.value == "active" else "draft",
            "variants": variants_payload,
            "images": images[:20],
        }
        # Shopify REST SEO title/description fields — never meta keywords.
        if product.seo_title:
            product_body["metafields_global_title_tag"] = product.seo_title
        if product.seo_description:
            product_body["metafields_global_description_tag"] = product.seo_description
        _apply_listing_overlay(product_body, listing_overlay)
        # Weight / shipping flags on the first variant when physical.
        if product.requires_shipping and product.package_weight_kg is not None:
            grams = int((product.package_weight_kg * Decimal("1000")).to_integral_value())
            for entry in variants_payload:
                entry["weight"] = float(product.package_weight_kg)
                entry["weight_unit"] = product.weight_unit or "kg"
                entry["grams"] = grams
                entry["requires_shipping"] = True
        elif product.requires_shipping is False:
            for entry in variants_payload:
                entry["requires_shipping"] = False

        body = {"product": product_body}

        try:
            if listing is not None:
                payload = await client.put(
                    f"/products/{listing.external_product_id}.json",
                    json_body=body,
                )
            else:
                payload = await self._create_or_adopt(client, body=body, handle=create_handle)

            shopify_product = payload.get("product") or {}
            external_id = str(shopify_product.get("id") or "")
            if not external_id:
                raise NotFoundError("Shopify did not return a product id.")

            handle = str(shopify_product.get("handle") or create_handle)
            shop_domain = connection.shop_domain
            graphql_id = f"gid://shopify/Product/{external_id}"
            admin_url = f"https://{shop_domain}/admin/products/{external_id}"
            # Online Store visibility is not verified without publications API.
            # Prefer Shopify's published_at / status as a soft signal only.
            shopify_status = str(shopify_product.get("status") or "").lower()
            published_at_raw = shopify_product.get("published_at")
            online_store_published: bool | None
            storefront_url: str | None
            if shopify_status == "active" and published_at_raw and handle:
                online_store_published = True
                storefront_url = f"https://{shop_domain}/products/{handle}"
            elif shopify_status == "draft":
                online_store_published = False
                storefront_url = None
            else:
                online_store_published = None
                storefront_url = None

            variant_map: dict[str, str] = {}
            inventory_map: dict[str, str] = {}
            live_variants = [v for v in product.variants if getattr(v, "is_enabled", True)]
            for index, shop_variant in enumerate(shopify_product.get("variants") or []):
                if index < len(live_variants):
                    key = str(live_variants[index].id)
                else:
                    key = f"default-{index}"
                variant_map[key] = str(shop_variant.get("id"))
                if shop_variant.get("inventory_item_id") is not None:
                    inventory_map[key] = str(shop_variant["inventory_item_id"])

            now = datetime.now(UTC)
            listing_fields = {
                "external_product_id": external_id,
                "external_variant_map": variant_map,
                "inventory_item_map": inventory_map,
                "external_handle": handle,
                "external_graphql_id": graphql_id,
                "shop_domain": shop_domain,
                "storefront_url": storefront_url,
                "admin_url": admin_url,
                "online_store_published": online_store_published,
                "published_at": now if online_store_published else None,
                "status": ListingSyncStatus.SYNCED,
                "last_synced_at": now,
                "last_error": None,
                "content_source": content_source,
                "content_version_id": (
                    listing_overlay.version_id if listing_overlay is not None else None
                ),
            }

            if listing is None:
                listing = await self.listings.create(
                    store_id=store_id,
                    product_id=product_id,
                    **listing_fields,
                )
            else:
                await self.listings.update(listing, **listing_fields)

            await self.shopify.mark_synced(connection)
            return {
                "listing_id": str(listing.id),
                "external_product_id": external_id,
                "external_handle": handle,
                "external_graphql_id": graphql_id,
                "shop_domain": shop_domain,
                "storefront_url": storefront_url,
                "admin_url": admin_url,
                "online_store_published": online_store_published,
                "updated": True,
                "content_source": content_source.value,
                "content_version_id": (
                    str(listing_overlay.version_id) if listing_overlay is not None else None
                ),
            }
        except Exception as exc:
            if listing is not None:
                await self.listings.update(
                    listing,
                    status=ListingSyncStatus.ERROR,
                    last_error=str(exc)[:1000],
                    last_failed_sync_at=datetime.now(UTC),
                )
            await self.shopify.mark_error(connection, str(exc))
            raise

    async def _resolve_listing_content(
        self,
        *,
        listing: StoreListing | None,
        product_id: uuid.UUID,
        listing_overlay: ShopifyListingOverlay | None,
        replace_ai_content: bool,
    ) -> tuple[ShopifyListingOverlay | None, ListingContentSource]:
        """Decide which title/description this publish sends. Runs under the lock."""
        if listing_overlay is not None:
            # A pipeline publish: the merchant explicitly chose this version.
            return listing_overlay, ListingContentSource.AI_VERSION
        live_ai = (
            listing is not None
            and listing.content_source is ListingContentSource.AI_VERSION
            and listing.content_version_id is not None
        )
        if not live_ai:
            return None, ListingContentSource.PRODUCT
        assert listing is not None and listing.content_version_id is not None
        if replace_ai_content:
            self.logger.info(
                "shopify_publish_replaces_ai_content",
                product_id=str(product_id),
                listing_id=str(listing.id),
                replaced_version_id=str(listing.content_version_id),
            )
            return None, ListingContentSource.PRODUCT
        version = await self.versions.get_by_id_for_product(
            product_id=product_id,
            version_id=listing.content_version_id,
            populate_existing=True,
        )
        if version is None:
            raise ConflictError(
                "The AI version live on Shopify can no longer be read. "
                "Choose explicitly whether to replace it with your draft text.",
                details={"reason": "published_ai_content_unavailable"},
            )
        self.logger.info(
            "shopify_publish_preserves_ai_content",
            product_id=str(product_id),
            listing_id=str(listing.id),
            version_id=str(version.id),
        )
        return overlay_from_published_version(version), ListingContentSource.AI_VERSION

    @staticmethod
    async def _create_or_adopt(
        client: ShopifyClient, *, body: dict[str, Any], handle: str
    ) -> dict[str, Any]:
        """Create a new Shopify product, or adopt one that already exists at
        `handle` from an earlier attempt whose local commit never landed.

        Shopify's REST Admin API has no create-idempotency key, so the
        deterministic handle *is* the idempotency mechanism. `publish_product`
        runs behind a Celery task with `task_acks_late` (at-least-once
        delivery — see `app.tasks.integrations.shopify`): if the worker
        crashes after Shopify successfully creates the product but before the
        local transaction commits `StoreListing`, a redelivery finds no
        listing and would otherwise POST again, creating a second Shopify
        product for the same DropPilot product — audit A-04. Searching by
        handle first means the retry finds and adopts the product Shopify
        already has, rather than duplicating it.
        """
        existing = await client.get("/products.json", params={"handle": handle, "limit": 1})
        found = existing.get("products") or []
        if found:
            logger.info("shopify_publish_adopted_existing_product", handle=handle)
            return {"product": found[0]}

        body["product"]["handle"] = handle
        return await client.post("/products.json", json_body=body)

    async def push_inventory(self, *, store_id: uuid.UUID, product_id: uuid.UUID) -> dict[str, Any]:
        client, connection = await self.shopify.client_for_store(store_id)
        listing = await self.listings.get_for_product(store_id=store_id, product_id=product_id)
        if listing is None:
            return await self.publish_product(store_id=store_id, product_id=product_id)

        product = await self._load_product(product_id)
        location_payload = await client.get("/locations.json")
        locations = location_payload.get("locations") or []
        if not locations:
            raise NotFoundError("Shopify shop has no locations for inventory.")
        location_id = locations[0]["id"]

        updated = 0
        for variant in product.variants:
            inv_item = listing.inventory_item_map.get(str(variant.id))
            if not inv_item:
                continue
            await client.post(
                "/inventory_levels/set.json",
                json_body={
                    "location_id": location_id,
                    "inventory_item_id": int(inv_item),
                    "available": int(variant.stock_quantity or 0),
                },
            )
            updated += 1

        await self.listings.update(
            listing,
            last_synced_at=datetime.now(UTC),
            status=ListingSyncStatus.SYNCED,
            last_error=None,
        )
        await self.shopify.mark_synced(connection)
        return {"updated_variants": updated}

    async def push_price(self, *, store_id: uuid.UUID, product_id: uuid.UUID) -> dict[str, Any]:
        """Push sell prices to Shopify variants; records reason on the listing."""
        client, connection = await self.shopify.client_for_store(store_id)
        listing = await self.listings.get_for_product(store_id=store_id, product_id=product_id)
        if listing is None:
            return await self.publish_product(store_id=store_id, product_id=product_id)

        product = await self._load_product(product_id)
        changes: list[dict[str, str]] = []
        for variant in product.variants:
            shopify_variant_id = listing.external_variant_map.get(str(variant.id))
            if not shopify_variant_id:
                continue
            new_price = variant.list_price or product.sell_price
            if new_price is None:
                continue
            previous = listing.external_variant_map.get(f"price:{variant.id}")
            await client.put(
                f"/variants/{shopify_variant_id}.json",
                json_body={"variant": {"id": int(shopify_variant_id), "price": str(new_price)}},
            )
            changes.append(
                {
                    "variant_id": str(variant.id),
                    "previous_price": str(previous) if previous is not None else "",
                    "new_price": str(new_price),
                    "reason": "pricing_engine_sync",
                }
            )

        await self.listings.update(
            listing,
            last_synced_at=datetime.now(UTC),
            last_error=None,
            status=ListingSyncStatus.SYNCED,
        )
        await self.shopify.mark_synced(connection)
        logger.info(
            "shopify_price_pushed",
            store_id=str(store_id),
            product_id=str(product_id),
            changes=len(changes),
        )
        return {"changes": changes}

    async def import_orders(self, *, store_id: uuid.UUID, limit: int = 50) -> dict[str, Any]:
        """Poll recent Shopify orders into DropPilot. No fulfilment mutations."""
        client, connection = await self.shopify.client_for_store(store_id)
        payload = await client.get(
            "/orders.json",
            params={"status": "any", "limit": min(limit, 100)},
        )
        created = 0
        updated = 0
        for raw in payload.get("orders") or []:
            outcome = await self.upsert_order_from_shopify(store_id=store_id, raw=raw)
            if outcome == "created":
                created += 1
            elif outcome == "updated":
                updated += 1
        await self.shopify.mark_synced(connection)
        return {"created": created, "updated": updated, "fetched": len(payload.get("orders") or [])}

    async def upsert_order_from_shopify(self, *, store_id: uuid.UUID, raw: dict[str, Any]) -> str:
        external_id = str(raw.get("id") or "")
        if not external_id:
            return "skipped"

        existing = await self.orders.get_by_external_id(
            source=OrderSource.SHOPIFY, external_id=external_id
        )
        financial = str(raw.get("financial_status") or "unknown")
        fulfillment = str(raw.get("fulfillment_status") or "unfulfilled")
        if financial == "paid":
            payment = PaymentStatus.PAID
        elif financial in {"pending", "authorized"}:
            payment = PaymentStatus.UNPAID
        else:
            payment = PaymentStatus.UNKNOWN
        if fulfillment == "fulfilled":
            fulfillment_status = FulfillmentStatus.FULFILLED
        elif raw.get("cancelled_at"):
            fulfillment_status = FulfillmentStatus.CANCELLED
        elif payment is PaymentStatus.PAID:
            fulfillment_status = FulfillmentStatus.PAID
        else:
            fulfillment_status = FulfillmentStatus.PENDING

        shipping_raw = raw.get("shipping_address")
        shipping: dict[str, Any] = shipping_raw if isinstance(shipping_raw, dict) else {}
        customer_raw = raw.get("customer")
        customer: dict[str, Any] = customer_raw if isinstance(customer_raw, dict) else {}
        buyer = f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip()
        total = raw.get("total_price")
        values = {
            "store_id": store_id,
            "external_status": financial,
            "fulfillment_status": fulfillment_status,
            "payment_status": payment,
            "buyer_name": buyer or None,
            "buyer_country": shipping.get("country_code"),
            "recipient_name": shipping.get("name"),
            "recipient_phone": shipping.get("phone"),
            "address_line1": shipping.get("address1"),
            "address_line2": shipping.get("address2"),
            "city": shipping.get("city"),
            "province": shipping.get("province"),
            "postal_code": shipping.get("zip"),
            "country_code": shipping.get("country_code"),
            "currency": raw.get("currency"),
            "total_amount": Decimal(str(total)) if total is not None else None,
            "last_synced_at": datetime.now(UTC),
        }

        if existing is None:
            await self.orders.create(
                source=OrderSource.SHOPIFY,
                external_id=external_id,
                **values,
            )
            return "created"

        await self.orders.update(existing, **values)
        return "updated"
