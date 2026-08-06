"""Shopify channel sync — publish, inventory, price, order import."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.integrations.aliexpress.countries import country_display_name
from app.integrations.shopify.client import ShopifyClient
from app.integrations.shopify.service import ShopifyService
from app.models.order import (
    FulfillmentStatus,
    OrderSource,
    PaymentStatus,
)
from app.models.product import Product
from app.models.shopify import ListingSyncStatus
from app.models.store import Store
from app.repositories.order import OrderRepository
from app.repositories.product import ProductRepository
from app.repositories.shopify import StoreListingRepository
from app.repositories.store import StoreRepository
from app.services.base import BaseService
from app.services.import_destination import country_from_store_settings

logger = get_logger(__name__)


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
        self, *, store_id: uuid.UUID, product_id: uuid.UUID
    ) -> dict[str, Any]:
        """Create or update a Shopify product for a DropPilot catalogue product.

        Idempotent two ways: a local :class:`StoreListing` drives update vs
        create; when no listing exists yet, :meth:`_create_or_adopt` uses a
        deterministic handle so a Celery redelivery after Shopify create /
        before the listing commit adopts the existing product instead of
        duplicating it (audit A-04).
        """
        product = await self._load_product(product_id)
        store = await self.stores.get_by_id_or_raise(store_id)
        self._assert_import_destination_matches_store(product=product, store=store)
        client, connection = await self.shopify.client_for_store(store_id)
        listing = await self.listings.get_for_product(store_id=store_id, product_id=product_id)

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

        body = {
            "product": {
                "title": product.title,
                "body_html": getattr(product, "description", None) or "",
                "status": "active" if product.status.value == "active" else "draft",
                "variants": variants_payload,
                "images": images[:20],
            }
        }

        try:
            if listing is not None:
                payload = await client.put(
                    f"/products/{listing.external_product_id}.json",
                    json_body=body,
                )
            else:
                payload = await self._create_or_adopt(
                    client, body=body, handle=_deterministic_handle(product_id)
                )

            shopify_product = payload.get("product") or {}
            external_id = str(shopify_product.get("id") or "")
            if not external_id:
                raise NotFoundError("Shopify did not return a product id.")

            variant_map: dict[str, str] = {}
            inventory_map: dict[str, str] = {}
            live_variants = list(product.variants)
            for index, shop_variant in enumerate(shopify_product.get("variants") or []):
                if index < len(live_variants):
                    key = str(live_variants[index].id)
                else:
                    key = f"default-{index}"
                variant_map[key] = str(shop_variant.get("id"))
                if shop_variant.get("inventory_item_id") is not None:
                    inventory_map[key] = str(shop_variant["inventory_item_id"])

            if listing is None:
                listing = await self.listings.create(
                    store_id=store_id,
                    product_id=product_id,
                    external_product_id=external_id,
                    external_variant_map=variant_map,
                    inventory_item_map=inventory_map,
                    status=ListingSyncStatus.SYNCED,
                    last_synced_at=datetime.now(UTC),
                    last_error=None,
                )
            else:
                await self.listings.update(
                    listing,
                    external_product_id=external_id,
                    external_variant_map=variant_map,
                    inventory_item_map=inventory_map,
                    status=ListingSyncStatus.SYNCED,
                    last_synced_at=datetime.now(UTC),
                    last_error=None,
                )

            await self.shopify.mark_synced(connection)
            return {
                "listing_id": str(listing.id),
                "external_product_id": external_id,
                "updated": True,
            }
        except Exception as exc:
            if listing is not None:
                await self.listings.update(
                    listing,
                    status=ListingSyncStatus.ERROR,
                    last_error=str(exc)[:1000],
                )
            await self.shopify.mark_error(connection, str(exc))
            raise

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
