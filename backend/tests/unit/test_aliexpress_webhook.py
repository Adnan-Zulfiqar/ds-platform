"""Unit tests for AliExpress webhook ingestion and processing."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from redis.exceptions import RedisError

from app.integrations.aliexpress.webhook import (
    acknowledge_webhook,
    classify_webhook,
    extract_message_id,
    parse_webhook_payload,
    process_webhook,
)

pytestmark = pytest.mark.unit


class FakeRedis:
    """Just enough of the Redis client for replay protection and counting."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.counters: dict[str, int] = {}

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> Any:
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def incr(self, key: str) -> int:
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]


@pytest.fixture()
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    client = FakeRedis()
    monkeypatch.setattr(
        "app.integrations.aliexpress.webhook.get_redis", lambda *_args, **_kwargs: client
    )
    return client


class TestAcknowledgeWebhook:
    def test_returns_received_status(self) -> None:
        response = acknowledge_webhook(payload={"event": "order_created"}, headers={})

        assert response.status == "received"

    def test_logs_field_names_not_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Shape is recorded; customer data is not.

        A real order notification carries buyer names, addresses and phone
        numbers. `CLAUDE.md` forbids logging a request body containing customer
        data, and an earlier revision of this handler logged the payload
        verbatim at INFO — in production, for every delivery.
        """
        info = MagicMock()
        monkeypatch.setattr("app.integrations.aliexpress.webhook.logger.info", info)

        payload = {
            "message_type": "ORDER_STATUS",
            "order_id": "12345",
            "buyer_name": "Ada Lovelace",
            "buyer_address": "12 Mill Lane",
        }
        acknowledge_webhook(payload=payload, headers={"x-forwarded-for": "1.2.3.4"})

        info.assert_called_once()
        _, kwargs = info.call_args

        assert kwargs["field_names"] == [
            "buyer_address",
            "buyer_name",
            "message_type",
            "order_id",
        ]
        assert kwargs["field_count"] == 4
        assert kwargs["header_keys"] == ["x-forwarded-for"]

        # The values themselves must appear nowhere in the log call.
        rendered = str(kwargs)
        assert "Ada Lovelace" not in rendered
        assert "12 Mill Lane" not in rendered
        assert "12345" not in rendered

    def test_the_body_is_logged_only_when_explicitly_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`LOG_INCLUDE_REQUEST_BODY` is refused in deployed environments, so
        this is the switch that keeps real payloads inspectable locally without
        making them loggable in production."""
        from app.integrations.aliexpress import webhook as webhook_module

        debug = MagicMock()
        monkeypatch.setattr(webhook_module.logger, "debug", debug)

        monkeypatch.setattr(webhook_module.settings.observability, "include_request_body", False)
        acknowledge_webhook(payload={"buyer_name": "Ada"}, headers={})
        debug.assert_not_called()

        monkeypatch.setattr(webhook_module.settings.observability, "include_request_body", True)
        acknowledge_webhook(payload={"buyer_name": "Ada"}, headers={})
        debug.assert_called_once()

    def test_field_names_are_capped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A hostile caller must not be able to inflate a log line at will."""
        info = MagicMock()
        monkeypatch.setattr("app.integrations.aliexpress.webhook.logger.info", info)

        acknowledge_webhook(payload={f"field_{i}": i for i in range(500)}, headers={})

        _, kwargs = info.call_args
        assert len(kwargs["field_names"]) == 40
        assert kwargs["field_count"] == 500

    def test_records_whether_a_signature_header_was_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Establishes empirically whether AliExpress signs deliveries, which is
        the open question blocking signature verification."""
        info = MagicMock()
        monkeypatch.setattr("app.integrations.aliexpress.webhook.logger.info", info)

        acknowledge_webhook(payload={}, headers={"content-type": "application/json"})
        assert info.call_args[1]["signature_header_present"] is False

        acknowledge_webhook(payload={}, headers={"x-iop-signature": "abc"})
        assert info.call_args[1]["signature_header_present"] is True


class TestParseWebhookPayload:
    @pytest.mark.asyncio
    async def test_parses_json_objects(self) -> None:
        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"message_type":"ORDER_STATUS","seller_id":"abc"}')
        request.headers = {"content-type": "application/json"}

        payload = await parse_webhook_payload(request)

        assert payload == {"message_type": "ORDER_STATUS", "seller_id": "abc"}

    @pytest.mark.asyncio
    async def test_parses_form_encoded_bodies(self) -> None:
        request = MagicMock()
        request.body = AsyncMock(return_value=b"message_type=ORDER_STATUS&seller_id=abc")
        request.headers = {"content-type": "application/x-www-form-urlencoded"}

        payload = await parse_webhook_payload(request)

        assert payload == {"message_type": "ORDER_STATUS", "seller_id": "abc"}

    @pytest.mark.asyncio
    async def test_empty_body_becomes_an_empty_dict(self) -> None:
        request = MagicMock()
        request.body = AsyncMock(return_value=b"")
        request.headers = {}

        assert await parse_webhook_payload(request) == {}

    @pytest.mark.asyncio
    async def test_malformed_json_is_kept_raw_rather_than_raising(self) -> None:
        """Raising here would become a 500, which a delivery agent retries."""
        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"broken": ')
        request.headers = {"content-type": "application/json"}

        assert await parse_webhook_payload(request) == {"_raw": '{"broken": '}

    @pytest.mark.asyncio
    async def test_a_json_array_is_wrapped(self) -> None:
        """The signature is a dict, so a top-level array must not escape as one."""
        request = MagicMock()
        request.body = AsyncMock(return_value=b"[1, 2]")
        request.headers = {"content-type": "application/json"}

        assert await parse_webhook_payload(request) == {"_value": [1, 2]}

    @pytest.mark.asyncio
    async def test_an_unknown_content_type_is_kept_raw(self) -> None:
        request = MagicMock()
        request.body = AsyncMock(return_value=b"<xml/>")
        request.headers = {"content-type": "application/xml"}

        assert await parse_webhook_payload(request) == {"_raw": "<xml/>"}


