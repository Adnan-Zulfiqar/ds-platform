"""Shopify channel sync — publish, inventory, price, order import."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.integrations.shopify.service import ShopifyService
from app.models.order import (
    FulfillmentStatus,
    OrderSource,
    PaymentStatus,
)
from app.models.product import Product
from app.models.shopify import ListingSyncStatus
from app.repositories.order import OrderRepository
from app.repositories.product import ProductRepository
from app.repositories.shopify import StoreListingRepository
from app.services.base import BaseService

logger = get_logger(__name__)


class ShopifySyncService(BaseService):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.shopify = ShopifyService(session)
        self.products = ProductRepository(session)
        self.listings = StoreListingRepository(session)
        self.orders = OrderRepository(session)

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

        Idempotent via :class:`StoreListing` — never creates a second Shopify
        product for the same store/product pair.
        """
        product = await self._load_product(product_id)
        client, connection = await self.shopify.client_for_store(store_id)
        listing = await self.listings.get_for_product(store_id=store_id, product_id=product_id)

        variants_payload: list[dict[str, Any]] = []
        for variant in product.variants:
            if getattr(variant, "deleted_at", None) is not None:
                continue
            price = variant.list_price or product.sell_price
            variants_payload.append(
                {
                    "sku": variant.external_variant_id[:64],
                    "price": str(price or "0"),
                    "inventory_management": "shopify",
                    "inventory_quantity": int(variant.stock_quantity or 0),
                    "option1": variant.label or "Default",
                }
            )
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

        images = [
            {"src": image.url}
            for image in getattr(product, "images", []) or []
            if getattr(image, "url", None)
        ]

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
                payload = await client.post("/products.json", json_body=body)

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
