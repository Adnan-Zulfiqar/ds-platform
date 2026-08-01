"""Shopify Admin REST client.

The only module that talks to Shopify over the network. Credentials never reach
logs. Transient failures retry; 4xx auth failures do not.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.rate_limiter import OutboundRateLimiter, compute_backoff
from app.integrations.shopify.exceptions import (
    ShopifyAuthError,
    ShopifyError,
    ShopifyRateLimitError,
    ShopifyResponseError,
    ShopifyTimeoutError,
)

logger = get_logger(__name__)


class ShopifyClient:
    def __init__(
        self,
        *,
        shop_domain: str,
        access_token: str,
        tenant_id: str,
    ) -> None:
        self._shop = shop_domain
        self._token = access_token
        self._tenant_id = tenant_id
        self._config = settings.shopify
        self._rate_limiter = OutboundRateLimiter(
            provider="shopify",
            limit=self._config.rate_limit_requests,
            window_seconds=self._config.rate_limit_window_seconds,
        )

    def _url(self, path: str) -> str:
        version = self._config.api_version
        cleaned = path if path.startswith("/") else f"/{path}"
        return f"https://{self._shop}/admin/api/{version}{cleaned}"

    async def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = {
            "X-Shopify-Access-Token": self._token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        decision = await self._rate_limiter.acquire(self._tenant_id)
        if not decision.allowed:
            raise ShopifyRateLimitError()

        attempts = self._config.max_retries + 1
        last_error: Exception | None = None

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(
                self._config.request_timeout_seconds,
                connect=self._config.connect_timeout_seconds,
            )
        ) as client:
            for attempt in range(attempts):
                try:
                    response = await client.request(
                        method,
                        self._url(path),
                        headers=headers,
                        json=json_body,
                        params=params,
                    )
                except httpx.TimeoutException as exc:
                    last_error = ShopifyTimeoutError()
                    if attempt + 1 >= attempts:
                        raise last_error from exc
                    await asyncio.sleep(
                        compute_backoff(attempt, base_seconds=1.0, max_seconds=30.0)
                    )
                    continue
                except httpx.HTTPError as exc:
                    last_error = ShopifyError(str(exc))
                    if attempt + 1 >= attempts:
                        raise last_error from exc
                    await asyncio.sleep(
                        compute_backoff(attempt, base_seconds=1.0, max_seconds=30.0)
                    )
                    continue

                if response.status_code == 429:
                    last_error = ShopifyRateLimitError()
                    if attempt + 1 >= attempts:
                        raise last_error
                    retry_after = float(response.headers.get("Retry-After", "1"))
                    await asyncio.sleep(
                        max(
                            retry_after,
                            compute_backoff(attempt, base_seconds=1.0, max_seconds=30.0),
                        )
                    )
                    continue

                if response.status_code in {401, 403}:
                    raise ShopifyAuthError()

                if response.status_code >= 500:
                    last_error = ShopifyResponseError(
                        f"Shopify returned HTTP {response.status_code}",
                        upstream_code=str(response.status_code),
                    )
                    if attempt + 1 >= attempts:
                        raise last_error
                    await asyncio.sleep(
                        compute_backoff(attempt, base_seconds=1.0, max_seconds=30.0)
                    )
                    continue

                if response.status_code >= 400:
                    raise ShopifyResponseError(
                        f"Shopify returned HTTP {response.status_code}",
                        upstream_code=str(response.status_code),
                        details={"body": response.text[:500]},
                    )

                if not response.content:
                    return {}
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ShopifyResponseError("Unexpected Shopify response shape.")
                logger.info(
                    "shopify_request_ok",
                    method=method,
                    path=path,
                    status=response.status_code,
                )
                return payload

        assert last_error is not None
        raise last_error

    async def get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self.request("GET", path, params=params)

    async def post(self, path: str, *, json_body: dict[str, Any]) -> dict[str, Any]:
        return await self.request("POST", path, json_body=json_body)

    async def put(self, path: str, *, json_body: dict[str, Any]) -> dict[str, Any]:
        return await self.request("PUT", path, json_body=json_body)

    @staticmethod
    async def exchange_token(
        *,
        shop_domain: str,
        code: str,
        api_key: str,
        api_secret: str,
    ) -> dict[str, Any]:
        url = f"https://{shop_domain}/admin/oauth/access_token"
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                url,
                json={"client_id": api_key, "client_secret": api_secret, "code": code},
            )
        if response.status_code >= 400:
            logger.warning(
                "shopify_token_exchange_http_error",
                shop_domain=shop_domain,
                status_code=response.status_code,
            )
            raise ShopifyAuthError()
        payload = response.json()
        if not isinstance(payload, dict) or "access_token" not in payload:
            logger.warning(
                "shopify_token_exchange_invalid_payload",
                shop_domain=shop_domain,
                status_code=response.status_code,
                has_access_token=isinstance(payload, dict) and "access_token" in payload,
            )
            raise ShopifyAuthError()
        return payload
