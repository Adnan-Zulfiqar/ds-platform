"""Bringing AliExpress tracking numbers back to the sales channel (Track F,
decision D-017).

For each placed supplier order the ``supplier_orders.sync_tracking`` task
asks ``aliexpress.ds.order.tracking.get`` for a tracking number. When one
appears it is stored, and, if the workspace's auto-tracking switch is on,
sent to the order's channel with the channel's own "mark shipped" code (the
same code the manual ship forms use, so the behaviour is identical and
idempotent on the tracking number). The customer is notified, per the
owner's choice.

**One parcel only, automatically.** An order whose lines came from several
AliExpress sellers produces several AliExpress orders and several parcels.
The channel "mark shipped" calls each take one tracking number for the whole
order, so pushing one number would tell the customer everything shipped
when it had not. Those orders keep their numbers on screen and wait for the
merchant (``error_code = multiple_parcels``).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError, ValidationError
from app.core.logging import get_logger
from app.integrations.aliexpress.ordering import TRACKING_METHOD, Tracking, parse_tracking
from app.integrations.aliexpress.service import AliExpressService
from app.models.order import OrderSource
from app.models.supplier_order import SupplierOrder, SupplierOrderStatus
from app.repositories.order import OrderRepository
from app.repositories.supplier_order import FulfilmentSettingsRepository, SupplierOrderRepository
from app.services.base import BaseService

logger = get_logger(__name__)


class SupplierTrackingService(BaseService):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.orders = OrderRepository(session)
        self.supplier_orders = SupplierOrderRepository(session)
        self.settings_rows = FulfilmentSettingsRepository(session)

    async def check(self, row: SupplierOrder) -> Tracking | None:
        """Ask AliExpress for the parcel's tracking number and store it.
        Returns the tracking when exactly one parcel has one."""
        client = await AliExpressService(self.session).authenticated_client()
        found: list[Tracking] = []
        for ae_order_id in row.external_order_ids:
            payload = await client.call(
                TRACKING_METHOD, {"ae_order_id": ae_order_id, "language": "en_US"}
            )
            found.extend(parse_tracking(payload))
        values: dict[str, object] = {"last_checked_at": datetime.now(UTC)}
        tracking = found[0] if found else None
        if tracking is not None:
            values["tracking_number"] = tracking.number[:128]
            values["tracking_carrier"] = (tracking.carrier or "")[:128] or None
        if len(row.external_order_ids) > 1:
            values["error_code"] = "multiple_parcels"
            values["error_message"] = (
                "This order became several AliExpress orders. Add each parcel's "
                "tracking to the store by hand."
            )
        await self.supplier_orders.update(row, **values)
        return tracking if len(row.external_order_ids) == 1 else None

    async def auto_push_enabled(self) -> bool:
        settings = await self.settings_rows.current()
        return bool(settings and settings.auto_tracking)

    async def push(self, row: SupplierOrder) -> SupplierOrder:
        """Send the stored tracking number to the order's sales channel."""
        if row.tracking_pushed_at is not None:
            return row  # already sent; the channels are idempotent anyway
        if not row.tracking_number:
            raise ValidationError("AliExpress has not given a tracking number yet.")
        if len(row.external_order_ids) > 1:
            raise ValidationError(
                "This order shipped in several parcels; add their tracking in the store."
            )
        order = await self.orders.get_by_id_or_raise(row.order_id)
        carrier = row.tracking_carrier or "Other"
        if order.source is OrderSource.SHOPIFY:
            from app.integrations.shopify.fulfilment import (
                ShopifyFulfilmentService,
                ShopifyTracking,
            )

            await ShopifyFulfilmentService(self.session).mark_shipped(
                order.id,
                ShopifyTracking(
                    company=carrier, number=row.tracking_number, url=None, notify_customer=True
                ),
            )
        elif order.source is OrderSource.WOOCOMMERCE:
            from app.integrations.woocommerce.orders import WooCommerceOrderService

            await WooCommerceOrderService(self.session).mark_shipped(
                order.id,
                company=carrier,
                tracking_number=row.tracking_number,
                tracking_url=None,
                notify_customer=True,
            )
        elif order.source is OrderSource.EBAY:
            from app.integrations.ebay.orders import EbayOrderService

            # eBay validates the carrier against its own list; AliExpress
            # carrier names may not be on it. A refusal is recorded below and
            # the merchant ships from the order page with eBay's code.
            await EbayOrderService(self.session).mark_shipped(
                order.id,
                carrier_code=carrier,
                tracking_number=row.tracking_number,
                shipped_at=None,
            )
        else:
            raise ValidationError("Only Shopify, eBay and WooCommerce orders take tracking.")
        logger.info("supplier_tracking_pushed", order_id=str(order.id), source=order.source.value)
        return await self.supplier_orders.update(
            row,
            status=SupplierOrderStatus.SHIPPED.value,
            tracking_pushed_at=datetime.now(UTC),
            error_code=None,
            error_message=None,
        )

    async def record_push_failure(self, row: SupplierOrder, exc: Exception) -> None:
        code = exc.code if isinstance(exc, AppError) else type(exc).__name__
        await self.supplier_orders.update(
            row,
            error_code=f"tracking_push:{code}"[:128],
            error_message=str(exc)[:1024] or "The store did not accept the tracking number.",
        )


__all__ = ["SupplierTrackingService"]
