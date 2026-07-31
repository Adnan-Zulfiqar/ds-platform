"""AliExpress inbound webhook handling.

AliExpress pushes order and shipping notifications here. This module is separate
from the OAuth callback — the callback completes a browser redirect; webhooks
are server-to-server POSTs with a JSON (or form) body.

Processing is intentionally stubbed: the endpoint acknowledges receipt, logs the
payload for inspection, and returns immediately so AliExpress does not retry.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qs

from starlette.requests import Request

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.aliexpress.schemas import AliExpressWebhookAckResponse

logger = get_logger(__name__)

#: Cap on how many field names one log line may carry, so a hostile caller
#: cannot inflate the log by posting an object with thousands of keys.
_MAX_LOGGED_FIELD_NAMES = 40

#: Header names that may carry a delivery signature. Recorded as a boolean only,
#: to establish empirically whether AliExpress signs deliveries — the question
#: the signature TODO below is waiting on.
_SIGNATURE_HEADERS = ("x-aliexpress-signature", "x-iop-signature", "sign", "signature")


async def parse_webhook_payload(request: Request) -> dict[str, Any]:
    """Normalise the request body into a dictionary for logging."""
    raw_body = await request.body()
    if not raw_body:
        return {}

    content_type = request.headers.get("content-type", "").lower()
    if "application/json" in content_type:
        try:
            parsed = json.loads(raw_body)
        except json.JSONDecodeError:
            return {"_raw": raw_body.decode("utf-8", errors="replace")}
        if isinstance(parsed, dict):
            return parsed
        return {"_value": parsed}

    if "application/x-www-form-urlencoded" in content_type:
        decoded = raw_body.decode("utf-8", errors="replace")
        form = parse_qs(decoded, keep_blank_values=True)
        return {key: values[0] if len(values) == 1 else values for key, values in form.items()}

    return {"_raw": raw_body.decode("utf-8", errors="replace")}


def acknowledge_webhook(
    *,
    payload: Mapping[str, Any],
    headers: Mapping[str, str],
) -> AliExpressWebhookAckResponse:
    """Record a webhook delivery and return an immediate acknowledgement.

    **The payload is logged as shape, not content.** An order or shipping
    notification carries buyer names, addresses and phone numbers, and
    ``CLAUDE.md`` forbids logging a request body containing customer data. Field
    *names* and counts are enough to identify what arrived and to build the
    parser against; the values are not.

    The body itself is emitted only at DEBUG and only when
    ``LOG_INCLUDE_REQUEST_BODY`` is enabled — a switch ``Settings`` refuses in
    any deployed environment. So a developer working against real deliveries can
    see everything, and production cannot leak it.

    TODO: verify AliExpress webhook signatures once their signing scheme is
    confirmed against current developer documentation. Until then, this endpoint
    accepts any POST — acceptable only while no business logic runs here.
    """
    logger.info(
        "aliexpress_webhook_received",
        field_names=sorted(payload)[:_MAX_LOGGED_FIELD_NAMES],
        field_count=len(payload),
        header_keys=sorted(headers.keys()),
        signature_header_present=any(name in headers for name in _SIGNATURE_HEADERS),
    )

    if settings.observability.include_request_body:
        logger.debug("aliexpress_webhook_body", payload=dict(payload))

    return AliExpressWebhookAckResponse()


async def receive_webhook(request: Request) -> AliExpressWebhookAckResponse:
    """Parse an inbound webhook request and acknowledge it.

    **200 is unconditional, including when parsing fails.** The module docstring
    promises AliExpress will not retry, and an uncaught exception would break
    that promise by returning 500 — provoking exactly the redelivery the design
    is trying to avoid, on a schedule this application does not control.

    Reading the body can fail independently of its content: a client that
    disconnects mid-upload raises rather than returning bytes. So the guard is
    deliberately broad. Nothing is lost by it, because nothing here acts on the
    payload yet; the failure is recorded and alerting is the right place to
    surface it.

    This has to be revisited when processing arrives. Acknowledging a delivery
    that was never understood means dropping it, which is only acceptable while
    the handler is inert.
    """
    try:
        payload = await parse_webhook_payload(request)
        header_map = {key.lower(): value for key, value in request.headers.items()}
    except Exception:
        logger.exception("aliexpress_webhook_unreadable")
        return AliExpressWebhookAckResponse()

    return acknowledge_webhook(payload=payload, headers=header_map)


__all__ = ["acknowledge_webhook", "parse_webhook_payload", "receive_webhook"]
