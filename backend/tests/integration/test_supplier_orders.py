"""Placing the AliExpress order behind a channel order (Track F, D-017).

Real Postgres; AliExpress is a fake client. The properties that matter:
an order is placed only when every line maps to one supplier SKU and the
order is paid with a usable address; anything else is ``needs_review`` with
reasons; a second request cannot start a second order; an unreadable or
refused answer is ``failed``, never ``placed``; and a row left ``placing``
is never placed again by a redelivered task.
"""

from __future__ import annotations

import importlib
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import set_tenant_id
from app.core.exceptions import ConflictError, ValidationError
from app.integrations.aliexpress.exceptions import (
    AliExpressResponseError,
    AliExpressTimeoutError,
    AliExpressUnavailableError,
)
from app.models.order import FulfillmentStatus, Order, OrderItem, OrderSource, PaymentStatus
from app.models.product import Product, ProductSource, ProductStatus, ProductVariant
from app.models.supplier_order import SupplierOrderStatus
from app.repositories.supplier_order import SupplierOrderRepository
from app.services.supplier_ordering import SupplierOrderingService
from app.tasks import supplier_orders as tasks
from tests.integration.test_ebay_c1_api import auth_header, register, token_with_roles

pytestmark = pytest.mark.integration


class FakeAliExpress:
    def __init__(self, answer: dict[str, Any] | Exception) -> None:
        self.answer = answer
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.retry_flags: list[bool] = []

    async def call(
        self, method: str, params: dict[str, Any], *, retry: bool = True
    ) -> dict[str, Any]:
        self.calls.append((method, params))
        self.retry_flags.append(retry)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


PLACED = {
    "aliexpress_ds_order_create_response": {
        "result": {"is_success": True, "order_list": {"number": [8001]}}
    }
}


@pytest.fixture
def aliexpress(monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession) -> Any:
    """Run the task's three transactions on the test session, and answer
    AliExpress calls with whatever the test sets."""
    fake = FakeAliExpress(PLACED)

    @asynccontextmanager
    async def shared() -> AsyncIterator[AsyncSession]:
        yield db_session
        await db_session.flush()

    async def client(_self: object) -> FakeAliExpress:
        return fake

    monkeypatch.setattr(tasks, "transaction", shared)
    monkeypatch.setattr(tasks.AliExpressService, "authenticated_client", client)
    return fake


async def workspace(client: AsyncClient) -> tuple[dict[str, Any], uuid.UUID]:
    owner = await register(client)
    tenant_id = uuid.UUID(str(owner["identity"]["tenant"]["id"]))
    set_tenant_id(tenant_id)
    return owner, tenant_id


async def catalogue(
    db_session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    variants: list[str],
    external_id: str = "1005001",
) -> tuple[uuid.UUID, list[uuid.UUID]]:
    product = Product(
        tenant_id=tenant_id,
        source=ProductSource.ALIEXPRESS,
        external_id=external_id,
        title="Case",
        status=ProductStatus.ACTIVE,
        sell_price=Decimal("10"),
    )
    db_session.add(product)
    await db_session.flush()
    rows = [
        ProductVariant(
            tenant_id=tenant_id,
            product_id=product.id,
            external_variant_id=f"sku{i}",
            external_attributes=attr,
        )
        for i, attr in enumerate(variants)
    ]
    db_session.add_all(rows)
    await db_session.flush()
    return product.id, [row.id for row in rows]


async def channel_order(
    db_session: AsyncSession,
    tenant_id: uuid.UUID,
    lines: list[tuple[uuid.UUID | None, uuid.UUID | None, int]],
    **overrides: Any,
) -> uuid.UUID:
    values: dict[str, Any] = {
        "tenant_id": tenant_id,
        "source": OrderSource.SHOPIFY,
        "external_id": f"sh-{uuid.uuid4().hex[:8]}",
        "fulfillment_status": FulfillmentStatus.PAID,
        "payment_status": PaymentStatus.PAID,
        "recipient_name": "Jane Buyer",
        "recipient_phone": "+44 7700 900000",
        "address_line1": "1 High Street",
        "city": "London",
        "postal_code": "N1 1AA",
        "country_code": "GB",
    }
    values.update(overrides)
    order = Order(**values)
    db_session.add(order)
    await db_session.flush()
    for index, (product_id, variant_id, quantity) in enumerate(lines):
        db_session.add(
            OrderItem(
                tenant_id=tenant_id,
                order_id=order.id,
                external_item_id=str(index + 1),
                product_id=product_id,
                variant_id=variant_id,
                quantity=quantity,
            )
        )
    await db_session.flush()
    return order.id


