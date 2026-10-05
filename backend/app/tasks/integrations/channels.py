"""After-commit price/stock push to every marketplace channel (Track E7, W3).

Before W3 this hook lived in the eBay task module and queued only eBay
pushes. WooCommerce needs the same trigger points (product edit, pricing,
inventory and supplier syncs), so the hook moved here and fans out per
channel, rather than every caller learning a second function.

Each channel's task finds that channel's listings for the product. A product
with no listing on a channel costs one indexed query in that task and makes
no outbound call.

Shopify joined the fan-out later: its push tasks existed from Phase 8 but
nothing enqueued them, so a supplier or pricing change never reached a
Shopify store on its own. ``enqueue_price_quantity`` is the one list of
channels; the scheduled tasks (inventory, pricing, supplier refresh) call it
directly because they commit their own transaction first.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any

from sqlalchemy import event

from app.core.context import require_tenant_id
from app.tasks.integrations import ebay as ebay_tasks
from app.tasks.integrations import shopify as shopify_tasks
from app.tasks.integrations import woocommerce as woocommerce_tasks


def enqueue_price_quantity(
    tenant_id: uuid.UUID, product_ids: Iterable[uuid.UUID]
) -> dict[str, int]:
    """Queue one push per product on every channel. Call only after the
    change has committed. Returns the count queued per channel."""
    ids = list(dict.fromkeys(product_ids))
    if not ids:
        return {}
    return {
        "ebay": ebay_tasks.enqueue_price_quantity(tenant_id, ids),
        "woocommerce": woocommerce_tasks.enqueue_price_quantity(tenant_id, ids),
        "shopify": shopify_tasks.enqueue_price_quantity(tenant_id, ids),
    }


def push_price_quantity_after_commit(session: Any, product_ids: Iterable[uuid.UUID]) -> None:
    """Queue the pushes once the caller's transaction has committed. Never
    before (the task would read the old values), and never on rollback.

    ``session`` is the request's ``AsyncSession``. The listener goes on its
    sync session, where SQLAlchemy emits the event. The tenant is read now,
    while the request context still holds it.
    """
    ids = list(dict.fromkeys(product_ids))
    if not ids:
        return
    tenant_id = require_tenant_id()

    def on_commit(_session: object) -> None:
        enqueue_price_quantity(tenant_id, ids)

    event.listen(session.sync_session, "after_commit", on_commit, once=True)


__all__ = ["enqueue_price_quantity", "push_price_quantity_after_commit"]
