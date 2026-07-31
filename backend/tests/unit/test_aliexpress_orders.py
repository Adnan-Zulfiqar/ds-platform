"""Unit tests for the AliExpress order contract layer.

Two kinds of payload appear here, and the distinction is the point:

* **Captured fixtures** (``tests/fixtures/aliexpress/order_get_not_found.json``,
  ``commissionorder_list_live.json``) are real gateway responses recorded by
  the Phase 5 live verification run. Tests against them pin observed truth.
* **Documentation-derived payloads** are labelled as such. This account is not
  a registered DS publisher, so a populated order body has never been observed
  (M16); the parsing tests prove the models tolerate the documented shape and
  degrade on surprises, not that the shape is what AliExpress really sends.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app.integrations.aliexpress.orders import (
    envelope_status,
    is_success_envelope,
    parse_commission_orders,
    parse_order_detail,
    to_datetime,
)

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "aliexpress"


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))  # type: ignore[no-any-return]


#: Documentation-derived populated order (M16 — never observed live).
DOCUMENTED_ORDER = {
    "aliexpress_ds_trade_order_get_response": {
        "rsp_code": 200,
        "rsp_msg": "Call succeeds",
        "result": {
            "order_id": 8123456789012345,
            "gmt_create": "2026-07-25 10:30:00",
            "order_status": "WAIT_BUYER_ACCEPT_GOODS",
            "logistics_status": "SELLER_SEND_GOODS",
            "order_amount": {"amount": "23.99", "currency_code": "USD"},
            "child_order_list": {
                "aeop_child_order_info": [
                    {
                        "product_id": 3256806389000685,
                        "product_name": "Wireless Earbuds",
                        "product_count": 2,
                        "product_price": "11.99",
                        "sku_attr": "14:771#White",
                        "child_order_id": 8123456789012346,
                        "logistics_status": "SELLER_SEND_GOODS",
                    }
                ]
            },
            "logistics_info_list": {
                "aeop_order_logistics_info": [
                    {
                        "logistics_no": "LP00123456789CN",
                        "logistics_service": "CAINIAO_STANDARD",
                    }
                ]
            },
            "receipt_address": {
                "contact_person": "Test Buyer",
                "address": "12 Mill Lane",
                "city": "Springfield",
                "province": "IL",
                "zip": "62704",
                "country": "US",
            },
        },
    }
}


class TestCapturedEnvelopes:
    """Pinned against real gateway responses, captured 2026-07-31."""

    def test_the_publisher_not_registered_envelope_parses_to_none(self) -> None:
        """The captured live response to a well-formed order query: an envelope
        with ``rsp_code 401`` and no result. Parsing must report "no order",
        not crash — this is the response every query gets until the account is
        registered as a publisher."""
        payload = load_fixture("order_get_not_found.json")

        assert parse_order_detail(payload) is None

    def test_the_envelope_status_is_readable(self) -> None:
        payload = load_fixture("order_get_not_found.json")

        code, message = envelope_status(payload)

        assert code == 401
        assert message is not None and "publisher" in message.lower()
        assert not is_success_envelope(payload)

    def test_the_rate_limited_list_response_is_an_error_envelope(self) -> None:
        """The captured `commissionorder.listbyindex` answer is an
        `error_response` envelope — which the client now raises on before any
        parser runs (see the client regression tests). If it ever reached the
        parser, the answer must be empty, not an exception."""
        payload = load_fixture("commissionorder_list_live.json")

        orders, has_more = parse_commission_orders(payload)

        assert orders == []
        assert has_more is False


class TestDocumentedOrderParsing:
    """Documentation-derived shape (M16) — proves tolerance, not truth."""

    def test_a_populated_order_parses_completely(self) -> None:
        detail = parse_order_detail(DOCUMENTED_ORDER)

        assert detail is not None
        assert detail.external_id == "8123456789012345"
        assert detail.created_at == datetime(2026, 7, 25, 10, 30, tzinfo=UTC)
        assert detail.order_status == "WAIT_BUYER_ACCEPT_GOODS"
        assert detail.total_amount == Decimal("23.99")
        assert detail.currency == "USD"

        assert len(detail.items) == 1
        item = detail.items[0]
        assert item.quantity == 2
        assert item.unit_price == Decimal("11.99")
        assert item.external_product_id == "3256806389000685"
        assert item.sku_attr == "14:771#White"

        assert len(detail.logistics) == 1
        assert detail.logistics[0].logistics_no == "LP00123456789CN"

        assert detail.receipt_address is not None
        assert detail.receipt_address.country == "US"

    def test_a_minimal_order_does_not_crash(self) -> None:
        """Every field is optional by design: a divergent real payload must
        degrade to missing data rather than a crashed sync."""
        detail = parse_order_detail(
            {"aliexpress_ds_trade_order_get_response": {"result": {"order_id": 1}}}
        )

        assert detail is not None
        assert detail.external_id == "1"
        assert detail.items == []
        assert detail.logistics == []
        assert detail.total_amount is None

    def test_unknown_fields_are_tolerated(self) -> None:
        payload = json.loads(json.dumps(DOCUMENTED_ORDER))
        payload["aliexpress_ds_trade_order_get_response"]["result"]["surprise_field"] = {"x": 1}

        assert parse_order_detail(payload) is not None

    def test_a_missing_result_is_none(self) -> None:
        assert parse_order_detail({"aliexpress_ds_trade_order_get_response": {}}) is None
        assert parse_order_detail({}) is None


class TestCommissionOrderParsing:
    def test_an_empty_result_is_an_empty_list(self) -> None:
        payload = {
            "aliexpress_ds_commissionorder_listbyindex_response": {
                "resp_result": {"result": {"current_page_no": 1, "total_page_no": 1}}
            }
        }

        orders, has_more = parse_commission_orders(payload)

        assert orders == []
        assert has_more is False

    def test_pagination_is_reported(self) -> None:
        payload = {
            "aliexpress_ds_commissionorder_listbyindex_response": {
                "resp_result": {
                    "result": {
                        "current_page_no": 1,
                        "total_page_no": 3,
                        "orders": {"order_dto": [{"order_id": 111}, {"order_number": 222}]},
                    }
                }
            }
        }

        orders, has_more = parse_commission_orders(payload)

        assert [o.external_id for o in orders] == ["111", "222"]
        assert has_more is True

    def test_malformed_rows_are_skipped_not_fatal(self) -> None:
        payload = {
            "aliexpress_ds_commissionorder_listbyindex_response": {
                "resp_result": {
                    "result": {"orders": {"order_dto": [{"order_id": 1}, "not-a-dict"]}}
                }
            }
        }

        orders, _ = parse_commission_orders(payload)

        assert len(orders) == 1


class TestToDatetime:
    def test_gateway_local_strings_parse_as_utc(self) -> None:
        assert to_datetime("2026-07-25 10:30:00") == datetime(2026, 7, 25, 10, 30, tzinfo=UTC)

    def test_epoch_milliseconds_parse(self) -> None:
        assert to_datetime(1753439400000) == datetime(2025, 7, 25, 10, 30, tzinfo=UTC)

    def test_epoch_seconds_parse(self) -> None:
        assert to_datetime(1753439400) == datetime(2025, 7, 25, 10, 30, tzinfo=UTC)

    def test_garbage_is_none_not_an_exception(self) -> None:
        assert to_datetime("not a date") is None
        assert to_datetime(None) is None
        assert to_datetime("") is None
