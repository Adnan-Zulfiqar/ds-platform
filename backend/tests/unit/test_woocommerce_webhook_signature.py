"""Track E7 W4b — the WooCommerce delivery signature check."""

from __future__ import annotations

import base64
import hashlib
import hmac

import pytest

from app.integrations.woocommerce.webhook import signature_matches

pytestmark = pytest.mark.unit


def _sign(secret: str, body: bytes) -> str:
    return base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()


def test_the_store_secret_signature_matches() -> None:
    assert signature_matches(
        raw_body=b'{"id":7}', header=_sign("s3cret", b'{"id":7}'), secret="s3cret"
    )


@pytest.mark.parametrize(
    ("body", "secret"),
    [(b'{"id":8}', "s3cret"), (b'{"id":7}', "other")],
)
def test_a_changed_body_or_another_secret_does_not(body: bytes, secret: str) -> None:
    header = _sign("s3cret", b'{"id":7}')
    assert not signature_matches(raw_body=body, header=header, secret=secret)


def test_an_empty_or_garbage_header_does_not() -> None:
    assert not signature_matches(raw_body=b"{}", header="", secret="s3cret")
    assert not signature_matches(raw_body=b"{}", header="not-base64!", secret="s3cret")
