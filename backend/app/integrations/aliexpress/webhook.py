"""AliExpress inbound webhook handling.

AliExpress pushes order and shipping notifications here. This module is separate
from the OAuth callback — the callback completes a browser redirect; webhooks
are server-to-server POSTs with a JSON (or form) body.

Phase 5 adds recognition, replay protection and audit accounting on top of the
Phase 3 acknowledgement. What it deliberately does **not** add is any state
mutation, and the reason is a security boundary rather than an omission:

* **Deliveries are unsigned by default.** AliExpress has not confirmed a
  public signing scheme for this endpoint. Phase 7 adds opt-in HMAC via
  ``ALIEXPRESS_WEBHOOK_SECRET`` and shed-without-429 limiting when the secret
  is unset (see ``webhook_security.py``). An unsigned body remains
  attacker-controlled input.
* **Deliveries carry no verifiable tenant claim.** The stored connection keeps
  no external seller identifier to match against, so a payload cannot be
  attributed to a tenant without trusting the payload itself — which is
  exactly the thing that cannot be trusted.

Letting an unsigned, unattributable POST write to any tenant's orders would be
an unauthenticated write path into customer data. Until deliveries are signed,
the honest ceiling is: recognise, deduplicate, count, log — and let the
polling sync (which authenticates outbound) remain the source of truth.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qs

from redis.exceptions import RedisError
from starlette.requests import Request

from app.core.config import settings
from app.core.exceptions import AuthenticationError
from app.core.logging import get_logger
from app.core.redis import RedisPurpose, get_redis
from app.integrations.aliexpress.schemas import AliExpressWebhookAckResponse
from app.integrations.aliexpress.webhook_security import (
    allow_webhook_under_shed,
    configured_webhook_secret,
    verify_webhook_signature,
    webhook_secret_configured,
)

logger = get_logger(__name__)

#: Cap on how many field names one log line may carry, so a hostile caller
#: cannot inflate the log by posting an object with thousands of keys.
_MAX_LOGGED_FIELD_NAMES = 40

#: Header names that may carry a delivery signature. Recorded as a boolean only,
#: to establish empirically whether AliExpress signs deliveries — the question
#: the signature TODO below is waiting on.
_SIGNATURE_HEADERS = ("x-aliexpress-signature", "x-iop-signature", "sign", "signature")

#: Payload fields that may carry a delivery identifier, in preference order.
#: The message shape is undocumented, so recognised names are tried first and a
#: content hash is the fallback — a replayed identical body still deduplicates.
_MESSAGE_ID_FIELDS = ("message_id", "msg_id", "id", "event_id", "notify_id")

#: Payload fields whose value hints at the notification type.
_TYPE_FIELDS = ("type", "msg_type", "biz_type", "topic", "event")

#: How long a delivery id is remembered for replay protection. Long enough to
#: cover any plausible redelivery schedule; short enough that the key space
#: cannot grow without bound.
_REPLAY_TTL_SECONDS = 7 * 24 * 3600

#: Counter surfaced by /orders/statistics. Global rather than tenant-scoped
#: because deliveries carry no verifiable tenant claim (see module docstring).
WEBHOOK_COUNTER_KEY = "webhooks:aliexpress:received"


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


def extract_message_id(payload: Mapping[str, Any]) -> str:
    """A stable identifier for this delivery, for replay protection.

    Prefers an explicit id field; falls back to a content hash so that a
    byte-identical replay is still recognised even when the sender includes no
    identifier. Two genuinely different messages never collide, and that is
    the only property replay protection needs.
    """
    for field in _MESSAGE_ID_FIELDS:
        value = payload.get(field)
        if isinstance(value, str | int) and str(value).strip():
            return str(value).strip()

    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def classify_webhook(payload: Mapping[str, Any]) -> str:
    """A coarse label for what kind of notification arrived.

    Only the label is trusted for logging and metrics — never for a write. The
    message vocabulary is undocumented, so anything mentioning an order is
    "order" and everything else keeps its raw type or falls to "unknown".
    """
    for field in _TYPE_FIELDS:
        value = payload.get(field)
        if isinstance(value, str) and value.strip():
            return "order" if "order" in value.lower() else value.strip().lower()[:64]
    if any("order" in key.lower() for key in payload):
        return "order"
    return "unknown"


async def register_delivery(message_id: str) -> bool:
    """Record a delivery id, returning whether it is new.

    Redis ``SET NX`` gives an atomic test-and-set, so two concurrent replays
    cannot both pass. **Degrades toward processing**: when Redis is down the
    delivery is treated as new, because the consequence of a duplicate here is
    a duplicate log line — while the consequence of dropping a genuine
    delivery is silence about a real order. That trade-off must be revisited
    the moment webhook payloads drive writes.
    """
    try:
        client = get_redis(RedisPurpose.CACHE)
        is_new = await client.set(
            f"webhooks:aliexpress:seen:{message_id}",
            "1",
            nx=True,
            ex=_REPLAY_TTL_SECONDS,
        )
        return bool(is_new)
    except (RedisError, OSError):
        logger.warning("webhook_replay_guard_unavailable")
        return True


async def count_delivery() -> None:
    """Increment the activity counter surfaced by /orders/statistics."""
    try:
        await get_redis(RedisPurpose.CACHE).incr(WEBHOOK_COUNTER_KEY)
    except (RedisError, OSError):
        # Degrade, never fail: the counter is telemetry, not the delivery.
        logger.warning("webhook_counter_unavailable")


async def process_webhook(payload: Mapping[str, Any], headers: Mapping[str, str]) -> str:
    """Recognise, deduplicate and account for one delivery.

    Returns the outcome — ``"processed"``, ``"duplicate"`` or ``"empty"`` —
    which the tests assert on and the audit log records. Deliberately performs
    **no state mutation**; see the module docstring for why unsigned,
    unattributable input must not reach the orders tables.
    """
    if not payload:
        return "empty"

    message_id = extract_message_id(payload)
    kind = classify_webhook(payload)

    if not await register_delivery(message_id):
        # The audit trail records the replay; the caller still acknowledges,
        # because re-acking a duplicate is what stops the sender re-sending.
        logger.info(
            "aliexpress_webhook_duplicate",
            message_kind=kind,
            message_id_hash=hashlib.sha256(message_id.encode()).hexdigest()[:16],
        )
        return "duplicate"

    await count_delivery()
    logger.info(
        "aliexpress_webhook_processed",
        message_kind=kind,
        message_id_hash=hashlib.sha256(message_id.encode()).hexdigest()[:16],
        signature_present=any(name in headers for name in _SIGNATURE_HEADERS),
    )
    return "processed"


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

    Signature verification is opt-in via ``ALIEXPRESS_WEBHOOK_SECRET``; see
    ``webhook_security.py``. Until a secret is configured, this endpoint
    accepts any POST and remains non-mutating.
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

    **Signature mode** (secret configured): invalid or missing signatures raise
    :class:`AuthenticationError` (401). Attackers should not get a 200 that
    looks like acceptance.

    **Unsigned mode** (default): 200 is returned even when processing is shed
    or Redis fails, so a delivery agent does not retry-storm. Processing
    failures are swallowed because nothing here mutates state — the polling
    sync remains the source of truth.
    """
    try:
        raw_body = await request.body()
        payload = await parse_webhook_payload(request)
        header_map = {key.lower(): value for key, value in request.headers.items()}
    except Exception:
        logger.exception("aliexpress_webhook_unreadable")
        return AliExpressWebhookAckResponse()

    if webhook_secret_configured():
        if not verify_webhook_signature(
            secret=configured_webhook_secret(),
            raw_body=raw_body,
            headers=header_map,
        ):
            logger.warning("aliexpress_webhook_signature_rejected")
            raise AuthenticationError("Webhook signature is not valid.")
    else:
        client_ip = request.client.host if request.client else "unknown"
        if not await allow_webhook_under_shed(client_ip=client_ip):
            # Still acknowledge — returning 429 would provoke redelivery.
            return acknowledge_webhook(payload=payload, headers=header_map)

    try:
        await process_webhook(payload, header_map)
    except Exception:
        logger.exception("aliexpress_webhook_processing_failed")

    return acknowledge_webhook(payload=payload, headers=header_map)


__all__ = [
    "WEBHOOK_COUNTER_KEY",
    "acknowledge_webhook",
    "classify_webhook",
    "count_delivery",
    "extract_message_id",
    "parse_webhook_payload",
    "process_webhook",
    "receive_webhook",
    "register_delivery",
]
