"""Unit tests for AliExpress webhook ingestion."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.integrations.aliexpress.webhook import acknowledge_webhook, parse_webhook_payload

pytestmark = pytest.mark.unit


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
