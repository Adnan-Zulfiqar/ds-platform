"""After-commit price/stock push to every marketplace channel (Track E7, W3).

Before W3 this hook lived in the eBay task module and queued only eBay
pushes. WooCommerce needs the same trigger points (product edit, pricing,
inventory and supplier syncs), so the hook moved here and fans out per
channel, rather than every caller learning a second function.

Each channel's task finds that channel's listings for the product. A product
with no listing on a channel costs one indexed query in that task and makes
no outbound call. Shopify is not here: its price and inventory pushes have
their own path (``tasks/integrations/shopify.py``).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any

from sqlalchemy import event

from app.core.context import require_tenant_id
from app.tasks.integrations import ebay as ebay_tasks
from app.tasks.integrations import woocommerce as woocommerce_tasks


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
        ebay_tasks.enqueue_price_quantity(tenant_id, ids)
        woocommerce_tasks.enqueue_price_quantity(tenant_id, ids)

    event.listen(session.sync_session, "after_commit", on_commit, once=True)


__all__ = ["push_price_quantity_after_commit"]