async def place(db_session: AsyncSession, tenant_id: uuid.UUID, order_id: uuid.UUID) -> Any:
    row = await SupplierOrderingService(db_session).request(
        order_id, user_id=None, trigger="manual"
    )
    if row.status == SupplierOrderStatus.QUEUED.value:
        await tasks._place(tenant_id, row.id)
        set_tenant_id(tenant_id)  # _place clears the context on exit
    return await SupplierOrderRepository(db_session).for_order(order_id)


async def test_a_clean_order_is_placed_with_the_exact_sku_and_address(
    client: AsyncClient, db_session: AsyncSession, aliexpress: FakeAliExpress
) -> None:
    _, tenant_id = await workspace(client)
    product_id, [_red, blue] = await catalogue(
        db_session, tenant_id, variants=["14:Red", "14:Blue"]
    )
    order_id = await channel_order(db_session, tenant_id, [(product_id, blue, 2)])

    row = await place(db_session, tenant_id, order_id)

    assert row.status == SupplierOrderStatus.PLACED.value
    assert row.external_order_ids == ["8001"]
    assert row.placed_at is not None
    [(method, params)] = aliexpress.calls
    assert method == "aliexpress.ds.order.create"
    assert aliexpress.retry_flags == [False]  # review C1: never re-sent
    body = json.loads(params["param_place_order_request4_open_api_d_t_o"])
    assert body["product_items"] == [
        {"product_id": 1005001, "product_count": 2, "sku_attr": "14:Blue"}
    ]
    assert body["logistics_address"]["country"] == "GB"
    assert body["out_order_id"] == str(order_id)
    assert row.request_lines == [{"productId": "1005001", "skuAttr": "14:Blue", "quantity": 2}]


async def test_a_single_variant_product_needs_no_variant_on_the_line(
    client: AsyncClient, db_session: AsyncSession, aliexpress: FakeAliExpress
) -> None:
    _, tenant_id = await workspace(client)
    product_id, _ = await catalogue(db_session, tenant_id, variants=["14:Only"])
    order_id = await channel_order(
        db_session, tenant_id, [(product_id, None, 1)], source=OrderSource.WOOCOMMERCE
    )
    row = await place(db_session, tenant_id, order_id)
    assert row.status == SupplierOrderStatus.PLACED.value


async def test_ambiguous_or_unpaid_orders_go_to_review_and_nothing_is_sent(
    client: AsyncClient, db_session: AsyncSession, aliexpress: FakeAliExpress
) -> None:
    _, tenant_id = await workspace(client)
    product_id, _ = await catalogue(db_session, tenant_id, variants=["14:Red", "14:Blue"])
    order_id = await channel_order(
        db_session,
        tenant_id,
        [(product_id, None, 1), (None, None, 1)],
        payment_status=PaymentStatus.UNPAID,
        recipient_phone=None,
        country_code="BR",
    )

    row = await place(db_session, tenant_id, order_id)

    assert row.status == SupplierOrderStatus.NEEDS_REVIEW.value
    assert set(row.review_reasons) == {
        "order_not_paid",
        "missing_recipient_phone",
        "tax_id_required",
        "line_1:variant_unknown",
        "line_2:not_a_droppilot_product",
    }
    assert aliexpress.calls == []


async def test_a_second_request_cannot_start_a_second_order(
    client: AsyncClient, db_session: AsyncSession, aliexpress: FakeAliExpress
) -> None:
    _, tenant_id = await workspace(client)
    product_id, [only] = await catalogue(db_session, tenant_id, variants=["14:Only"])
    order_id = await channel_order(db_session, tenant_id, [(product_id, only, 1)])
    await place(db_session, tenant_id, order_id)

    from app.core.exceptions import ConflictError

    with pytest.raises(ConflictError):
        await SupplierOrderingService(db_session).request(order_id, user_id=None, trigger="manual")
    assert len(aliexpress.calls) == 1


