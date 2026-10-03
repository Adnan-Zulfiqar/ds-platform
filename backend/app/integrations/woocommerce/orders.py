"""WooCommerce orders in (Track E7, W4a).

``GET /orders?modified_after=…`` is paged and upserted into
``orders``/``order_items``, the same tables as the other channels. A
re-import updates rather than duplicates, through the existing
``uq_orders_tenant_source_external`` guarantee.

**The external id is namespaced by store.** WooCommerce order ids are small
integers per site. Two WooCommerce stores in one workspace would collide on
``(tenant, source, external_id)``, so the stored id is
``<store id>:<order id>``. That fits the column's 128 characters.

Line items find their DropPilot product through the W2 SKU (``dp-<id>``).

**Marking shipped (W5).** WooCommerce core has no tracking field; the
tracking plugins each store it differently. So DropPilot sets the order to
``completed`` and adds an order note with the carrier, the number and the
link. The note is shown to the customer when ``notify_customer`` is set.
The status change comes first: it is idempotent, so a retry after the note
failed sends one note, not two. WooCommerce sends its own "order completed"
email on that status change, whatever DropPilot asks; that is the store's
setting, not ours.
Buyer data is what fulfilment needs (recipient, address, phone), as for the
other channels; nothing else from the order is stored.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.integrations.woocommerce.connection import WooCommerceConnectionService
from app.models.order import (
    FulfillmentStatus,
    Order,
    OrderItem,
    OrderSource,
    PaymentStatus,
    Shipment,
    ShipmentStatus,
)
from app.models.store import Store, StorePlatform
from app.repositories.order import OrderRepository
from app.repositories.product import ProductRepository
from app.repositories.store import StoreRepository

logger = get_logger(__name__)

_PAGE_SIZE: Final = 50
#: A manual import reads at most this many pages; older history is out of scope.
_MAX_PAGES: Final = 10
_SKU: Final = re.compile(r"^dp-([0-9a-f-]{36})$")

#: WooCommerce order status → (fulfilment, payment).
_STATUSES: Final[dict[str, tuple[FulfillmentStatus, PaymentStatus]]] = {
    "pending": (FulfillmentStatus.AWAITING_PAYMENT, PaymentStatus.UNPAID),
    "on-hold": (FulfillmentStatus.AWAITING_PAYMENT, PaymentStatus.UNPAID),
    "failed": (FulfillmentStatus.AWAITING_PAYMENT, PaymentStatus.UNPAID),
    "processing": (FulfillmentStatus.PAID, PaymentStatus.PAID),
    "completed": (FulfillmentStatus.SHIPPED, PaymentStatus.PAID),
    "cancelled": (FulfillmentStatus.CANCELLED, PaymentStatus.UNKNOWN),
    "refunded": (FulfillmentStatus.REFUNDED, PaymentStatus.REFUNDED),
}


@dataclass(frozen=True, slots=True)
class OrderImportOutcome:
    fetched: int
    created: int
    updated: int


def _text(mapping: Any, key: str) -> str | None:
    if not isinstance(mapping, Mapping):
        return None
    value = mapping.get(key)
    if isinstance(value, int | float) and not isinstance(value, bool):
        value = str(value)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _decimal(raw: Any) -> Decimal | None:
    try:
        return Decimal(str(raw)) if raw not in (None, "") else None
    except (InvalidOperation, ValueError):
        return None


def _when_gmt(raw: str | None) -> datetime | None:
    """WooCommerce's ``*_gmt`` fields are UTC without an offset."""
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def external_order_id(store: Store, order_id: str) -> str:
    return f"{store.id}:{order_id}"


