"""eBay call limits, for the operator (EBAY-C6).

The Developer Analytics API reports how much of this application's daily
quota each API has left. Read on demand by the eBay health endpoint, never
on a schedule: it exists to answer "are we about to hit eBay's limits?", and
the answer that matters is the one at the moment someone asks.

A failure here never fails the health check — the limits come back as
``None`` and the rest of the report stands.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.ebay.tokens import application_access_token

logger = get_logger(__name__)

#: The Sell APIs the seller channel calls (C2 to C5). Taxonomy is a Commerce
#: API under the application's own quota and is not listed here.
_WATCHED = frozenset({"inventory", "fulfillment", "account"})


@dataclass(frozen=True, slots=True)
class EbayCallLimit:
    api: str
    resource: str
    limit: int
    remaining: int
    reset: str | None


def parse_rate_limits(payload: Any) -> tuple[EbayCallLimit, ...]:
    if not isinstance(payload, Mapping):
        return ()
    out: list[EbayCallLimit] = []
    for entry in payload.get("rateLimits") or []:
        # eBay's own examples capitalise API names; compare case-insensitively.
        name = str(entry.get("apiName") or "").lower() if isinstance(entry, Mapping) else ""
        if not isinstance(entry, Mapping) or name not in _WATCHED:
            continue
        for resource in entry.get("resources") or []:
            if not isinstance(resource, Mapping):
                continue
            for rate in resource.get("rates") or []:
                if not isinstance(rate, Mapping):
                    continue
                limit, remaining = rate.get("limit"), rate.get("remaining")
                if not isinstance(limit, int) or not isinstance(remaining, int):
                    continue
                reset = rate.get("reset")
                out.append(
                    EbayCallLimit(
                        api=name,
                        resource=str(resource.get("name") or ""),
                        limit=limit,
                        remaining=remaining,
                        reset=reset if isinstance(reset, str) else None,
                    )
                )
    return tuple(out)


async def call_limits() -> tuple[EbayCallLimit, ...] | None:
    try:
        token = await application_access_token()
        timeout = httpx.Timeout(
            settings.ebay.request_timeout_seconds,
            connect=settings.ebay.connect_timeout_seconds,
        )
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                f"{settings.ebay.notification_api_base}/developer/analytics/v1_beta/rate_limit/",
                params={"api_context": "sell"},
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
    except Exception as exc:  # any failure degrades to "unknown" (module docstring)
        logger.warning("ebay_call_limits_unavailable", error=type(exc).__name__)
        return None
    if response.status_code != httpx.codes.OK:
        logger.warning("ebay_call_limits_unavailable", status_code=response.status_code)
        return None
    try:
        return parse_rate_limits(response.json())
    except ValueError:
        return None


__all__ = ["EbayCallLimit", "call_limits", "parse_rate_limits"]