@pytest.mark.parametrize(
    "answer",
    [
        {"result": {"is_success": False, "error_code": "B_STOCK", "error_msg": "out of stock"}},
        AliExpressResponseError("refused", upstream_code="isv.permission"),
    ],
    ids=["explicit_refusal", "error_envelope"],
)
async def test_an_explicit_refusal_is_failed_and_can_be_retried(
    client: AsyncClient,
    db_session: AsyncSession,
    aliexpress: FakeAliExpress,
    answer: dict[str, Any] | Exception,
) -> None:
    _, tenant_id = await workspace(client)
    product_id, [only] = await catalogue(db_session, tenant_id, variants=["14:Only"])
    order_id = await channel_order(db_session, tenant_id, [(product_id, only, 1)])
    aliexpress.answer = answer

    row = await place(db_session, tenant_id, order_id)
    assert row.status == SupplierOrderStatus.FAILED.value
    assert row.external_order_ids == []
    assert row.error_code

    aliexpress.answer = PLACED  # the merchant fixes the cause and retries
    row = await place(db_session, tenant_id, order_id)
    assert row.status == SupplierOrderStatus.PLACED.value


@pytest.mark.parametrize(
    "answer",
    [
        {"unexpected": "shape"},
        {"result": {"is_success": True, "order_list": {"number": []}}},
        AliExpressTimeoutError(),
        AliExpressUnavailableError("502 from the gateway"),
        RuntimeError("worker lost the connection"),
    ],
    ids=["unreadable", "success_without_ids", "timeout", "server_error", "crash"],
)
async def test_an_unclear_answer_waits_for_the_merchant_and_is_never_retried(
    client: AsyncClient,
    db_session: AsyncSession,
    aliexpress: FakeAliExpress,
    answer: dict[str, Any] | Exception,
) -> None:
    """Review C2: AliExpress may have created the order. The row stays
    "placing" with outcome_unknown, a new request is refused, and only the
    merchant's release (after checking AliExpress) allows another try."""
    _, tenant_id = await workspace(client)
    product_id, [only] = await catalogue(db_session, tenant_id, variants=["14:Only"])
    order_id = await channel_order(db_session, tenant_id, [(product_id, only, 1)])
    aliexpress.answer = answer

    row = await place(db_session, tenant_id, order_id)
    assert row.status == SupplierOrderStatus.PLACING.value
    assert row.error_code == "outcome_unknown"
    assert "Check your AliExpress orders" in (row.error_message or "")
    with pytest.raises(ConflictError):
        await SupplierOrderingService(db_session).request(order_id, user_id=None, trigger="manual")
    assert len(aliexpress.calls) == 1

    released = await SupplierOrderingService(db_session).release(order_id)
    assert released.status == SupplierOrderStatus.FAILED.value
    aliexpress.answer = PLACED
    row = await place(db_session, tenant_id, order_id)
    assert row.status == SupplierOrderStatus.PLACED.value


async def test_release_is_only_for_a_stuck_order(
    client: AsyncClient, db_session: AsyncSession, aliexpress: FakeAliExpress
) -> None:
    _, tenant_id = await workspace(client)
    product_id, [only] = await catalogue(db_session, tenant_id, variants=["14:Only"])
    order_id = await channel_order(db_session, tenant_id, [(product_id, only, 1)])
    await place(db_session, tenant_id, order_id)  # placed
    with pytest.raises(ConflictError):
        await SupplierOrderingService(db_session).release(order_id)


