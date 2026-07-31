"""Webhook signature verification and shed-without-429 limiting.

AliExpress has not published a confirmed signing scheme for this push endpoint
(M11). Until a real delivery proves otherwise, verification is **opt-in** via
``ALIEXPRESS_WEBHOOK_SECRET``:

* Secret set → HMAC-SHA256 over the raw body; mismatch or missing header → 401.
* Secret unset → no signature check; a per-IP shed limiter drops excess traffic
  while still acknowledging with 200 so legitimate agents do not retry-storm (M12).

Neither mode mutates order state. See ``webhook.py``.
"""

from __future__ import annotations

import hashlib
import hmac

from redis.exceptions import RedisError

from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis import RedisPurpose, get_redis

logger = get_logger(__name__)

_SIGNATURE_HEADERS = (
    "x-aliexpress-signature",
    "x-iop-signature",
    "x-signature",
    "sign",
    "signature",
)

_SHED_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return current
"""


def extract_signature_header(headers: dict[str, str]) -> str | None:
    for name in _SIGNATURE_HEADERS:
        value = headers.get(name)
        if value and value.strip():
            return value.strip()
    return None


def compute_hmac_sha256_hex(*, secret: str, raw_body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()


def verify_webhook_signature(*, secret: str, raw_body: bytes, headers: dict[str, str]) -> bool:
    """Return True when the provided signature matches the body.

    Accepts a bare hex digest or a ``sha256=<hex>`` form. Comparison is
    constant-time.
    """
    provided = extract_signature_header(headers)
    if provided is None:
        return False

    candidate = provided
    if candidate.lower().startswith("sha256="):
        candidate = candidate.split("=", 1)[1].strip()

    expected = compute_hmac_sha256_hex(secret=secret, raw_body=raw_body)
    return hmac.compare_digest(expected, candidate)


async def allow_webhook_under_shed(*, client_ip: str) -> bool:
    """Return False when this IP has exceeded the shed limit for the window.

    Failures degrade to allow (same trade-off as replay protection): dropping a
    genuine delivery because Redis is down is worse than absorbing a burst.
    """
    limit = settings.aliexpress.webhook_shed_limit
    window = settings.aliexpress.webhook_shed_window_seconds
    key = f"webhooks:aliexpress:shed:{client_ip or 'unknown'}"
    try:
        client = get_redis(RedisPurpose.CACHE)
        # redis-py stubs leave ``eval`` untyped.
        current = await client.eval(_SHED_SCRIPT, 1, key, str(window))  # type: ignore[no-untyped-call]
        allowed = int(current) <= limit
        if not allowed:
            logger.warning(
                "aliexpress_webhook_shed",
                client_ip=client_ip,
                count=int(current),
                limit=limit,
            )
        return allowed
    except (RedisError, OSError, TypeError, ValueError, RuntimeError):
        # RuntimeError covers asyncio "Event loop is closed" during test teardown
        # and similar client lifecycle faults — still fail open.
        logger.warning("webhook_shed_unavailable")
        return True


def webhook_secret_configured() -> bool:
    secret = settings.aliexpress.webhook_secret
    return secret is not None and bool(secret.get_secret_value())


def configured_webhook_secret() -> str:
    secret = settings.aliexpress.webhook_secret
    if secret is None:
        return ""
    return secret.get_secret_value()


__all__ = [
    "allow_webhook_under_shed",
    "compute_hmac_sha256_hex",
    "configured_webhook_secret",
    "extract_signature_header",
    "verify_webhook_signature",
    "webhook_secret_configured",
]
