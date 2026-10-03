"""EBAY-C5: eBay orders in, shipments out.

* **Import** — ``getOrders`` filtered on last-modified time, paged, upserted
  into ``orders``/``order_items`` keyed on eBay's order id (the existing
  ``uq_orders_tenant_source_external`` guarantee makes a re-import an
  update). Line items find their product through the C3 SKU (``dp-<id>``).
* **Shipment** — ``createShippingFulfillment`` with carrier and tracking for
  every line item, then a ``Shipment`` row. Repeating it with the same
  tracking number is a no-op, so a double click cannot tell eBay twice.
* **Cancellation** — read, not initiated: an order eBay reports cancelled is
  marked cancelled here. Seller-initiated cancellation is eBay's Post-Order
  API, a separate API family not in this milestone.

Buyer data is the minimum fulfilment needs (recipient, address, phone) plus
the buyer's username, kept only so eBay's account-deletion notice for that
buyer can find the order (``EbayOrderBuyersOwner`` anonymises it).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.integrations.ebay.connection import EbayConnectionService
from app.integrations.ebay.exceptions import (
    EbayNotConnectedError,
    EbaySellerApiUnavailableError,
)
from app.integrations.ebay.seller_setup import EBAY_MARKETPLACES, EbaySellerClient
from app.models.order import (
    FulfillmentStatus,
    Order,
    OrderItem,
    OrderSource,
    PaymentStatus,
    Shipment,
    ShipmentStatus,
)
from app.models.store import StorePlatform
from app.repositories.order import OrderRepository
from app.repositories.product import ProductRepository
from app.repositories.store import StoreRepository

logger = get_logger(__name__)

#: Page size for getOrders (eBay allows up to 200; 50 keeps each page quick).
_PAGE_SIZE = 50
#: A manual import reads at most this many orders; older history is out of scope.
_MAX_PAGES = 10
#: ``dp-<country>-<id>`` (current) or ``dp-<id>`` (the first C3 format).
_SKU = re.compile(r"^dp-(?:[a-z]{2}-)?([0-9a-f-]{36})$")


@dataclass(frozen=True, slots=True)
class OrderImportOutcome:
    fetched: int
    created: int
    updated: int


def _text(mapping: Any, key: str) -> str | None:
    if not isinstance(mapping, Mapping):
        return None
    value = mapping.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _money(amount: Any) -> tuple[Decimal | None, str | None]:
    if not isinstance(amount, Mapping):
        return None, None
    try:
        value = Decimal(str(amount.get("value")))
    except (InvalidOperation, ValueError):
        value = None
    return value, _text(amount, "currency")


def _when(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def map_statuses(raw: Mapping[str, Any]) -> tuple[FulfillmentStatus, PaymentStatus]:
    """eBay's three status fields → the platform's two."""
    payment_raw = _text(raw, "orderPaymentStatus") or ""
    payment = {
        "PAID": PaymentStatus.PAID,
        "PARTIALLY_REFUNDED": PaymentStatus.PAID,
        "FULLY_REFUNDED": PaymentStatus.REFUNDED,
        "PENDING": PaymentStatus.UNPAID,
        "FAILED": PaymentStatus.UNPAID,
    }.get(payment_raw, PaymentStatus.UNKNOWN)
    cancel = raw.get("cancelStatus")
    if _text(cancel, "cancelState") == "CANCELED":
        return FulfillmentStatus.CANCELLED, payment
    if payment is PaymentStatus.REFUNDED:
        return FulfillmentStatus.REFUNDED, payment
    fulfilment_raw = _text(raw, "orderFulfillmentStatus")
    if fulfilment_raw == "FULFILLED":
        return FulfillmentStatus.SHIPPED, payment
    if fulfilment_raw == "IN_PROGRESS":
        return FulfillmentStatus.PROCESSING, payment
    if payment is PaymentStatus.PAID:
        return FulfillmentStatus.PAID, payment
    return FulfillmentStatus.AWAITING_PAYMENT, payment


class EbayFulfillmentClient(EbaySellerClient):
    """The Fulfillment API calls C5 makes, over the C2 client's transport."""

    async def orders_modified_since(self, since: datetime, *, offset: int) -> tuple[list[Any], int]:
        stamp = since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        response = await self._request(
            "GET",
            "/sell/fulfillment/v1/order",
            call="get_orders",
            params={
                "filter": f"lastmodifieddate:[{stamp}..]",
                "limit": str(_PAGE_SIZE),
                "offset": str(offset),
            },
        )
        if response.status_code != httpx.codes.OK:
            self._raise_for(response, call="get_orders")
        try:
            body = response.json()
        except ValueError as exc:
            raise EbaySellerApiUnavailableError("eBay returned a non-JSON response.") from exc
        orders = body.get("orders") if isinstance(body, Mapping) else None
        total = body.get("total") if isinstance(body, Mapping) else 0
        return (orders if isinstance(orders, list) else []), int(total or 0)

    async def create_shipping_fulfillment(self, order_id: str, payload: Mapping[str, Any]) -> None:
        response = await self._request(
            "POST",
            f"/sell/fulfillment/v1/order/{quote(order_id, safe='')}/shipping_fulfillment",
            call="create_shipping_fulfillment",
            json=payload,
        )
        if response.status_code not in (httpx.codes.CREATED, httpx.codes.OK):
            self._raise_for_listing(response, call="create_shipping_fulfillment")


