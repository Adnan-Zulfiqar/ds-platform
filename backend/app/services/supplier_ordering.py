"""Placing the AliExpress order behind a customer's channel order (Track F,
decision D-017).

The service decides *whether* an order can be placed and records each step;
the AliExpress call itself runs in the ``supplier_orders.place`` Celery task,
because it must sit between two commits (see ``models/supplier_order.py``).

**Nothing is guessed.** An order is placed only when every line resolves to
exactly one AliExpress SKU, the order is paid, and the address has what
AliExpress requires. Anything else becomes ``needs_review`` with reasons the
merchant can act on. A line from a product DropPilot did not publish, or a
multi-variant product whose sold variant the channel did not identify, is a
reason, not a best guess.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, ValidationError
from app.core.logging import get_logger
from app.integrations.aliexpress.ordering import PlaceAddress, PlaceLine
from app.models.order import FulfillmentStatus, Order, OrderSource, PaymentStatus
from app.models.product import ProductSource
from app.models.supplier_order import FulfilmentSettings, SupplierOrder, SupplierOrderStatus
from app.repositories.order import OrderRepository
from app.repositories.product import ProductRepository, ProductVariantRepository
from app.repositories.supplier_order import FulfilmentSettingsRepository, SupplierOrderRepository
from app.services.base import BaseService

logger = get_logger(__name__)

#: Channel orders DropPilot can fulfil from AliExpress.
CHANNEL_SOURCES = frozenset({OrderSource.SHOPIFY, OrderSource.EBAY, OrderSource.WOOCOMMERCE})

#: AliExpress requires a tax id for these destinations, which DropPilot does
#: not store (buyer data is minimised); the merchant places those by hand.
TAX_ID_COUNTRIES = frozenset({"BR", "CL"})

#: Statuses from which a new placement may start. ``placing`` is absent on
#: purpose: a stuck ``placing`` needs a human to check AliExpress first.
_RESTARTABLE = frozenset({SupplierOrderStatus.NEEDS_REVIEW.value, SupplierOrderStatus.FAILED.value})

#: Orders that left the merchant's hands already.
_ALREADY_HANDLED = frozenset(
    {FulfillmentStatus.FULFILLED, FulfillmentStatus.SHIPPED, FulfillmentStatus.DELIVERED}
)


@dataclass(frozen=True, slots=True)
class Review:
    """Whether an order can be placed now, and exactly what would be sent."""

    reasons: list[str]
    lines: list[PlaceLine]
    address: PlaceAddress | None

    @property
    def ok(self) -> bool:
        return not self.reasons


class SupplierOrderingService(BaseService):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.orders = OrderRepository(session)
        self.products = ProductRepository(session)
        self.variants = ProductVariantRepository(session)
        self.supplier_orders = SupplierOrderRepository(session)
        self.settings_rows = FulfilmentSettingsRepository(session)

    # --- settings ---------------------------------------------------------

    async def settings(self) -> FulfilmentSettings | None:
        return await self.settings_rows.current()

    async def update_settings(
        self,
        *,
        auto_order: bool,
        auto_tracking: bool,
        fallback_shipping_method: str | None,
    ) -> FulfilmentSettings:
        row = await self.settings_rows.current()
        values: dict[str, Any] = {
            "auto_order": auto_order,
            "auto_tracking": auto_tracking,
            "fallback_shipping_method": (fallback_shipping_method or "").strip() or None,
        }
        was_on = bool(row and row.auto_order)
        if auto_order and not was_on:
            values["auto_order_enabled_at"] = datetime.now(UTC)
        elif not auto_order:
            values["auto_order_enabled_at"] = None
        if row is None:
            return await self.settings_rows.create(**values)
        return await self.settings_rows.update(row, **values)

    # --- review -----------------------------------------------------------

    async def review(self, order: Order) -> Review:
        reasons: list[str] = []
        if order.source not in CHANNEL_SOURCES:
            reasons.append("not_a_channel_order")
        if order.fulfillment_status is FulfillmentStatus.CANCELLED:
            reasons.append("order_cancelled")
        if order.fulfillment_status in _ALREADY_HANDLED:
            # Shipped by hand or by another tool before DropPilot saw it:
            # buying it again from AliExpress would send a second parcel.
            reasons.append("order_already_fulfilled")
        if order.fulfillment_status is FulfillmentStatus.REFUNDED:
            reasons.append("order_refunded")
        if order.payment_status is not PaymentStatus.PAID:
            reasons.append("order_not_paid")

        address = self._address(order, reasons)
        lines = await self._lines(order, reasons)
        return Review(reasons=reasons, lines=lines, address=address)

    def _address(self, order: Order, reasons: list[str]) -> PlaceAddress | None:
        missing = [
            name
            for name, value in (
                ("recipient_name", order.recipient_name),
                ("recipient_phone", order.recipient_phone),
                ("address_line1", order.address_line1),
                ("city", order.city),
                ("country_code", order.country_code),
            )
            if not (value or "").strip()
        ]
        reasons.extend(f"missing_{name}" for name in missing)
        country = (order.country_code or "").upper()
        if country in TAX_ID_COUNTRIES:
            reasons.append("tax_id_required")
        if missing:
            return None
        return PlaceAddress(
            full_name=str(order.recipient_name).strip(),
            phone=str(order.recipient_phone).strip(),
            address=str(order.address_line1).strip(),
            address2=(order.address_line2 or "").strip() or None,
            city=str(order.city).strip(),
            province=(order.province or "").strip() or None,
            zip=(order.postal_code or "").strip() or None,
            country=country,
        )

    async def _lines(self, order: Order, reasons: list[str]) -> list[PlaceLine]:
        if not order.items:
            reasons.append("no_order_lines")
            return []
        lines: list[PlaceLine] = []
        line_problems = 0
        for item in order.items:
            if item.quantity <= 0:
                # Removed from the order by an edit: nothing to buy.
                continue
            line_problems += 1  # undone below when the line resolves
            if item.product_id is None:
                reasons.append(f"line_{item.external_item_id or item.id}:not_a_droppilot_product")
                continue
            product = await self.products.get_by_id(item.product_id)
            if product is None or product.source is not ProductSource.ALIEXPRESS:
                reasons.append(f"line_{item.external_item_id or item.id}:not_an_aliexpress_product")
                continue
            variants = [
                v for v in await self.variants.list_for_product(product.id) if v.deleted_at is None
            ]
            chosen = None
            if item.variant_id is not None:
                chosen = next((v for v in variants if v.id == item.variant_id), None)
            elif len(variants) == 1:
                chosen = variants[0]
            if chosen is None:
                reasons.append(f"line_{item.external_item_id or item.id}:variant_unknown")
                continue
            if not (chosen.external_attributes or "").strip():
                reasons.append(f"line_{item.external_item_id or item.id}:no_supplier_sku")
                continue
            line_problems -= 1
            lines.append(
                PlaceLine(
                    product_id=product.external_id,
                    sku_attr=str(chosen.external_attributes),
                    quantity=item.quantity,
                )
            )
        if not lines and not line_problems:
            reasons.append("no_order_lines")  # every line was removed
        return lines

    # --- the button and the switch ------------------------------------------

    async def request(
        self, order_id: uuid.UUID, *, user_id: uuid.UUID | None, trigger: str
    ) -> SupplierOrder:
        """Record the intent to place. ``queued`` when the order passes review,
        ``needs_review`` otherwise. The caller enqueues the task after commit.

        Refuses when an earlier attempt is queued, in flight, or done: the
        state machine, not the merchant's patience, prevents a double order.
        """
        order = await self.orders.get_by_id_or_raise(order_id)
        await self.session.refresh(order, attribute_names=["items"])
        review = await self.review(order)
        status = (
            SupplierOrderStatus.QUEUED.value
            if review.ok
            else SupplierOrderStatus.NEEDS_REVIEW.value
        )
        values: dict[str, Any] = {
            "status": status,
            "trigger": trigger,
            "review_reasons": review.reasons,
            "requested_by_user_id": user_id,
            "error_code": None,
            "error_message": None,
        }
        existing = await self.supplier_orders.for_order(order.id, lock=True)
        if existing is None:
            try:
                async with self.session.begin_nested():
                    row = await self.supplier_orders.create(order_id=order.id, **values)
            except IntegrityError as exc:
                # Two first requests at once (the button and auto mode): the
                # unique constraint lets one through; the other is a 409.
                raise ConflictError(
                    "This order was already sent to AliExpress or is being sent now."
                ) from exc
        elif existing.status in _RESTARTABLE:
            row = await self.supplier_orders.update(existing, **values)
        else:
            raise ConflictError(
                "This order was already sent to AliExpress or is being sent now.",
                details={"status": existing.status},
            )
        logger.info(
            "supplier_order_requested",
            order_id=str(order.id),
            status=status,
            trigger=trigger,
            reasons=len(review.reasons),
        )
        return row

    async def get(self, order_id: uuid.UUID, *, lock: bool = False) -> SupplierOrder | None:
        await self.orders.get_by_id_or_raise(order_id)  # 404 across tenants
        return await self.supplier_orders.for_order(order_id, lock=lock)

    @staticmethod
    def auto_eligible(order: Order, settings: FulfilmentSettings) -> bool:
        """Auto mode only takes orders placed after it was switched on.
        Turning it on must never reach back and buy orders the merchant
        already handled some other way."""
        since = settings.auto_order_enabled_at
        if not settings.auto_order or since is None:
            return False
        placed = order.external_created_at or order.created_at
        return placed is not None and placed >= since

    # --- used by the task, one transaction each -------------------------------

    async def begin_placing(
        self, supplier_order_id: uuid.UUID
    ) -> tuple[SupplierOrder, Review, str | None] | None:
        """Move ``queued`` to ``placing`` and return what to send, or ``None``
        if there is nothing to do (already handled, or no longer valid)."""
        row = await self.supplier_orders.get_locked(supplier_order_id)
        if row is None or row.status != SupplierOrderStatus.QUEUED.value:
            return None
        order = await self.orders.get_by_id_or_raise(row.order_id)
        await self.session.refresh(order, attribute_names=["items"])
        review = await self.review(order)  # the order may have changed since
        if not review.ok or review.address is None:
            await self.supplier_orders.update(
                row, status=SupplierOrderStatus.NEEDS_REVIEW.value, review_reasons=review.reasons
            )
            return None
        settings = await self.settings_rows.current()
        await self.supplier_orders.update(
            row,
            status=SupplierOrderStatus.PLACING.value,
            request_lines=[
                {"productId": line.product_id, "skuAttr": line.sku_attr, "quantity": line.quantity}
                for line in review.lines
            ],
        )
        return row, review, settings.fallback_shipping_method if settings else None

    async def record_unknown(self, supplier_order_id: uuid.UUID, *, reason: str) -> None:
        """AliExpress may or may not have created the order (a timeout, a
        server error, or a crash after the call). The row stays ``placing``,
        which nothing retries, and says why; the merchant checks AliExpress
        and then releases it (:meth:`release`) or leaves it."""
        row = await self.supplier_orders.get_locked(supplier_order_id)
        if row is None:
            return
        await self.supplier_orders.update(
            row,
            error_code="outcome_unknown",
            error_message=(
                f"AliExpress did not answer clearly ({reason}). Check your AliExpress "
                "orders before trying again, so the goods are not bought twice."
            )[:1024],
        )

    async def release(self, order_id: uuid.UUID) -> SupplierOrder:
        """The merchant checked AliExpress and found no order: let them try
        again. Only from ``placing``; everything else has a definite state."""
        await self.orders.get_by_id_or_raise(order_id)  # 404 across tenants
        row = await self.supplier_orders.for_order(order_id, lock=True)
        if row is None or row.status != SupplierOrderStatus.PLACING.value:
            raise ConflictError("Only an order stuck while being sent can be released.")
        logger.info("supplier_order_released", order_id=str(order_id))
        return await self.supplier_orders.update(
            row,
            status=SupplierOrderStatus.FAILED.value,
            error_code="released_by_merchant",
            error_message="Released after checking AliExpress: no order had been created.",
        )

    async def record_outcome(
        self,
        supplier_order_id: uuid.UUID,
        *,
        ok: bool,
        order_ids: list[str],
        error_code: str | None,
        error_message: str | None,
    ) -> SupplierOrder | None:
        row = await self.supplier_orders.get_locked(supplier_order_id)
        if row is None:
            return None
        if ok:
            return await self.supplier_orders.update(
                row,
                status=SupplierOrderStatus.PLACED.value,
                external_order_ids=order_ids,
                placed_at=datetime.now(UTC),
                error_code=None,
                error_message=None,
            )
        return await self.supplier_orders.update(
            row,
            status=SupplierOrderStatus.FAILED.value,
            external_order_ids=order_ids,
            error_code=(error_code or "unknown")[:128],
            error_message=(error_message or "AliExpress did not accept the order.")[:1024],
        )


def ensure_admin_can_place(order: Order) -> None:
    """A channel check the API runs before queuing, so a wrong-source
    request is a 422 rather than a ``needs_review`` row."""
    if order.source not in CHANNEL_SOURCES:
        raise ValidationError("Only Shopify, eBay and WooCommerce orders are placed on AliExpress.")


__all__ = [
    "CHANNEL_SOURCES",
    "TAX_ID_COUNTRIES",
    "Review",
    "SupplierOrderingService",
    "ensure_admin_can_place",
]
