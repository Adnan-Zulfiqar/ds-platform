"""Track E6 — Stripe's webhook signature scheme and form encoding."""

from __future__ import annotations

import hashlib
import hmac

import pytest

from app.integrations.stripe.client import StripeSignatureError, encode_form, verify_signature

pytestmark = pytest.mark.unit

SECRET = "whsec_unit_test_secret"
BODY = b'{"id":"evt_1","type":"customer.subscription.updated"}'


def header(timestamp: int, body: bytes = BODY, secret: str = SECRET) -> str:
    signed = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={signed}"


def test_a_fresh_correct_signature_verifies() -> None:
    verify_signature(payload=BODY, header=header(1_000), secret=SECRET, now=1_010)


def test_one_of_several_v1_signatures_may_match() -> None:
    good = header(1_000).split(",")[1]
    verify_signature(payload=BODY, header=f"t=1000,v1=deadbeef,{good}", secret=SECRET, now=1_000)


@pytest.mark.parametrize(
    "bad",
    [
        header(1_000, body=b"{}"),  # body changed
        header(1_000, secret="whsec_other"),  # another secret
        "v1=abc",  # no timestamp
        "t=1000",  # no signature
        "",
    ],
)
def test_wrong_or_malformed_signatures_are_refused(bad: str) -> None:
    with pytest.raises(StripeSignatureError):
        verify_signature(payload=BODY, header=bad, secret=SECRET, now=1_000)


def test_an_old_signature_is_refused_as_a_replay() -> None:
    with pytest.raises(StripeSignatureError):
        verify_signature(payload=BODY, header=header(1_000), secret=SECRET, now=1_000 + 301)


def test_nested_params_use_stripes_bracket_encoding() -> None:
    assert encode_form(
        {
            "mode": "subscription",
            "line_items": [{"price": "price_a", "quantity": 1}, {"price": "price_b"}],
            "subscription_data": {"metadata": {"tenant_id": "t1"}, "trial_end": 99},
            "flag": True,
            "skip": None,
        }
    ) == [
        ("mode", "subscription"),
        ("line_items[0][price]", "price_a"),
        ("line_items[0][quantity]", "1"),
        ("line_items[1][price]", "price_b"),
        ("subscription_data[metadata][tenant_id]", "t1"),
        ("subscription_data[trial_end]", "99"),
        ("flag", "true"),
    ]
