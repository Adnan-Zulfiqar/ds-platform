"""Building the AliExpress order request and reading its answers (Track F).

The success bodies have never been seen live, so the parsers are tested
against the documented shapes *and* against near misses: an answer that
cannot be read must never be reported as a placed order.
"""

from __future__ import annotations

import json

import pytest

from app.integrations.aliexpress.ordering import (
    PlaceAddress,
    PlaceLine,
    parse_place,
    parse_tracking,
    place_params,
)

pytestmark = pytest.mark.unit

ADDRESS = PlaceAddress(
    full_name="Jane Buyer",
    phone="+44 7700 900000",
    address="1 High Street",
    address2=None,
    city="London",
    province=None,
    zip="N1 1AA",
    country="GB",
)


def test_the_request_is_one_json_parameter_with_address_and_lines() -> None:
    params = place_params(
        out_order_id="order-1",
        address=ADDRESS,
        lines=[PlaceLine("1005001", "14:193#Red", 2)],
        shipping_method=None,
    )
    [(name, raw)] = params.items()
    assert name == "param_place_order_request4_open_api_d_t_o"
    body = json.loads(raw)
    assert body["out_order_id"] == "order-1"
    assert body["product_items"] == [
        {"product_id": 1005001, "product_count": 2, "sku_attr": "14:193#Red"}
    ]
    assert body["logistics_address"]["full_name"] == "Jane Buyer"
    assert body["logistics_address"]["mobile_no"] == "+44 7700 900000"
    assert body["logistics_address"]["zip"] == "N1 1AA"
    assert "address2" not in body["logistics_address"]  # empty fields are left out
    assert "province" not in body["logistics_address"]


def test_a_fallback_shipping_method_is_sent_only_when_configured() -> None:
    lines = [PlaceLine("1", "a", 1)]
    without = json.loads(
        place_params(out_order_id="o", address=ADDRESS, lines=lines, shipping_method=None)[
            "param_place_order_request4_open_api_d_t_o"
        ]
    )
    with_method = json.loads(
        place_params(out_order_id="o", address=ADDRESS, lines=lines, shipping_method="CAINIAO")[
            "param_place_order_request4_open_api_d_t_o"
        ]
    )
    assert "logistics_service_name" not in without["product_items"][0]
    assert with_method["product_items"][0]["logistics_service_name"] == "CAINIAO"


@pytest.mark.parametrize(
    "payload",
    [
        {
            "aliexpress_ds_order_create_response": {
                "result": {"is_success": True, "order_list": {"number": [8001, 8002]}}
            }
        },
        {
            "aliexpress_ds_order_create_response": {
                "result": {"is_success": "true", "order_list": ["8001", "8002"]}
            }
        },
    ],
)
def test_a_success_needs_the_flag_and_order_ids(payload: dict[str, object]) -> None:
    outcome = parse_place(payload)
    assert outcome.ok
    assert outcome.order_ids == ["8001", "8002"]


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (  # documented refusal
            {
                "result": {
                    "is_success": False,
                    "error_code": "B_DROPSHIPPER_DELIVERY_ADDRESS_VALIDATE_FAIL",
                    "error_msg": "bad address",
                }
            },
            "B_DROPSHIPPER_DELIVERY_ADDRESS_VALIDATE_FAIL",
        ),
        (  # gateway error envelope
            {
                "error_response": {
                    "code": "15",
                    "msg": "Remote service error",
                    "sub_code": "isv.x",
                    "sub_msg": "nope",
                }
            },
            "isv.x",
        ),
    ],
)
def test_an_explicit_refusal_is_a_failure_that_may_be_retried(
    payload: dict[str, object], code: str
) -> None:
    outcome = parse_place(payload)
    assert not outcome.ok
    assert not outcome.unknown
    assert outcome.error_code == code


@pytest.mark.parametrize(
    "payload",
    [
        {"result": {"is_success": True, "order_list": {"number": []}}},  # success, no ids
        {"result": {"order_list": {"number": [8001]}}},  # ids, no flag
        {},
        {"something": "else"},
    ],
    ids=["success_without_ids", "ids_without_flag", "empty", "unreadable"],
)
def test_an_unclear_answer_is_unknown_never_failed(payload: dict[str, object]) -> None:
    """Review finding C2: the success body has never been seen, so a body
    the parser cannot read may be a placed order. Calling it "failed" would
    offer "try again" and could buy the goods twice."""
    outcome = parse_place(payload)
    assert not outcome.ok
    assert outcome.unknown


def test_tracking_numbers_are_found_wherever_the_body_puts_them() -> None:
    payload = {
        "aliexpress_ds_order_tracking_get_response": {
            "result": {
                "ret": True,
                "data": {
                    "tracking_detail_line_list": [
                        {"carrier_name": "Cainiao", "mail_no": "LP00123", "detail_node_list": []},
                        {"carrier_name": "Cainiao", "mail_no": "LP00123"},
                        {"logistics_service": "YunExpress", "logistics_no": "YT999"},
                    ]
                },
            }
        }
    }
    found = parse_tracking(payload)
    assert [(t.number, t.carrier) for t in found] == [
        ("LP00123", "Cainiao"),
        ("YT999", "YunExpress"),
    ]


def test_no_tracking_yet_is_an_empty_list() -> None:
    assert (
        parse_tracking({"result": {"ret": True, "data": {"tracking_detail_line_list": []}}}) == []
    )
    assert parse_tracking({"result": {"mail_no": ""}}) == []
