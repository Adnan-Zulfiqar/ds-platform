"""WooCommerce REST API v3 client (Track E7).

The merchant supplies the site URL, so every call is an outbound request to
an address a customer chose. Each request therefore goes through the same
SSRF contract as product image fetches (`pin_https_target`): HTTPS only,
every resolved address must be globally routable, the connection is pinned
to the vetted address, and redirects are **refused** rather than followed.
The refusal names the target so the merchant can enter that URL instead.

Authentication is HTTP Basic with the merchant's consumer key and secret,
which WooCommerce accepts over HTTPS. Neither value is ever logged or put in
an error.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any, Final
from urllib.parse import urlencode, urlparse

import httpx

from app.ai.image_fetch import ImageFetchError, pin_https_target, system_resolve
from app.core.exceptions import AppError

API_PREFIX: Final = "/wp-json/wc/v3"
TIMEOUT: Final = httpx.Timeout(15.0, connect=5.0)
#: A settings or product response is a few KiB; this bounds a hostile site.
MAX_BODY: Final = 2_097_152
_REDIRECTS: Final = frozenset({301, 302, 303, 307, 308})

#: Seams for tests. Production uses the system resolver and the real network.
Resolver = Callable[[str], Sequence[str]]
_resolver: Resolver = system_resolve
_transport: httpx.AsyncBaseTransport | None = None


class WooCommerceError(AppError):
    code = "woocommerce_error"
    status_code = 502
    message = "The WooCommerce store returned an error."


class WooCommerceUrlRejectedError(WooCommerceError):
    code = "woocommerce_url_rejected"
    status_code = 422
    message = "That store address cannot be used. Enter the public https:// address of the site."


class WooCommerceAuthError(WooCommerceError):
    code = "woocommerce_auth_failed"
    status_code = 422
    message = (
        "WooCommerce refused the API keys. Check the consumer key and secret, and that "
        "the key has Read/Write permission."
    )


class WooCommerceRejectedError(WooCommerceError):
    """The store answered and said no (W2). Its own message is passed on —
    it is the merchant's own site describing the merchant's own data."""

    code = "woocommerce_rejected"
    status_code = 422
    message = "The WooCommerce store refused the request."


class WooCommerceUnreachableError(WooCommerceError):
    code = "woocommerce_unreachable"
    status_code = 502
    message = "The WooCommerce store could not be reached. Check the address and try again."


def normalise_site_url(raw: str) -> str:
    """``https://shop.example.com/sub`` form: scheme and host lower-cased, no
    trailing slash, query or fragment. Plain ``http`` is refused: Basic
    credentials must never cross the network unencrypted."""
    parsed = urlparse(raw.strip())
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise WooCommerceUrlRejectedError()
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise WooCommerceUrlRejectedError()
    if parsed.port not in (None, 443):
        raise WooCommerceUrlRejectedError()
    path = parsed.path.rstrip("/")
    return f"https://{parsed.hostname.lower()}{path}"


def _store_message(raw: bytes) -> str | None:
    """WooCommerce errors are ``{"code", "message", "data"}``; pass on the
    message, bounded, or fall back to the generic text."""
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    message = parsed.get("message") if isinstance(parsed, dict) else None
    return f"WooCommerce: {str(message)[:300]}" if message else None


class WooCommerceClient:
    def __init__(self, *, site_url: str, consumer_key: str, consumer_secret: str) -> None:
        self.site_url = normalise_site_url(site_url)
        self._auth = httpx.BasicAuth(consumer_key, consumer_secret)

    async def get(self, path: str, params: dict[str, str] | None = None) -> Any:
        return await self._request("GET", path, params=params)

    async def post(self, path: str, body: dict[str, Any]) -> Any:
        return await self._request("POST", path, body=body)

    async def put(self, path: str, body: dict[str, Any]) -> Any:
        return await self._request("PUT", path, body=body)

    async def delete(self, path: str, params: dict[str, str] | None = None) -> Any:
        return await self._request("DELETE", path, params=params)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.site_url}{API_PREFIX}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"
        try:
            hostname, connection_url = await pin_https_target(url, resolver=_resolver)
        except ImageFetchError:
            raise WooCommerceUrlRejectedError() from None

        kwargs: dict[str, Any] = {
            "timeout": TIMEOUT,
            "follow_redirects": False,
            "trust_env": False,
            "verify": True,
            "auth": self._auth,
        }
        if _transport is not None:
            kwargs["transport"] = _transport
        try:
            async with (
                httpx.AsyncClient(**kwargs) as client,
                client.stream(
                    method,
                    connection_url,
                    headers={"Host": hostname, "Accept": "application/json"},
                    json=body,
                    extensions={"sni_hostname": hostname},
                ) as response,
            ):
                if response.status_code in _REDIRECTS:
                    raise WooCommerceUrlRejectedError(
                        "The store address redirects elsewhere. Enter the address it redirects to.",
                        details={"location": response.headers.get("location", "")[:300]},
                    )
                if response.status_code in (401, 403):
                    raise WooCommerceAuthError()
                received = bytearray()
                async for chunk in response.aiter_bytes():
                    received.extend(chunk)
                    if len(received) > MAX_BODY:
                        raise WooCommerceError("The store's response was too large.")
                # A 404 *from the REST API* (JSON with a message: "invalid
                # product id") is an answer; a 404 page is a site without one.
                if response.status_code == 404 and _store_message(bytes(received)) is None:
                    raise WooCommerceUnreachableError(
                        "No WooCommerce REST API was found at that address. Check that "
                        "WooCommerce is installed and permalinks are enabled."
                    )
                if 400 <= response.status_code < 500:
                    raise WooCommerceRejectedError(_store_message(bytes(received)))
                if response.status_code >= 400:
                    raise WooCommerceError(details={"status": response.status_code})
        except httpx.TimeoutException:
            raise WooCommerceUnreachableError() from None
        except httpx.RequestError:
            raise WooCommerceUnreachableError() from None
        try:
            return json.loads(bytes(received))
        except ValueError:
            raise WooCommerceError("The store did not answer with JSON.") from None


__all__ = [
    "WooCommerceAuthError",
    "WooCommerceClient",
    "WooCommerceError",
    "WooCommerceRejectedError",
    "WooCommerceUnreachableError",
    "WooCommerceUrlRejectedError",
    "normalise_site_url",
]