class TestExtractMessageId:
    def test_prefers_an_explicit_identifier(self) -> None:
        assert extract_message_id({"message_id": "m-1", "order_id": "9"}) == "m-1"

    def test_numeric_identifiers_are_accepted(self) -> None:
        assert extract_message_id({"msg_id": 42}) == "42"

    def test_identical_bodies_hash_identically_without_an_id(self) -> None:
        """A byte-identical replay must deduplicate even when the sender
        includes no identifier — that is what the content-hash fallback buys."""
        payload = {"message_type": "ORDER_STATUS", "order_id": "123"}
        assert extract_message_id(dict(payload)) == extract_message_id(dict(payload))

    def test_different_bodies_hash_differently(self) -> None:
        first = extract_message_id({"order_id": "1"})
        second = extract_message_id({"order_id": "2"})
        assert first != second


class TestClassifyWebhook:
    def test_order_types_are_recognised(self) -> None:
        assert classify_webhook({"type": "ORDER_STATUS"}) == "order"
        assert classify_webhook({"biz_type": "trade_order_paid"}) == "order"

    def test_order_field_names_are_recognised_without_a_type(self) -> None:
        assert classify_webhook({"order_id": "1", "status": "PAID"}) == "order"

    def test_unrecognised_payloads_are_unknown_not_guessed(self) -> None:
        assert classify_webhook({"foo": "bar"}) == "unknown"

    def test_non_order_types_keep_their_label(self) -> None:
        assert classify_webhook({"type": "TOKEN_REVOKED"}) == "token_revoked"


class TestProcessWebhook:
    """Replay protection and audit accounting.

    Processing deliberately mutates no order state — deliveries are unsigned
    and carry no verifiable tenant claim, so the ceiling is recognise,
    deduplicate, count, log. These tests pin that the ceiling behaves.
    """

    @pytest.mark.asyncio
    async def test_a_first_delivery_is_processed_and_counted(self, fake_redis: FakeRedis) -> None:
        outcome = await process_webhook({"message_id": "m-1", "type": "ORDER_STATUS"}, {})

        assert outcome == "processed"
        assert fake_redis.counters["webhooks:aliexpress:received"] == 1

    @pytest.mark.asyncio
    async def test_a_replayed_delivery_is_ignored_not_recounted(
        self, fake_redis: FakeRedis
    ) -> None:
        payload = {"message_id": "m-1", "type": "ORDER_STATUS"}

        assert await process_webhook(payload, {}) == "processed"
        assert await process_webhook(payload, {}) == "duplicate"
        assert fake_redis.counters["webhooks:aliexpress:received"] == 1

    @pytest.mark.asyncio
    async def test_an_identical_body_without_an_id_still_deduplicates(
        self, fake_redis: FakeRedis
    ) -> None:
        payload = {"order_id": "123", "status": "SHIPPED"}

        assert await process_webhook(dict(payload), {}) == "processed"
        assert await process_webhook(dict(payload), {}) == "duplicate"

    @pytest.mark.asyncio
    async def test_an_empty_payload_is_not_counted(self, fake_redis: FakeRedis) -> None:
        assert await process_webhook({}, {}) == "empty"
        assert fake_redis.counters == {}

    @pytest.mark.asyncio
    async def test_redis_outage_degrades_toward_processing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A cache outage must not silently drop deliveries. Processing a
        duplicate costs a log line; dropping a genuine delivery costs an order
        update nobody hears about."""

        class BrokenRedis:
            async def set(self, *args: Any, **kwargs: Any) -> Any:
                raise RedisError("down")

            async def incr(self, *args: Any, **kwargs: Any) -> int:
                raise RedisError("down")

        monkeypatch.setattr(
            "app.integrations.aliexpress.webhook.get_redis",
            lambda *_args, **_kwargs: BrokenRedis(),
        )

        outcome = await process_webhook({"message_id": "m-1"}, {})
        assert outcome == "processed"

    @pytest.mark.asyncio
    async def test_no_customer_data_reaches_the_processing_log(
        self, fake_redis: FakeRedis, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The processing log line records kind and a hashed id — never the
        payload, which carries buyer PII on a real order notification."""
        info = MagicMock()
        monkeypatch.setattr("app.integrations.aliexpress.webhook.logger.info", info)

        await process_webhook(
            {"order_id": "9", "buyer_name": "Ada Lovelace", "buyer_address": "12 Mill Lane"},
            {},
        )

        rendered = str(info.call_args_list)
        assert "Ada Lovelace" not in rendered
        assert "12 Mill Lane" not in rendered


class TestReceiveWebhook:
    """The 200-always contract.

    The module promises AliExpress will not retry. That promise is only kept if
    no path out of the handler raises.
    """

    @pytest.mark.asyncio
    async def test_acknowledges_even_when_the_body_cannot_be_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A client that disconnects mid-upload makes `body()` raise. Without a
        guard that becomes a 500, provoking the redelivery this design avoids.
        """
        from app.integrations.aliexpress import webhook as webhook_module

        request = MagicMock()
        request.body = AsyncMock(side_effect=ConnectionError("client went away"))
        request.headers = {}

        exception = MagicMock()
        monkeypatch.setattr(webhook_module.logger, "exception", exception)

        response = await webhook_module.receive_webhook(request)

        assert response.status == "received"
        exception.assert_called_once()
