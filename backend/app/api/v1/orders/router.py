"""Order endpoints.

Thin, like every router here: validate, delegate, return. Tenant isolation is
applied inside ``TenantScopedRepository`` and authorization runs as a
dependency, so neither can be forgotten by editing a handler carelessly.

**Ordering matters in this module.** ``/orders/statistics`` and
``/orders/sync`` are declared before ``/orders/{order_id}`` because FastAPI
matches in declaration order, and the reverse would make "statistics" parse as
an order id.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status

from app.api.deps import DbSession, RequireAdmin, RequireViewer
from app.models.order import FulfillmentStatus, Order, OrderSource
from app.repositories.order import OrderRepository
from app.schemas.common import ListQueryParams, Page, list_query_params
from app.schemas.order import (
    OrderDetailRead,
    OrderItemRead,
    OrderRead,
    OrderStatisticsRead,
    OrderSyncRequest,
    OrderSyncRunRead,
    OrderTimelineEntryRead,
    ShipmentRead,
    TrackingEventRead,
)
from app.services.order_sync import OrderSyncService

router = APIRouter(prefix="/orders", tags=["orders"])


def _to_read(order: Order) -> OrderRead:
    return OrderRead(
        **{
            field: getattr(order, field)
            for field in OrderRead.model_fields
            if field != "item_count"
        },
        item_count=len(order.items),
    )


def _to_detail(order: Order) -> OrderDetailRead:
    """Project an order and its children into the detail response.

    Fields are copied through explicit schemas rather than validating the ORM
    object wholesale, so adding a column can never leak into a response by
    accident.
    """
    return OrderDetailRead(
        **_to_read(order).model_dump(),
        buyer_country=order.buyer_country,
        recipient_name=order.recipient_name,
        recipient_phone=order.recipient_phone,
        address_line1=order.address_line1,
        address_line2=order.address_line2,
        city=order.city,
        province=order.province,
        postal_code=order.postal_code,
        shipping_amount=order.shipping_amount,
        paid_at=order.paid_at,
        delivered_at=order.delivered_at,
        items=[OrderItemRead.model_validate(item) for item in order.items],
        shipments=[
            ShipmentRead(
                **{
                    field: getattr(shipment, field)
                    for field in ShipmentRead.model_fields
                    if field != "tracking_events"
                },
                tracking_events=[
                    TrackingEventRead.model_validate(event) for event in shipment.tracking_events
                ],
            )
            for shipment in order.shipments
        ],
    )


@router.get(
    "",
    response_model=Page[OrderRead],
    summary="List orders in the current tenant",
)
async def list_orders(
    session: DbSession,
    params: Annotated[ListQueryParams, Depends(list_query_params)],
    _authorized: RequireViewer,
    order_status: Annotated[
        FulfillmentStatus | None, Query(alias="status", description="Fulfilment status filter.")
    ] = None,
    source: Annotated[
        OrderSource | None,
        Query(description="Order origin. Store-level filtering arrives with the stores module."),
    ] = None,
    date_from: Annotated[
        datetime | None, Query(alias="dateFrom", description="Placed on or after.")
    ] = None,
    date_to: Annotated[
        datetime | None, Query(alias="dateTo", description="Placed on or before.")
    ] = None,
) -> Page[OrderRead]:
    """Return a page of the tenant's orders.

    Readable by every real role: what has been ordered is operational
    information the whole team needs, even those who cannot change it.
    """
    orders, total = await OrderRepository(session).list_orders(
        params,
        fulfillment_status=order_status,
        source=source,
        date_from=date_from,
        date_to=date_to,
    )
    return Page[OrderRead].build(
        items=[_to_read(order) for order in orders],
        page=params.page,
        size=params.size,
        total_items=total,
    )


@router.get(
    "/statistics",
    response_model=OrderStatisticsRead,
    summary="Order and synchronisation statistics",
)
async def order_statistics(
    session: DbSession,
    _authorized: RequireViewer,
) -> OrderStatisticsRead:
    """The live status dashboard payload.

    Everything is computed from this platform's own tables plus one Redis
    counter; ``webhookEventsReceived`` is null when Redis cannot answer, which
    is the truth rather than a zero that would look like silence.
    """
    stats = await OrderSyncService(session).statistics()
    last_sync = stats.pop("last_sync")
    return OrderStatisticsRead(
        **stats,
        last_sync=OrderSyncRunRead.model_validate(last_sync) if last_sync else None,
    )


@router.post(
    "/sync",
    response_model=OrderSyncRunRead,
    status_code=status.HTTP_201_CREATED,
    summary="Synchronise orders from AliExpress",
)
async def sync_orders(
    session: DbSession,
    principal: RequireAdmin,
    payload: OrderSyncRequest | None = None,
) -> OrderSyncRunRead:
    """Run one incremental synchronisation for this workspace.

    Admin or owner: a sync spends the tenant's supplier quota, which is not a
    viewer's decision.

    **Idempotent.** Orders are upserted by supplier identifier; running the
    same window twice updates rather than duplicates. A run already in flight
    returns 409 rather than starting a second conversation with the supplier.

    Runs inline rather than through the task queue, like single-product
    import: the default window is small, and the caller deserves to be told
    whether it worked. The scheduled Celery sweep exists for the recurring
    case.
    """
    run = await OrderSyncService(session).sync_orders(
        since_days=payload.since_days if payload else 7,
        requested_by_user_id=principal.user_id,
    )
    return OrderSyncRunRead.model_validate(run)


@router.get(
    "/{order_id}",
    response_model=OrderDetailRead,
    summary="Get an order with items, shipments, and tracking",
)
async def get_order(
    session: DbSession,
    _authorized: RequireViewer,
    order_id: Annotated[uuid.UUID, Path()],
) -> OrderDetailRead:
    """Return one order.

    An order belonging to another tenant raises 404, not 403 — a 403 would
    confirm the row exists and enable enumeration.
    """
    order = await OrderRepository(session).get_by_id_or_raise(order_id)
    return _to_detail(order)


@router.get(
    "/{order_id}/timeline",
    response_model=list[OrderTimelineEntryRead],
    summary="Get the chronological history of an order",
)
async def get_order_timeline(
    session: DbSession,
    _authorized: RequireViewer,
    order_id: Annotated[uuid.UUID, Path()],
) -> list[OrderTimelineEntryRead]:
    """Lifecycle events and carrier scans, merged and ordered.

    One list rather than two endpoints: the reader wants the story of the
    order, and making the client zip two streams together invites every client
    to do it differently.
    """
    entries = await OrderSyncService(session).get_timeline(order_id)
    return [OrderTimelineEntryRead.model_validate(entry) for entry in entries]
