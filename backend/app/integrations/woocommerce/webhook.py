"""WooCommerce order webhooks (Track E7, W4b).

**Finding the workspace without a session.** Deliveries go to
``…/webhooks/<tenant id>/<store id>``. As with team invitations (decision
D-013), the tenant in the path only *narrows* the lookup. The store is read
inside that tenant, and the delivery is accepted only if its
``X-WC-Webhook-Signature`` is the HMAC-SHA256 of the body under that store's
own secret. A forged or swapped tenant id finds no store, or the wrong
secret, and gets the same 401 as a bad signature. No cross-tenant query is
on this path (CLAUDE.md §4).

**The payload is a doorbell, not data.** After verification, the order is
fetched afresh from the store (``GET /orders/{id}``) and that response is
upserted. A replayed or out-of-order delivery can therefore never write older
state over newer, and there is no replay cache to fail open or closed.

WooCommerce sends an unsigned ``webhook_id=<n>`` ping when a webhook is
created. It is acknowledged with 200 and does nothing.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from typing import Final

from starlette.requests import Request

from app.core.context import clear_context, set_tenant_id
from app.core.exceptions import AuthenticationError
from app.core.logging import get_logger
from app.core.request_body import read_bounded_body
from app.database.session import transaction
from app.integrations.woocommerce.connection import is_usable, webhook_secret_for
from app.integrations.woocommerce.orders import WooCommerceOrderService
from app.models.store import StorePlatform
from app.repositories.store import StoreRepository

logger = get_logger(__name__)

#: An order payload is a few KiB; this bounds a hostile sender.
MAX_BODY: Final = 262_144


def signature_matches(*, raw_body: bytes, header: str, secret: str) -> bool:
    expected = base64.b64encode(
        hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
    ).decode("ascii")
    return hmac.compare_digest(expected, header.strip())


async def receive_woocommerce_webhook(
    request: Request, *, tenant_id: uuid.UUID, store_id: uuid.UUID
) -> None:
    raw = await read_bounded_body(request, max_bytes=MAX_BODY)
    if raw.startswith(b"webhook_id="):
        return  # creation ping; unsigned by design
    header = request.headers.get("x-wc-webhook-signature", "")
    refused = AuthenticationError("WooCommerce webhook signature is not valid.")
    if not header:
        raise refused

    set_tenant_id(tenant_id)
    try:
        async with transaction() as session:
            store = await StoreRepository(session).get_by_id(store_id)
            if (
                store is None
                or store.platform is not StorePlatform.WOOCOMMERCE
                or not is_usable(store)
            ):
                raise refused
            secret = webhook_secret_for(store)
            if secret is None or not signature_matches(raw_body=raw, header=header, secret=secret):
                raise refused
            try:
                payload = json.loads(raw)
            except ValueError:
                return
            order_id = payload.get("id") if isinstance(payload, dict) else None
            if not isinstance(order_id, int):
                return
            await WooCommerceOrderService(session).refresh_order(store, str(order_id))
            logger.info("woocommerce_webhook_order_refreshed", store_id=str(store.id))
    finally:
        clear_context()


__all__ = ["MAX_BODY", "receive_woocommerce_webhook", "signature_matches"]
