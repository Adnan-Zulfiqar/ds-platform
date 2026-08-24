"""Fetch and cache eBay notification public keys.

Two calls to eBay, both to fixed hosts this module builds itself:

* ``POST {base}/identity/v1/oauth2/token`` — client-credentials grant, scope
  ``https://api.ebay.com/oauth/api_scope``, exactly as ``getPublicKey``'s
  reference specifies;
* ``GET {base}/commerce/notification/v1/public_key/{public_key_id}``.

**No caller supplies a URL.** The base comes from configuration as one of two
constants, and the only variable part of the path is a ``uuid.UUID`` — a type
that cannot hold a slash, a scheme or a host. SSRF and path traversal are
prevented by construction rather than by a filter that has to be right.

eBay is explicit that the key must be cached: *"This key should not be requested
for every notification since doing so can result in exceeding API call limits if
a large number of notification requests is received"*, recommending one hour.
The cache lives in the existing Redis client, and a Redis outage degrades to a
live fetch rather than failing the notification — the same rule the rest of this
codebase follows.
"""

from __future__ import annotations

import base64
import json
import uuid
from dataclasses import dataclass
from typing import Any, Final

import httpx
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis import RedisPurpose, get_redis
from app.integrations.ebay.exceptions import EbayKeyUnavailableError, EbayNotConfiguredError

logger = get_logger(__name__)

#: eBay's recommendation, verbatim: "one-hour is recommended".
PUBLIC_KEY_CACHE_TTL_SECONDS: Final = 3_600

#: Namespaced like every other cache key in this application, plus two things
#: a bare key id cannot carry.
#:
#: **The environment**, because sandbox and production are different estates
#: that can legitimately issue the same key id. Sharing a slot means a
#: production notification could be verified against a sandbox key, or refused
#: because of one — and the symptom would be an intermittent 412 that looks
#: like an eBay problem.
#:
#: **A schema version**, because the cached value is a serialised shape. When
#: that shape changes, entries written by the old code must not be read by the
#: new: bumping this retires them without a flush.
_CACHE_PREFIX: Final = "ebay:notification:public_key"
PUBLIC_KEY_CACHE_SCHEMA: Final = "v1"

#: The client-credentials scope named in the getPublicKey reference. It is the
#: same literal string in sandbox and production — it is a scope identifier,
#: not a URL that gets called.
_CLIENT_CREDENTIALS_SCOPE: Final = "https://api.ebay.com/oauth/api_scope"

# S105 flags the name, not the value: this is eBay's OAuth *path*, and it is
# a public constant from their documentation, not a credential.
_TOKEN_PATH: Final = "/identity/v1/oauth2/token"  # noqa: S105
_PUBLIC_KEY_PATH: Final = "/commerce/notification/v1/public_key/"

#: A key response is a few hundred bytes. Anything larger is not a key.
_MAX_RESPONSE_BYTES: Final = 64 * 1024

_CONNECT_TIMEOUT: Final = 5.0
_READ_TIMEOUT: Final = 10.0

#: Both calls are read-only (the token grant creates no eBay-side state that a
#: repeat would duplicate), so a bounded retry is safe. This mirrors the
#: Shopify GraphQL client's rule: retry idempotent reads, never a mutation.
_MAX_ATTEMPTS: Final = 3
_RETRYABLE_STATUSES: Final = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True, slots=True)
class NotificationPublicKey:
    """``getPublicKey``'s response: ``key``, ``algorithm``, ``digest``.

    All three are carried, and the verifier uses eBay's ``algorithm`` and
    ``digest`` rather than the ones in the attacker-supplied signature header.
    """

    key: str
    algorithm: str
    digest: str

    def to_json(self) -> str:
        return json.dumps({"key": self.key, "algorithm": self.algorithm, "digest": self.digest})

    @classmethod
    def from_payload(cls, payload: object) -> NotificationPublicKey:
        if not isinstance(payload, dict):
            raise EbayKeyUnavailableError("eBay returned a public key that was not an object.")
        key = payload.get("key")
        algorithm = payload.get("algorithm")
        digest = payload.get("digest")
        if not isinstance(key, str) or not key.strip():
            raise EbayKeyUnavailableError("eBay returned a public key with no key material.")
        if not isinstance(algorithm, str) or not algorithm.strip():
            raise EbayKeyUnavailableError("eBay returned a public key with no algorithm.")
        if not isinstance(digest, str) or not digest.strip():
            # Refused rather than defaulted. Guessing the digest would mean
            # verifying with an algorithm eBay did not name.
            raise EbayKeyUnavailableError("eBay returned a public key with no digest.")
        return cls(key=key, algorithm=algorithm, digest=digest)