async def test_no_call_is_made_when_aliexpress_is_not_connected(
    client: AsyncClient,
    db_session: AsyncSession,
    aliexpress: FakeAliExpress,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review C5: building the client failed outside the error handling and
    left the row "placing" forever. Nothing was sent, so it is a failure."""
    _, tenant_id = await workspace(client)
    product_id, [only] = await catalogue(db_session, tenant_id, variants=["14:Only"])
    order_id = await channel_order(db_session, tenant_id, [(product_id, only, 1)])

    async def not_connected(_self: object) -> None:
        raise ValidationError("AliExpress is not connected for this workspace.")

    monkeypatch.setattr(tasks.AliExpressService, "authenticated_client", not_connected)
    row = await place(db_session, tenant_id, order_id)
    assert row.status == SupplierOrderStatus.FAILED.value
    assert aliexpress.calls == []


async def test_already_fulfilled_or_refunded_orders_are_not_bought_again(
    client: AsyncClient, db_session: AsyncSession, aliexpress: FakeAliExpress
) -> None:
    """Review C3."""
    _, tenant_id = await workspace(client)
    product_id, [only] = await catalogue(db_session, tenant_id, variants=["14:Only"])
    for status, reason in (
        (FulfillmentStatus.FULFILLED, "order_already_fulfilled"),
        (FulfillmentStatus.SHIPPED, "order_already_fulfilled"),
        (FulfillmentStatus.DELIVERED, "order_already_fulfilled"),
        (FulfillmentStatus.REFUNDED, "order_refunded"),
    ):
        order_id = await channel_order(
            db_session, tenant_id, [(product_id, only, 1)], fulfillment_status=status
        )
        row = await place(db_session, tenant_id, order_id)
        assert row.status == SupplierOrderStatus.NEEDS_REVIEW.value
        assert reason in row.review_reasons
    assert aliexpress.calls == []


async def test_removed_lines_are_not_ordered_and_quantities_are_exact(
    client: AsyncClient, db_session: AsyncSession, aliexpress: FakeAliExpress
) -> None:
    """Review C4: a line edited down to 0 is not bought (it used to become 1)."""
    _, tenant_id = await workspace(client)
    product_id, [only] = await catalogue(db_session, tenant_id, variants=["14:Only"])
    order_id = await channel_order(
        db_session, tenant_id, [(product_id, only, 3), (product_id, only, 0)]
    )
    row = await place(db_session, tenant_id, order_id)
    assert row.status == SupplierOrderStatus.PLACED.value
    assert row.request_lines == [{"productId": "1005001", "skuAttr": "14:Only", "quantity": 3}]

    emptied = await channel_order(db_session, tenant_id, [(product_id, only, 0)])
    row = await place(db_session, tenant_id, emptied)
    assert row.status == SupplierOrderStatus.NEEDS_REVIEW.value
    assert row.review_reasons == ["no_order_lines"]


async def test_a_row_left_placing_is_never_placed_again(
    client: AsyncClient, db_session: AsyncSession, aliexpress: FakeAliExpress
) -> None:
    _, tenant_id = await workspace(client)
    product_id, [only] = await catalogue(db_session, tenant_id, variants=["14:Only"])
    order_id = await channel_order(db_session, tenant_id, [(product_id, only, 1)])
    row = await SupplierOrderingService(db_session).request(
        order_id, user_id=None, trigger="manual"
    )
    await SupplierOrderRepository(db_session).update(row, status=SupplierOrderStatus.PLACING.value)

    assert await tasks._place(tenant_id, row.id) == "skipped"  # a redelivered task
    set_tenant_id(tenant_id)
    assert aliexpress.calls == []
    from app.core.exceptions import ConflictError

    with pytest.raises(ConflictError):
        await SupplierOrderingService(db_session).request(order_id, user_id=None, trigger="manual")


async def test_the_api_queues_for_admins_and_reports_to_viewers(
    client: AsyncClient,
    db_session: AsyncSession,
    aliexpress: FakeAliExpress,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, tenant_id = await workspace(client)
    product_id, [only] = await catalogue(db_session, tenant_id, variants=["14:Only"])
    order_id = await channel_order(db_session, tenant_id, [(product_id, only, 1)])
    queued: list[uuid.UUID] = []
    # The module, not the dotted path: ``app.api.v1.orders.router`` names the
    # package's APIRouter attribute, which has no ``place_after_commit``.
    router_module = importlib.import_module("app.api.v1.orders.router")
    monkeypatch.setattr(
        router_module, "place_after_commit", lambda _s, row_id: queued.append(row_id)
    )

    url = f"/api/v1/orders/{order_id}/supplier-order"
    viewer = token_with_roles(owner, "viewer")
    assert (await client.post(url, headers=viewer)).status_code == 403
    response = await client.post(url, headers=auth_header(owner))
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "queued"
    assert len(queued) == 1

    read = await client.get(url, headers=viewer)
    assert read.status_code == 200
    assert read.json()["status"] == "queued"
    assert (
        await client.get(f"/api/v1/orders/{uuid.uuid4()}/supplier-order", headers=viewer)
    ).status_code == 404


async def test_settings_default_off_and_only_admins_change_them(client: AsyncClient) -> None:
    owner, _ = await workspace(client)
    url = "/api/v1/orders/fulfilment/settings"
    first = await client.get(url, headers=auth_header(owner))
    assert first.json() == {
        "autoOrder": False,
        "autoTracking": False,
        "fallbackShippingMethod": None,
    }

    body = {"autoOrder": True, "autoTracking": True, "fallbackShippingMethod": " CAINIAO_STANDARD "}
    assert (
        await client.put(url, json=body, headers=token_with_roles(owner, "viewer"))
    ).status_code == 403
    saved = await client.put(url, json=body, headers=auth_header(owner))
    assert saved.status_code == 200
    assert saved.json() == {
        "autoOrder": True,
        "autoTracking": True,
        "fallbackShippingMethod": "CAINIAO_STANDARD",
    }
