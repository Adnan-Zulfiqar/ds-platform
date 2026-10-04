"""A small Stripe REST client (Track E6).

``httpx`` against the documented form-encoded API rather than the ``stripe``
package: the handful of calls billing needs (customers, checkout and portal
sessions, subscriptions, prices by lookup key) do not justify a new
dependency in the hash-locked requirements, and the webhook signature scheme
is a dozen lines of ``hmac`` (``verify_signature``).

The secret key is sent only to Stripe and never logged. Stripe's own error
message is passed on only for 4xx responses: those describe the request
DropPilot made, never a card or a customer.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Mapping, Sequence
from typing import Any, Final

import httpx

from app.core.config import settings
from app.core.exceptions import AppError

#: Seam for tests; production uses the real network.
_transport: httpx.AsyncBaseTransport | None = None


class BillingNotConfiguredError(AppError):
    code = "billing_not_configured"
    status_code = 503
    message = "Billing is not configured on this server."


class StripeRequestError(AppError):
    code = "stripe_request_failed"
    status_code = 502
    message = "Stripe could not complete the request. Please try again."


class StripeSignatureError(AppError):
    code = "stripe_signature_invalid"
    status_code = 400
    message = "The Stripe signature is not valid."


def _flatten(prefix: str, value: Any, out: list[tuple[str, str]]) -> None:
    """Stripe's form encoding: ``a[b][0][c]=…``."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            _flatten(f"{prefix}[{key}]" if prefix else str(key), item, out)
    elif isinstance(value, Sequence) and not isinstance(value, str | bytes):
        for index, item in enumerate(value):
            _flatten(f"{prefix}[{index}]", item, out)
    elif isinstance(value, bool):
        out.append((prefix, "true" if value else "false"))
    elif value is not None:
        out.append((prefix, str(value)))


def encode_form(params: Mapping[str, Any]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    _flatten("", params, out)
    return out


class StripeClient:
    def __init__(self) -> None:
        key = settings.stripe.secret_key
        if key is None or not key.get_secret_value().strip():
            raise BillingNotConfiguredError()
        self._key = key.get_secret_value().strip()

    async def get(self, path: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return await self._request("GET", path, params=params)

    async def post(
        self,
        path: str,
        params: Mapping[str, Any] | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return await self._request("POST", path, data=params, idempotency_key=idempotency_key)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        headers = {"Stripe-Version": "2026-08-26.dahlia"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        kwargs: dict[str, Any] = {
            "timeout": settings.stripe.request_timeout_seconds,
            "auth": (self._key, ""),
        }
        if _transport is not None:
            kwargs["transport"] = _transport
        try:
            async with httpx.AsyncClient(base_url=settings.stripe.api_base, **kwargs) as client:
                response = await client.request(
                    method,
                    f"/v1{path}",
                    params=dict(encode_form(params)) if params else None,
                    # Flattened keys carry their indices, so they are unique.
                    data=dict(encode_form(data)) if data else None,
                    headers=headers,
                )
        except httpx.HTTPError:
            raise StripeRequestError() from None
        try:
            body: dict[str, Any] = response.json()
        except ValueError:
            raise StripeRequestError() from None
        if response.status_code >= 400:
            if 400 <= response.status_code < 500:
                message = (body.get("error") or {}).get("message")
                raise StripeRequestError(f"Stripe: {str(message)[:300]}" if message else None)
            raise StripeRequestError()
        return body

    async def prices_by_lookup_key(self, keys: Sequence[str]) -> dict[str, str]:
        """``lookup_key → price id`` for the active prices that exist."""
        body = await self.get("/prices", {"lookup_keys": list(keys), "active": True, "limit": 20})
        return {
            str(p["lookup_key"]): str(p["id"])
            for p in body.get("data", [])
            if isinstance(p, dict) and p.get("lookup_key")
        }


SIGNATURE_SCHEME: Final = "v1"


def verify_signature(*, payload: bytes, header: str, secret: str, now: float | None = None) -> None:
    """Stripe's scheme: ``Stripe-Signature: t=<ts>,v1=<hex>[,v1=…]`` where
    ``v1 = HMAC-SHA256(secret, f"{t}.{payload}")``. Raises unless one ``v1``
    matches and ``t`` is within the tolerance (replay window)."""
    timestamp: int | None = None
    candidates: list[str] = []
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t" and value.isdigit():
            timestamp = int(value)
        elif key == SIGNATURE_SCHEME and value:
            candidates.append(value)
    if timestamp is None or not candidates:
        raise StripeSignatureError()
    if (
        abs((time.time() if now is None else now) - timestamp)
        > settings.stripe.webhook_tolerance_seconds
    ):
        raise StripeSignatureError()
    expected = hmac.new(
        secret.encode("utf-8"), f"{timestamp}.".encode() + payload, hashlib.sha256
    ).hexdigest()
    if not any(hmac.compare_digest(expected, candidate) for candidate in candidates):
        raise StripeSignatureError()


def parse_event(payload: bytes) -> dict[str, Any]:
    try:
        event = json.loads(payload)
    except ValueError:
        raise StripeSignatureError() from None
    if not isinstance(event, dict):
        raise StripeSignatureError()
    return event


__all__ = [
    "BillingNotConfiguredError",
    "StripeClient",
    "StripeRequestError",
    "StripeSignatureError",
    "encode_form",
    "parse_event",
    "verify_signature",
]