class EbayPublicKeyClient:
    """Retrieves notification public keys, cached.

    ``transport`` exists so tests drive the real client against
    ``httpx.MockTransport``. Production passes nothing.
    """

    __slots__ = ("_transport",)

    def __init__(self, *, transport: httpx.AsyncClient | None = None) -> None:
        self._transport = transport

    async def get(self, key_id: uuid.UUID) -> NotificationPublicKey:
        cached = await self._read_cache(key_id)
        if cached is not None:
            logger.info("ebay_public_key_cache_hit", key_id=str(key_id))
            return cached

        logger.info("ebay_public_key_cache_miss", key_id=str(key_id))
        fetched = await self._fetch(key_id)
        await self._write_cache(key_id, fetched)
        return fetched

    # ------------------------------------------------------------------ cache
    @staticmethod
    def _cache_key(key_id: uuid.UUID) -> str:
        # `str(uuid)` is canonical lowercase, so two spellings of the same id
        # cannot occupy two cache slots. The environment and schema segments
        # are what stop two *different* keys occupying one.
        return (
            f"{_CACHE_PREFIX}:{PUBLIC_KEY_CACHE_SCHEMA}:{settings.ebay.environment.value}:{key_id}"
        )

    async def _read_cache(self, key_id: uuid.UUID) -> NotificationPublicKey | None:
        try:
            raw = await get_redis(RedisPurpose.CACHE).get(self._cache_key(key_id))
        except RedisError as exc:
            # Degrade, never fail: a cache outage becomes a slower verification,
            # not a rejected notification.
            logger.warning("ebay_public_key_cache_unavailable", error=str(exc))
            return None
        if not raw:
            return None
        try:
            return NotificationPublicKey.from_payload(json.loads(raw))
        except (json.JSONDecodeError, EbayKeyUnavailableError):
            # A poisoned or stale-format entry is discarded rather than trusted.
            logger.warning("ebay_public_key_cache_corrupt", key_id=str(key_id))
            return None

    async def _write_cache(self, key_id: uuid.UUID, key: NotificationPublicKey) -> None:
        try:
            await get_redis(RedisPurpose.CACHE).set(
                self._cache_key(key_id), key.to_json(), ex=PUBLIC_KEY_CACHE_TTL_SECONDS
            )
        except RedisError as exc:
            logger.warning("ebay_public_key_cache_write_failed", error=str(exc))

    # ------------------------------------------------------------------ fetch
    async def _fetch(self, key_id: uuid.UUID) -> NotificationPublicKey:
        token = await self._application_token()
        base = settings.ebay.notification_api_base
        # `str(key_id)` on a parsed UUID: the path segment is a canonical
        # 36-character hex-and-hyphen string and can be nothing else.
        url = f"{base}{_PUBLIC_KEY_PATH}{key_id}"
        payload = await self._request(
            "GET",
            url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            failure="public_key",
        )
        return NotificationPublicKey.from_payload(payload)

    async def _application_token(self) -> str:
        client_id = settings.ebay.client_id.strip()
        secret = settings.ebay.client_secret
        if not client_id or secret is None or not secret.get_secret_value().strip():
            raise EbayNotConfiguredError(
                "eBay application credentials are required to verify notifications."
            )
        basic = base64.b64encode(f"{client_id}:{secret.get_secret_value()}".encode()).decode(
            "ascii"
        )
        payload = await self._request(
            "POST",
            f"{settings.ebay.notification_api_base}{_TOKEN_PATH}",
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials", "scope": _CLIENT_CREDENTIALS_SCOPE},
            failure="oauth",
        )
        if not isinstance(payload, dict):
            raise EbayKeyUnavailableError("eBay returned a malformed token response.")
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise EbayKeyUnavailableError("eBay returned no access token.")
        return token

    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        data: dict[str, str] | None = None,
        failure: str,
    ) -> Any:
        """One bounded, retrying request.

        Retries only transient statuses and transport errors, and only because
        both calls here are reads. Nothing in this module creates provider-side
        state, so there is no non-idempotent operation to protect — the rule is
        stated rather than assumed so a later mutation cannot inherit it.
        """
        client = self._transport or httpx.AsyncClient(
            timeout=httpx.Timeout(_READ_TIMEOUT, connect=_CONNECT_TIMEOUT),
            follow_redirects=False,
        )
        owned = self._transport is None
        last: str = "unknown"
        try:
            for attempt in range(_MAX_ATTEMPTS):
                try:
                    response = await client.request(method, url, headers=headers, data=data)
                except httpx.HTTPError as exc:
                    last = type(exc).__name__
                    if attempt + 1 >= _MAX_ATTEMPTS:
                        break
                    continue
                if response.status_code in _RETRYABLE_STATUSES:
                    last = f"http_{response.status_code}"
                    if attempt + 1 >= _MAX_ATTEMPTS:
                        break
                    continue
                if response.status_code >= 400:
                    # Not retryable: 401 means the credentials are wrong and 404
                    # means the key id is unknown. Repeating either just burns
                    # the call quota eBay warned about.
                    last = f"http_{response.status_code}"
                    break
                if len(response.content) > _MAX_RESPONSE_BYTES:
                    last = "oversized_response"
                    break
                try:
                    return response.json()
                except ValueError:
                    last = "not_json"
                    break
        finally:
            if owned:
                await client.aclose()

        # The URL is not logged: it is fixed and known, and the key id is
        # already logged by the caller.
        logger.warning("ebay_key_service_unavailable", stage=failure, reason=last)
        raise EbayKeyUnavailableError(details={"stage": failure, "reason": last})


__all__ = [
    "PUBLIC_KEY_CACHE_TTL_SECONDS",
    "EbayPublicKeyClient",
    "NotificationPublicKey",
]
