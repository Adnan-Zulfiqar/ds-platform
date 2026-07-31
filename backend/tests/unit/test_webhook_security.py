"""Unit tests for webhook HMAC verification and shed limiting."""

from __future__ import annotations

from typing import Any

import pytest

from app.integrations.aliexpress.webhook_security import (
    allow_webhook_under_shed,
    compute_hmac_sha256_hex,
    verify_webhook_signature,
)

pytestmark = pytest.mark.unit


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, int] = {}

    async def eval(self, script: str, numkeys: int, *args: Any) -> int:
        key = str(args[0])
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]


@pytest.fixture()
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    client = FakeRedis()
    monkeypatch.setattr(
        "app.integrations.aliexpress.webhook_security.get_redis",
        lambda *_args, **_kwargs: client,
    )
    return client


class TestSignature:
    def test_valid_hex_signature_is_accepted(self) -> None:
        secret = "test-webhook-secret"
        body = b'{"message_id":"1","type":"ORDER"}'
        digest = compute_hmac_sha256_hex(secret=secret, raw_body=body)
        assert verify_webhook_signature(
            secret=secret,
            raw_body=body,
            headers={"x-aliexpress-signature": digest},
        )

    def test_sha256_prefix_form_is_accepted(self) -> None:
        secret = "test-webhook-secret"
        body = b'{"id":"abc"}'
        digest = compute_hmac_sha256_hex(secret=secret, raw_body=body)
        assert verify_webhook_signature(
            secret=secret,
            raw_body=body,
            headers={"signature": f"sha256={digest}"},
        )

    def test_invalid_signature_is_rejected(self) -> None:
        assert not verify_webhook_signature(
            secret="test-webhook-secret",
            raw_body=b"{}",
            headers={"x-aliexpress-signature": "deadbeef"},
        )

    def test_missing_signature_is_rejected(self) -> None:
        assert not verify_webhook_signature(
            secret="test-webhook-secret",
            raw_body=b"{}",
            headers={},
        )


class TestShed:
    async def test_allows_under_limit(
        self, fake_redis: FakeRedis, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.integrations.aliexpress.webhook_security.settings.aliexpress.webhook_shed_limit",
            3,
        )
        assert await allow_webhook_under_shed(client_ip="1.2.3.4")
        assert await allow_webhook_under_shed(client_ip="1.2.3.4")
        assert await allow_webhook_under_shed(client_ip="1.2.3.4")

    async def test_sheds_over_limit(
        self, fake_redis: FakeRedis, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.integrations.aliexpress.webhook_security.settings.aliexpress.webhook_shed_limit",
            2,
        )
        assert await allow_webhook_under_shed(client_ip="9.9.9.9")
        assert await allow_webhook_under_shed(client_ip="9.9.9.9")
        assert not await allow_webhook_under_shed(client_ip="9.9.9.9")
