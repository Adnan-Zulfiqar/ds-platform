"""AliExpress dropshipping order placement and tracking (Track F, D-017).

Two API methods, both confirmed reachable for the connected account on
2026-10-05 by an owner-approved probe that could not create an order:

* ``aliexpress.ds.order.create`` answered ``MissingParameter
  logistics_address`` to an empty request, so the method exists and the
  account may call it. (``aliexpress.trade.ds.order.create`` answered
  ``InvalidApiPath``.)
* ``aliexpress.ds.order.tracking.get`` answered with its normal envelope.

**What is not known.** No real order has been placed, so the success body
of either method has never been seen. The request shape follows AliExpress's
published ``PlaceOrderRequest4OpenApiDTO``; the parsers below search the
response for the documented fields rather than trusting one fixed path, and
an answer they cannot read is reported as "unknown", never as success.

**Money.** A created order is *unpaid*. The merchant pays it on AliExpress;
nothing here can spend money by itself.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

PLACE_METHOD = "aliexpress.ds.order.create"
TRACKING_METHOD = "aliexpress.ds.order.tracking.get"

#: Where the merchant pays the unpaid orders DropPilot creates.
PAYMENT_URL = "https://www.aliexpress.com/p/order/index.html"


@dataclass(frozen=True, slots=True)
class PlaceLine:
    product_id: str
    sku_attr: str
    quantity: int


@dataclass(frozen=True, slots=True)
class PlaceAddress:
    full_name: str
    phone: str
    address: str
    address2: str | None
    city: str
    province: str | None
    zip: str | None
    country: str


@dataclass(frozen=True, slots=True)
class PlaceOutcome:
    """``ok`` placed; ``unknown`` AliExpress may or may not have placed it;
    neither means an explicit refusal (the only state that may be retried)."""

    ok: bool
    order_ids: list[str]
    error_code: str | None
    error_message: str | None
    unknown: bool = False


@dataclass(frozen=True, slots=True)
class Tracking:
    number: str
    carrier: str | None


def place_params(
    *,
    out_order_id: str,
    address: PlaceAddress,
    lines: list[PlaceLine],
    shipping_method: str | None,
) -> dict[str, Any]:
    """The single API parameter, a JSON string, as AliExpress expects it.

    ``logistics_service_name`` is left out unless a fallback method is
    configured: the owner chose the supplier's default (D-017).
    ``out_order_id`` carries DropPilot's order id so the AliExpress order can
    be traced back; whether AliExpress also de-duplicates on it is not
    documented, so DropPilot's own state machine is what prevents a second
    order.
    """
    items = []
    for line in lines:
        item: dict[str, Any] = {
            "product_id": int(line.product_id) if line.product_id.isdigit() else line.product_id,
            "product_count": line.quantity,
            "sku_attr": line.sku_attr,
        }
        if shipping_method:
            item["logistics_service_name"] = shipping_method
        items.append(item)
    logistics: dict[str, Any] = {
        "full_name": address.full_name,
        "contact_person": address.full_name,
        "mobile_no": address.phone,
        "address": address.address,
        "city": address.city,
        "country": address.country,
    }
    if address.address2:
        logistics["address2"] = address.address2
    if address.province:
        logistics["province"] = address.province
    if address.zip:
        logistics["zip"] = address.zip
    request = {"out_order_id": out_order_id, "logistics_address": logistics, "product_items": items}
    return {"param_place_order_request4_open_api_d_t_o": json.dumps(request, separators=(",", ":"))}


def _walk(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _ids(value: Any) -> list[str]:
    if isinstance(value, dict):
        for key in ("number", "order_id", "order_list"):
            if key in value:
                return _ids(value[key])
        return []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_ids(item))
        return out
    if isinstance(value, int | str) and str(value).strip():
        return [str(value).strip()]
    return []


def parse_place(payload: dict[str, Any]) -> PlaceOutcome:
    """Read ``aliexpress.ds.order.create``'s answer, three ways.

    * **Placed:** an explicit success flag *and* at least one order id.
    * **Refused:** an explicit ``is_success: false``, or an error envelope.
      AliExpress said no, so trying again cannot buy twice.
    * **Unknown:** anything else (success without ids, a body DropPilot
      cannot read). The success body has never been seen live, so an
      unreadable answer may well be a placed order; it must never be
      offered as "try again".
    """
    for node in _walk(payload):
        if "is_success" in node or "order_list" in node:
            ids = _ids(node.get("order_list"))
            flag = node.get("is_success")
            code = str(node.get("error_code") or "") or None
            message = str(node.get("error_msg") or node.get("error_message") or "") or None
            if flag in (True, "true", "True") and ids:
                return PlaceOutcome(True, ids, None, None)
            if flag in (False, "false", "False"):
                return PlaceOutcome(False, ids, code or "refused", message)
            return PlaceOutcome(False, ids, code or "unclear_success", message, unknown=True)
    for node in _walk(payload):
        if "code" in node and ("msg" in node or "sub_msg" in node):
            return PlaceOutcome(
                False,
                [],
                str(node.get("sub_code") or node.get("code")),
                str(node.get("sub_msg") or node.get("msg")),
            )
    return PlaceOutcome(
        False,
        [],
        "unreadable_response",
        "AliExpress returned an answer DropPilot could not read.",
        unknown=True,
    )


def parse_tracking(payload: dict[str, Any]) -> list[Tracking]:
    """Tracking numbers in ``aliexpress.ds.order.tracking.get``'s answer:
    every object carrying ``mail_no`` (documented) or ``logistics_no``
    (the order-detail spelling), de-duplicated, in order."""
    found: list[Tracking] = []
    seen: set[str] = set()
    for node in _walk(payload):
        number = node.get("mail_no") or node.get("logistics_no")
        if not isinstance(number, str | int) or not str(number).strip():
            continue
        text = str(number).strip()
        if text in seen:
            continue
        seen.add(text)
        carrier = node.get("carrier_name") or node.get("logistics_service") or node.get("company")
        found.append(Tracking(text, str(carrier).strip() if carrier else None))
    return found


__all__ = [
    "PAYMENT_URL",
    "PLACE_METHOD",
    "TRACKING_METHOD",
    "PlaceAddress",
    "PlaceLine",
    "PlaceOutcome",
    "Tracking",
    "parse_place",
    "parse_tracking",
    "place_params",
]
