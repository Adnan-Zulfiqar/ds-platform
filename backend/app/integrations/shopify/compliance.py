"""Shopify's mandatory privacy webhooks (``customers/data_request``,
``customers/redact``, ``shop/redact``).

Shopify requires every public app to answer these three topics, and sends
them to a URL set in the Partner Dashboard rather than one the app
subscribes to. They arrive on the same HMAC-verified receiver as every other
Shopify webhook.

**Why this is the first buyer erasure in the codebase.** Buyer erasure by
*name or email* is refused in ``services/data_subject_erasure.py`` because
``orders`` holds no buyer identifier and a name match would hit the wrong
customer. Shopify's redact payload is different in kind: it names the exact
Shopify order ids to redact, and DropPilot stores that id as
``orders.external_id``. Matching on it, inside the one tenant and the one
store the shop belongs to, cannot touch anyone else's record.

A data request is **referred, not answered**: the merchant is the controller
of their shop's customer data, so the workspace gets an in-app notification
naming the Shopify customer and order ids, and the merchant responds to
Shopify's customer. The notification carries ids only, never the customer's
email or phone.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.logging import get_logger
from app.models.notification import NotificationKind
from app.repositories.order import OrderRepository
from app.services.base import BaseService
from app.services.notification_service import NotificationService

logger = get_logger(__name__)

COMPLIANCE_TOPICS = frozenset(
    {
        "customers-data_request",
        "customers/data_request",
        "customers-redact",
        "customers/redact",
        "shop-redact",
        "shop/redact",
    }
)


def _ids(value: Any) -> list[str]:
    """Shopify sends order ids as integers; ``orders.external_id`` is text."""
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, int | str)]


class ShopifyComplianceService(BaseService):
    """Runs inside the webhook's tenant context, bound to one store."""

    def __init__(self, session: Any) -> None:
        super().__init__(session)
        self.orders = OrderRepository(session)
        self.notifications = NotificationService(session)

    async def handle(self, topic: str, payload: dict[str, Any], *, store_id: uuid.UUID) -> int:
        normalised = topic.replace("/", "-")
        if normalised == "customers-redact":
            return await self.redact_customer(payload, store_id=store_id)
        if normalised == "shop-redact":
            return await self.redact_shop(store_id=store_id)
        if normalised == "customers-data_request":
            await self.refer_data_request(payload, store_id=store_id)
            return 0
        return 0

    async def redact_customer(self, payload: dict[str, Any], *, store_id: uuid.UUID) -> int:
        order_ids = _ids(payload.get("orders_to_redact"))
        if not order_ids:
            # Shopify lists the orders; with none listed there is nothing of
            # this customer's that DropPilot can identify. Never widen to a
            # name match.
            logger.info("shopify_customer_redact_no_orders", store_id=str(store_id))
            return 0
        redacted = await self.orders.redact_buyer_fields(store_id=store_id, external_ids=order_ids)
        logger.info(
            "shopify_customer_redacted",
            store_id=str(store_id),
            orders_listed=len(order_ids),
            orders_redacted=redacted,
        )
        return redacted

    async def redact_shop(self, *, store_id: uuid.UUID) -> int:
        """48 hours after uninstall, Shopify asks for the shop's data to go.
        The connection and token were already deleted on uninstall; what
        remains that is personal is the buyer detail on the shop's orders."""
        redacted = await self.orders.redact_buyer_fields(store_id=store_id, external_ids=None)
        logger.info("shopify_shop_redacted", store_id=str(store_id), orders_redacted=redacted)
        return redacted

    async def refer_data_request(self, payload: dict[str, Any], *, store_id: uuid.UUID) -> None:
        customer = payload.get("customer") if isinstance(payload.get("customer"), dict) else {}
        customer_id = str(customer.get("id", "")) if customer else ""
        request = (
            payload.get("data_request") if isinstance(payload.get("data_request"), dict) else {}
        )
        order_ids = _ids(payload.get("orders_requested"))
        await self.notifications.notify(
            kind=NotificationKind.INFO,
            title="A Shopify customer asked for their data",
            body=(
                "Shopify forwarded a customer data request for your store. You are the "
                "controller of your customers' data: reply to the customer through "
                f"Shopify. DropPilot holds buyer details on {len(order_ids)} of their "
                "order(s), visible under Orders."
            ),
            href="/orders",
            payload={
                "provider": "shopify",
                "store_id": str(store_id),
                "customer_id": customer_id,
                "orders_requested": order_ids,
                "data_request_id": str(request.get("id", "")) if request else "",
            },
        )
        logger.info(
            "shopify_customer_data_request_referred",
            store_id=str(store_id),
            orders_requested=len(order_ids),
        )


__all__ = ["COMPLIANCE_TOPICS", "ShopifyComplianceService"]