class WooCommerceOrderService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.orders = OrderRepository(session)
        self.products = ProductRepository(session)
        self.stores = StoreRepository(session)
        self.connections = WooCommerceConnectionService(session)

    async def import_recent(self, store_id: uuid.UUID, *, days: int = 7) -> OrderImportOutcome:
        store = await self.stores.get_by_id(store_id)
        if store is None or store.platform is not StorePlatform.WOOCOMMERCE:
            raise NotFoundError.for_resource("Store", store_id)
        client = self.connections.client_for(store)
        since = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
        fetched = created = updated = 0
        for page in range(1, _MAX_PAGES + 1):
            batch = await client.get(
                "/orders",
                {
                    "modified_after": since,
                    "dates_are_gmt": "true",
                    "per_page": str(_PAGE_SIZE),
                    "page": str(page),
                    "orderby": "id",
                    "order": "asc",
                },
            )
            if not isinstance(batch, list) or not batch:
                break
            for raw in batch:
                if not isinstance(raw, Mapping):
                    continue
                outcome = await self._upsert(store, raw)
                fetched += outcome != "skipped"
                created += outcome == "created"
                updated += outcome == "updated"
            if len(batch) < _PAGE_SIZE:
                break
        logger.info(
            "woocommerce_orders_imported",
            store_id=str(store.id),
            fetched=fetched,
            created=created,
            updated=updated,
        )
        return OrderImportOutcome(fetched=fetched, created=created, updated=updated)

    async def refresh_order(self, store: Store, order_id: str) -> str:
        """W4b: fetch one order's current state from the store and upsert it.
        Used by webhooks, which are treated as a doorbell, not as data."""
        client = self.connections.client_for(store)
        raw = await client.get(f"/orders/{int(order_id)}")
        if not isinstance(raw, Mapping):
            return "skipped"
        return await self._upsert(store, raw)

    async def _upsert(self, store: Store, raw: Mapping[str, Any]) -> str:
        order_id = _text(raw, "id")
        status = _text(raw, "status") or ""
        if order_id is None or status not in _STATUSES:
            # Drafts ("checkout-draft") and unknown custom statuses are skipped.
            return "skipped"
        fulfilment, payment = _STATUSES[status]
        shipping = raw.get("shipping") if isinstance(raw.get("shipping"), Mapping) else {}
        billing = raw.get("billing") if isinstance(raw.get("billing"), Mapping) else {}
        first = _text(shipping, "first_name") or _text(billing, "first_name") or ""
        last = _text(shipping, "last_name") or _text(billing, "last_name") or ""
        recipient = f"{first} {last}".strip() or None
        country = _text(shipping, "country") or _text(billing, "country")
        values: dict[str, Any] = {
            "store_id": store.id,
            "external_status": status[:128],
            "fulfillment_status": fulfilment,
            "payment_status": payment,
            "buyer_name": recipient,
            "buyer_country": country,
            "recipient_name": recipient,
            "recipient_phone": _text(shipping, "phone") or _text(billing, "phone"),
            "address_line1": _text(shipping, "address_1"),
            "address_line2": _text(shipping, "address_2"),
            "city": _text(shipping, "city"),
            "province": _text(shipping, "state"),
            "postal_code": _text(shipping, "postcode"),
            "country_code": country,
            "currency": _text(raw, "currency"),
            "total_amount": _decimal(raw.get("total")),
            "shipping_amount": _decimal(raw.get("shipping_total")),
            "external_created_at": _when_gmt(_text(raw, "date_created_gmt")),
            "paid_at": _when_gmt(_text(raw, "date_paid_gmt")),
            "last_synced_at": datetime.now(UTC),
        }
        external_id = external_order_id(store, order_id)
        existing = await self.orders.get_by_external_id(
            source=OrderSource.WOOCOMMERCE, external_id=external_id
        )
        if existing is None:
            order = await self.orders.create(
                source=OrderSource.WOOCOMMERCE, external_id=external_id, **values
            )
            outcome = "created"
        else:
            order = await self.orders.update(existing, **values)
            outcome = "updated"
        lines = [li for li in (raw.get("line_items") or []) if isinstance(li, Mapping)]
        await self._sync_items(order, lines, currency=values["currency"])
        return outcome

    async def _sync_items(
        self, order: Order, lines: list[Mapping[str, Any]], *, currency: str | None
    ) -> None:
        loaded = await self._load(order.id)
        known = {item.external_item_id: item for item in loaded.items}
        for line in lines:
            line_id = _text(line, "id")
            if line_id is None:
                continue
            quantity = line.get("quantity") if isinstance(line.get("quantity"), int) else 1
            total = _decimal(line.get("total"))
            unit = (
                (total / quantity).quantize(Decimal("0.01"))
                if total is not None and quantity
                else None
            )
            fields: dict[str, Any] = {
                "external_product_id": _text(line, "product_id"),
                "product_id": await self._product_for_sku(_text(line, "sku")),
                "title": (_text(line, "name") or "")[:512] or None,
                "quantity": quantity,
                "unit_price": unit,
                "currency": currency,
            }
            existing = known.get(line_id)
            if existing is None:
                self.session.add(
                    OrderItem(
                        tenant_id=order.tenant_id,
                        order_id=order.id,
                        external_item_id=line_id,
                        **fields,
                    )
                )
            else:
                for key, value in fields.items():
                    setattr(existing, key, value)
        await self.session.flush()

    async def _product_for_sku(self, sku: str | None) -> uuid.UUID | None:
        match = _SKU.match(sku or "")
        if match is None:
            return None
        product = await self.products.get_by_id(uuid.UUID(match.group(1)))
        return product.id if product is not None else None

    async def _load(self, order_id: uuid.UUID, *, lock: bool = False) -> Order:
        query = (
            self.orders._base_query()
            .where(Order.id == order_id)
            .options(selectinload(Order.items), selectinload(Order.shipments))
            .execution_options(populate_existing=True)
        )
        if lock:
            # Two "mark shipped" requests for one order: the second waits,
            # then sees the first one's shipment and sends nothing.
            query = query.with_for_update(of=Order)
        order = (await self.session.execute(query)).scalar_one_or_none()
        if order is None:
            raise NotFoundError("Order not found.")
        return order

    async def mark_shipped(
        self,
        order_id: uuid.UUID,
        *,
        company: str,
        tracking_number: str,
        tracking_url: str | None,
        notify_customer: bool,
    ) -> Shipment:
        order = await self._load(order_id, lock=True)
        if order.source is not OrderSource.WOOCOMMERCE:
            raise ValidationError("Only WooCommerce orders can be marked shipped on WooCommerce.")
        if order.fulfillment_status in (FulfillmentStatus.CANCELLED, FulfillmentStatus.REFUNDED):
            raise ValidationError("This order was cancelled or refunded in WooCommerce.")
        for shipment in order.shipments:
            if shipment.tracking_number == tracking_number:
                return shipment  # already told the store; never twice
        store = await self.stores.get_by_id(order.store_id) if order.store_id else None
        if store is None:
            raise ValidationError("This order's WooCommerce store is no longer connected.")
        client = self.connections.client_for(store)
        remote_id = order.external_id.split(":", 1)[-1]

        await client.put(f"/orders/{remote_id}", {"status": "completed"})
        note = f"Shipped with {company}, tracking number {tracking_number}."
        if tracking_url:
            note += f" Track it: {tracking_url}"
        await client.post(
            f"/orders/{remote_id}/notes", {"note": note, "customer_note": notify_customer}
        )

        shipment = Shipment(
            tenant_id=order.tenant_id,
            order_id=order.id,
            tracking_number=tracking_number,
            carrier=company,
            status=ShipmentStatus.IN_TRANSIT,
            shipped_at=datetime.now(UTC),
        )
        self.session.add(shipment)
        order.fulfillment_status = FulfillmentStatus.SHIPPED
        order.external_status = "completed"
        await self.session.flush()
        logger.info("woocommerce_order_shipped", order_id=str(order.id))
        return shipment


__all__ = ["OrderImportOutcome", "WooCommerceOrderService", "external_order_id"]
