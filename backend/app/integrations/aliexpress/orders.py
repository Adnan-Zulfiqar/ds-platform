"""AliExpress order wire models.

Separate from ``catalog.py`` for the same reason that is separate from
``schemas.py``: the order contract changes for different reasons and at a
different cadence from the product contract.

**What is pinned by evidence and what is not — stated plainly:**

* The *envelope* shape is captured live: ``aliexpress_ds_trade_order_get_response``
  with ``rsp_code`` / ``rsp_msg``, committed as
  ``tests/fixtures/aliexpress/order_get_not_found.json``. The gateway answered
  ``rsp_code 401 "This publisher is not registered"`` — the strongest signal
  available without a registered publisher account.
* The *populated* order body has **never been observed live**. This account is
  not registered as a DS publisher and placing a real order spends real money.
  The field models below are derived from the published
  ``aliexpress.ds.trade.order.get`` documentation, parsed with the same
  defensive posture as the catalogue: every field optional, unknown fields
  allowed, money as strings coerced to ``Decimal``.

That asymmetry is recorded in ``TECHNICAL_DEBT.md`` (M16) rather than implied
away. When the first real order payload is captured, it becomes a fixture and
any divergence becomes a regression test — the same path the product contract
took in Phase 4.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import Field

from app.integrations.aliexpress.catalog import WireModel, to_decimal, to_int

#: Envelope codes observed live. 200 is success; 401 is "publisher not
#: registered"; the product API uses 605 for "not found" and the order API is
#: documented to follow the same scheme.
_SUCCESS_CODES = frozenset({200, 0})


def to_datetime(value: Any) -> datetime | None:
    """Coerce an AliExpress timestamp to an aware UTC datetime.

    Timestamps arrive in at least two shapes across the DS APIs: epoch
    milliseconds (integers or numeric strings) and ``YYYY-MM-DD HH:MM:SS``
    strings in gateway-local time. Both are accepted; anything else returns
    ``None`` rather than failing the order it belongs to.
    """
    if value is None or value == "":
        return None

    numeric = to_int(value)
    if numeric is not None and numeric > 10_000_000_000:  # epoch millis, not seconds
        return datetime.fromtimestamp(numeric / 1000, tz=UTC)
    if numeric is not None and numeric > 100_000_000:  # epoch seconds
        return datetime.fromtimestamp(numeric, tz=UTC)

    try:
        parsed = datetime.strptime(str(value).strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC)


class OrderChildItem(WireModel):
    """One line item inside a supplier order.

    Documentation-derived shape (see module docstring). ``sku_attr`` mirrors
    the catalogue composite key so an item can be matched back to the imported
    product variant it was placed against.
    """

    product_id: int | None = None
    product_name: str | None = None
    product_count: int | None = None
    product_price: str | None = None
    sku_attr: str | None = None
    logistics_status: str | None = None
    order_status: str | None = None
    child_order_id: int | None = None
    logistics_service_name: str | None = None
    logistics_no: str | None = None

    @property
    def quantity(self) -> int:
        return self.product_count or 1

    @property
    def unit_price(self) -> Decimal | None:
        return to_decimal(self.product_price)

    @property
    def external_product_id(self) -> str | None:
        return str(self.product_id) if self.product_id is not None else None


class _ChildItemWrapper(WireModel):
    aeop_child_order_info: list[OrderChildItem] = Field(default_factory=list)


class OrderLogisticsInfo(WireModel):
    """Shipping record attached to an order — documentation-derived."""

    logistics_no: str | None = None
    logistics_service: str | None = None


class _LogisticsWrapper(WireModel):
    aeop_order_logistics_info: list[OrderLogisticsInfo] = Field(default_factory=list)


class OrderReceiptInfo(WireModel):
    """Delivery address — documentation-derived.

    Only what fulfilment needs is modelled; the platform must not become a
    warehouse for buyer PII it has no use for.
    """

    contact_person: str | None = None
    mobile_no: str | None = None
    phone_country: str | None = None
    address: str | None = None
    address2: str | None = None
    city: str | None = None
    province: str | None = None
    zip: str | None = None
    country: str | None = None


class OrderDetail(WireModel):
    """The ``result`` object of ``aliexpress.ds.trade.order.get``.

    Documentation-derived except the envelope (captured live). Every field is
    optional and unknown fields are allowed, so a divergent real payload
    degrades to missing data rather than a crashed sync.
    """

    order_id: int | None = None
    gmt_create: str | None = None
    order_status: str | None = None
    logistics_status: str | None = None
    order_amount: dict[str, Any] | None = None
    child_order_list: _ChildItemWrapper | None = None
    logistics_info_list: _LogisticsWrapper | None = None
    receipt_address: OrderReceiptInfo | None = None
    store_info: dict[str, Any] | None = None

    @property
    def external_id(self) -> str | None:
        return str(self.order_id) if self.order_id is not None else None

    @property
    def created_at(self) -> datetime | None:
        return to_datetime(self.gmt_create)

    @property
    def items(self) -> list[OrderChildItem]:
        return self.child_order_list.aeop_child_order_info if self.child_order_list else []

    @property
    def logistics(self) -> list[OrderLogisticsInfo]:
        return (
            self.logistics_info_list.aeop_order_logistics_info if self.logistics_info_list else []
        )

    @property
    def total_amount(self) -> Decimal | None:
        """``order_amount`` is documented as ``{"amount": "12.34", "currency_code": "USD"}``."""
        if not isinstance(self.order_amount, dict):
            return None
        return to_decimal(self.order_amount.get("amount"))

    @property
    def currency(self) -> str | None:
        if not isinstance(self.order_amount, dict):
            return None
        value = self.order_amount.get("currency_code")
        return str(value) if value else None


def parse_order_detail(payload: dict[str, Any]) -> OrderDetail | None:
    """Extract the order from a raw ``ds.trade.order.get`` envelope.

    Returns ``None`` when the envelope carries no result. The captured live
    response for this method is an envelope with ``rsp_code`` and no ``result``
    (``401 This publisher is not registered``), so "no result" is a normal,
    observed outcome — not a hypothetical.
    """
    body = payload.get("aliexpress_ds_trade_order_get_response") or payload
    result = body.get("result")
    if not isinstance(result, dict):
        return None
    return OrderDetail.model_validate(result)


def envelope_status(payload: dict[str, Any]) -> tuple[int | None, str | None]:
    """Read the ``rsp_code`` / ``rsp_msg`` pair from an order envelope.

    Both fields captured live. Callers use this to distinguish "no such order"
    from "this account cannot query orders", which need different handling —
    the first is per-order churn, the second fails the whole sync run.
    """
    body = payload.get("aliexpress_ds_trade_order_get_response") or payload
    code = to_int(body.get("rsp_code"))
    message = body.get("rsp_msg")
    return code, str(message) if message else None


def is_success_envelope(payload: dict[str, Any]) -> bool:
    code, _ = envelope_status(payload)
    return code in _SUCCESS_CODES


class CommissionOrder(WireModel):
    """One row of ``aliexpress.ds.commissionorder.listbyindex``.

    Documentation-derived. The live gateway currently answers this method with
    ``ApiCallLimit`` for this application (captured as
    ``commissionorder_list_live.json``), so the row shape is unverified; the
    fields below are the documented ones the sync consumes.
    """

    order_id: int | None = None
    order_number: int | None = None
    paid_time: str | None = None
    order_status: str | None = None
    created_time: str | None = None
    finished_time: str | None = None

    @property
    def external_id(self) -> str | None:
        if self.order_id is not None:
            return str(self.order_id)
        if self.order_number is not None:
            return str(self.order_number)
        return None


def parse_commission_orders(payload: dict[str, Any]) -> tuple[list[CommissionOrder], bool]:
    """Extract order rows and whether another page exists.

    Returns ``([], False)`` for an empty or malformed result — an account with
    no orders in the window is the common case, not an error.
    """
    body = payload.get("aliexpress_ds_commissionorder_listbyindex_response") or payload
    result = (body.get("resp_result") or {}).get("result") or {}
    if not isinstance(result, dict):
        return [], False

    orders = result.get("orders")
    items: list[Any] = []
    if isinstance(orders, dict):
        candidate = orders.get("order_dto") or orders.get("order")
        if isinstance(candidate, list):
            items = candidate
    elif isinstance(orders, list):
        items = orders

    parsed = [CommissionOrder.model_validate(i) for i in items if isinstance(i, dict)]

    current = to_int(result.get("current_page_no")) or 1
    total_pages = to_int(result.get("total_page_no")) or 1
    return parsed, current < total_pages


__all__ = [
    "CommissionOrder",
    "OrderChildItem",
    "OrderDetail",
    "OrderLogisticsInfo",
    "OrderReceiptInfo",
    "envelope_status",
    "is_success_envelope",
    "parse_commission_orders",
    "parse_order_detail",
    "to_datetime",
]