class EbayOrderService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.orders = OrderRepository(session)
        self.products = ProductRepository(session)
        self.stores = StoreRepository(session)
        self.connections = EbayConnectionService(session)

    async def _client(self) -> EbayFulfillmentClient:
        connection = await self.connections.get_connection()
        if connection is None or not connection.is_usable:
            raise EbayNotConnectedError()
        return EbayFulfillmentClient(await self.connections.access_token_for(connection))

    async def import_recent(self, *, days: int = 7) -> OrderImportOutcome:
        client = await self._client()
        since = datetime.now(UTC) - timedelta(days=days)
        fetched = created = updated = 0
        for page in range(_MAX_PAGES):
            orders, total = await client.orders_modified_since(since, offset=page * _PAGE_SIZE)
            for raw in orders:
                if not isinstance(raw, Mapping):
                    continue
                outcome = await self._upsert(raw)
                fetched += 1
                created += outcome == "created"
                updated += outcome == "updated"
            if (page + 1) * _PAGE_SIZE >= total or not orders:
                break
        logger.info("ebay_orders_imported", fetched=fetched, created=created, updated=updated)
        return OrderImportOutcome(fetched=fetched, created=created, updated=updated)

    async def _upsert(self, raw: Mapping[str, Any]) -> str:
        external_id = _text(raw, "orderId")
        if external_id is None:
            return "skipped"
        fulfilment, payment = map_statuses(raw)
        instructions = raw.get("fulfillmentStartInstructions")
        first = instructions[0] if isinstance(instructions, list) and instructions else {}
        ship_to = (
            first.get("shippingStep", {}).get("shipTo", {}) if isinstance(first, Mapping) else {}
        )
        address = ship_to.get("contactAddress", {}) if isinstance(ship_to, Mapping) else {}
        phone = ship_to.get("primaryPhone", {}) if isinstance(ship_to, Mapping) else {}
        summary = (
            raw.get("pricingSummary") if isinstance(raw.get("pricingSummary"), Mapping) else {}
        )
        total, currency = _money(summary.get("total") if isinstance(summary, Mapping) else None)
        shipping, _ = _money(summary.get("deliveryCost") if isinstance(summary, Mapping) else None)
        line_items = [li for li in (raw.get("lineItems") or []) if isinstance(li, Mapping)]
        marketplace = next(
            (
                m
                for li in line_items
                if (m := _text(li, "listingMarketplaceId")) in EBAY_MARKETPLACES
            ),
            None,
        )
        store = (
            await self.stores.get_by_slug(
                f"ebay-marketplace-{EBAY_MARKETPLACES[marketplace].country.lower()}"
            )
            if marketplace
            else None
        )
        if store is not None and store.platform is not StorePlatform.EBAY:
            store = None  # a hand-made store holds the slug; never attach to it
        recipient = _text(ship_to, "fullName")
        values: dict[str, Any] = {
            "store_id": store.id if store else None,
            "external_status": (_text(raw, "orderFulfillmentStatus") or "")[:128] or None,
            "fulfillment_status": fulfilment,
            "payment_status": payment,
            "buyer_name": recipient,
            "marketplace_buyer_username": (_text(raw.get("buyer"), "username") or "")[:64] or None,
            "buyer_country": _text(address, "countryCode"),
            "recipient_name": recipient,
            "recipient_phone": _text(phone, "phoneNumber"),
            "address_line1": _text(address, "addressLine1"),
            "address_line2": _text(address, "addressLine2"),
            "city": _text(address, "city"),
            "province": _text(address, "stateOrProvince"),
            "postal_code": _text(address, "postalCode"),
            "country_code": _text(address, "countryCode"),
            "currency": currency,
            "total_amount": total,
            "shipping_amount": shipping,
            "external_created_at": _when(_text(raw, "creationDate")),
            "last_synced_at": datetime.now(UTC),
        }
        existing = await self.orders.get_by_external_id(
            source=OrderSource.EBAY, external_id=external_id
        )
        if existing is None:
            order = await self.orders.create(
                source=OrderSource.EBAY, external_id=external_id, **values
            )
            outcome = "created"
        else:
            order = await self.orders.update(existing, **values)
            outcome = "updated"
        await self._sync_items(order, line_items)
        return outcome

    async def _sync_items(self, order: Order, line_items: list[Mapping[str, Any]]) -> None:
        loaded = await self._load(order.id)
        known = {item.external_item_id: item for item in loaded.items}
        for line in line_items:
            line_id = _text(line, "lineItemId")
            if line_id is None:
                continue
            quantity = line.get("quantity") if isinstance(line.get("quantity"), int) else 1
            cost, currency = _money(line.get("lineItemCost"))
            unit = (
                (cost / quantity).quantize(Decimal("0.01"))
                if cost is not None and quantity
                else None
            )
            product_id = await self._product_for_sku(_text(line, "sku"))
            fields: dict[str, Any] = {
                "external_product_id": _text(line, "legacyItemId"),
                "product_id": product_id,
                "title": (_text(line, "title") or "")[:512] or None,
                "quantity": quantity,
                "unit_price": unit,
                "currency": currency,
                "external_status": _text(line, "lineItemFulfillmentStatus"),
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
            # Serialises two "mark shipped" requests for one order: the second
            # waits, then sees the first one's shipment and sends nothing.
            query = query.with_for_update(of=Order)
        result = await self.session.execute(query)
        order = result.scalar_one_or_none()
        if order is None:
            raise NotFoundError("Order not found.")
        return order

    async def mark_shipped(
        self,
        order_id: uuid.UUID,
        *,
        carrier_code: str,
        tracking_number: str,
        shipped_at: datetime | None,
    ) -> Shipment:
        order = await self._load(order_id, lock=True)
        if order.source is not OrderSource.EBAY:
            raise ValidationError("Only eBay orders can be marked shipped on eBay.")
        if order.fulfillment_status is FulfillmentStatus.CANCELLED:
            raise ValidationError("This order was cancelled on eBay.")
        for shipment in order.shipments:
            if shipment.tracking_number == tracking_number:
                return shipment  # already told eBay; never twice
        line_items = [
            {"lineItemId": item.external_item_id, "quantity": item.quantity}
            for item in order.items
            if item.external_item_id
        ]
        if not line_items:
            raise ValidationError("This order has no eBay line items to ship.")
        when = shipped_at or datetime.now(UTC)
        client = await self._client()
        await client.create_shipping_fulfillment(
            order.external_id,
            {
                "lineItems": line_items,
                "shippedDate": when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "shippingCarrierCode": carrier_code,
                "trackingNumber": tracking_number,
            },
        )
        shipment = Shipment(
            tenant_id=order.tenant_id,
            order_id=order.id,
            tracking_number=tracking_number,
            carrier=carrier_code,
            status=ShipmentStatus.IN_TRANSIT,
            shipped_at=when,
        )
        self.session.add(shipment)
        order.fulfillment_status = FulfillmentStatus.SHIPPED
        await self.session.flush()
        logger.info("ebay_order_shipped", order_id=str(order.id))
        return shipment


__all__ = [
    "EbayFulfillmentClient",
    "EbayOrderService",
    "OrderImportOutcome",
    "map_statuses",
]
