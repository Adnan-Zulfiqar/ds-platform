"""Track E1: tell Shopify an order has shipped, with tracking.

Shopify's fulfilment model works on *fulfillment orders* (one per location
holding the goods). The merchant enters a carrier and tracking number for a
Shopify order; this service finds the order's open fulfillment orders and
creates one fulfilment covering them (``fulfillmentCreate``, Admin GraphQL),
then records a ``Shipment`` and marks the order shipped.

Why manual, not automatic: the supplier's tracking arrives on the supplier
order (an AliExpress order row), and nothing in the data model links that to
the customer's Shopify order. Pushing it automatically would mean guessing
the link. The merchant, who knows which parcel is which, makes it.

Scopes: ``read_/write_merchant_managed_fulfillment_orders`` (added to the
default ``SHOPIFY_SCOPES``). A store connected before this change lacks them
and gets a clear "reconnect Shopify" message rather than a generic failure.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.integrations.shopify.exceptions import ShopifyResponseError
from app.integrations.shopify.service import ShopifyService
from app.models.order import FulfillmentStatus, Order, OrderSource, Shipment, ShipmentStatus
from app.repositories.order import OrderRepository

logger = get_logger(__name__)

#: Fulfillment orders Shopify will accept a fulfilment for.
_FULFILLABLE: Final = frozenset({"OPEN", "IN_PROGRESS"})

FULFILLMENT_ORDERS_QUERY: Final = """
query DropPilotFulfillmentOrders($id: ID!) {
  order(id: $id) {
    fulfillmentOrders(first: 20) {
      nodes { id status }
    }
  }
}
"""

FULFILLMENT_CREATE_MUTATION: Final = """
mutation DropPilotFulfillmentCreate($fulfillment: FulfillmentInput!) {
  fulfillmentCreate(fulfillment: $fulfillment) {
    fulfillment { id status }
    userErrors { field message }
  }
}
"""


class ShopifyFulfilmentRejectedError(ValidationError):
    code = "shopify_fulfilment_rejected"
    message = "Shopify did not accept the fulfilment."


class ShopifyFulfilmentScopeError(ValidationError):
    code = "shopify_fulfilment_scope_missing"
    message = (
        "This Shopify store was connected before DropPilot could mark orders as "
        "shipped. Reconnect it in Settings → Integrations to grant that permission."
    )


@dataclass(frozen=True, slots=True)
class ShopifyTracking:
    company: str
    number: str
    url: str | None
    notify_customer: bool


def _is_access_denied(exc: ShopifyResponseError) -> bool:
    errors = (getattr(exc, "details", None) or {}).get("errors") or []
    for error in errors if isinstance(errors, list) else []:
        if not isinstance(error, dict):
            continue
        code = str((error.get("extensions") or {}).get("code") or "")
        if code == "ACCESS_DENIED" or "access denied" in str(error.get("message", "")).lower():
            return True
    return False


class ShopifyFulfilmentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.orders = OrderRepository(session)
        self.shopify = ShopifyService(session)

    async def mark_shipped(self, order_id: uuid.UUID, tracking: ShopifyTracking) -> Shipment:
        order = await self._load_locked(order_id)
        if order.source is not OrderSource.SHOPIFY:
            raise ValidationError("Only Shopify orders can be marked shipped on Shopify.")
        if order.fulfillment_status is FulfillmentStatus.CANCELLED:
            raise ValidationError("This order was cancelled on Shopify.")
        if order.store_id is None:
            raise ValidationError("This order is not linked to a connected Shopify store.")
        for shipment in order.shipments:
            if shipment.tracking_number == tracking.number:
                return shipment  # Shopify already has it; never twice

        client, _ = await self.shopify.client_for_store(order.store_id)
        order_gid = f"gid://shopify/Order/{order.external_id}"
        try:
            payload = await client.graphql(FULFILLMENT_ORDERS_QUERY, variables={"id": order_gid})
            nodes = (
                ((payload.get("data") or {}).get("order") or {}).get("fulfillmentOrders") or {}
            ).get("nodes") or []
            open_ids = [
                str(node["id"])
                for node in nodes
                if isinstance(node, dict) and node.get("status") in _FULFILLABLE and node.get("id")
            ]
            if not open_ids:
                raise ValidationError("Nothing on this order is left to fulfil on Shopify.")
            tracking_info: dict[str, Any] = {"company": tracking.company, "number": tracking.number}
            if tracking.url:
                tracking_info["url"] = tracking.url
            result = await client.graphql(
                FULFILLMENT_CREATE_MUTATION,
                variables={
                    "fulfillment": {
                        "lineItemsByFulfillmentOrder": [
                            {"fulfillmentOrderId": fid} for fid in open_ids
                        ],
                        "trackingInfo": tracking_info,
                        "notifyCustomer": tracking.notify_customer,
                    }
                },
            )
        except ShopifyResponseError as exc:
            if _is_access_denied(exc):
                raise ShopifyFulfilmentScopeError() from exc
            raise
        created = ((result.get("data") or {}).get("fulfillmentCreate")) or {}
        user_errors = [
            str(e.get("message"))
            for e in created.get("userErrors") or []
            if isinstance(e, dict) and e.get("message")
        ]
        if user_errors or not created.get("fulfillment"):
            raise ShopifyFulfilmentRejectedError(
                "Shopify did not accept the fulfilment: " + " ".join(user_errors[:3])
                if user_errors
                else None
            )

        now = datetime.now(UTC)
        shipment = Shipment(
            tenant_id=order.tenant_id,
            order_id=order.id,
            tracking_number=tracking.number,
            carrier=tracking.company,
            status=ShipmentStatus.IN_TRANSIT,
            shipped_at=now,
        )
        self.session.add(shipment)
        order.fulfillment_status = FulfillmentStatus.SHIPPED
        await self.session.flush()
        logger.info("shopify_order_fulfilled", order_id=str(order.id))
        return shipment

    async def _load_locked(self, order_id: uuid.UUID) -> Order:
        # Locked: a double click must not create two fulfilments on Shopify.
        result = await self.session.execute(
            self.orders._base_query()
            .where(Order.id == order_id)
            .options(selectinload(Order.shipments))
            .with_for_update(of=Order)
            .execution_options(populate_existing=True)
        )
        order = result.scalar_one_or_none()
        if order is None:
            raise NotFoundError("Order not found.")
        return order


__all__ = [
    "ShopifyFulfilmentRejectedError",
    "ShopifyFulfilmentScopeError",
    "ShopifyFulfilmentService",
    "ShopifyTracking",
]
