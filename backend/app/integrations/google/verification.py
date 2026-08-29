"""Verification of Google Identity Services ID tokens.

The browser receives a signed credential from Google and posts it here. Nothing
the browser says about that credential is believed — only what survives
verification against Google's published signing keys.

**Google's own library does the cryptography.** `google.oauth2.id_token`
handles signature verification, key rotation and the `aud`/`exp` checks. Those
are precisely the parts that are dangerous to reimplement: a hand-rolled JWT
check that forgets to pin the algorithm accepts `alg: none`, and one that caches
keys badly starts rejecting valid tokens the day Google rotates.

What the library does **not** do is decide whether *this* application should
accept the token, so three checks are made explicitly on top:

* **`iss`** — the library validates the signature and audience but leaves the
  issuer to the caller.
* **`email_verified`** — Google will assert an address it has not verified.
  Treating that as proof of ownership would let somebody claim an address they
  do not control and, worse, collide with a real user.
* **`nonce`** — a one-time value this server issued, so a credential captured
  elsewhere cannot be replayed here.

There is **no client secret** anywhere in this module. This flow exchanges no
authorization code; the credential is self-contained and signed. A secret would
be a stored credential the flow never uses.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Final

import requests as requests_lib
from google.auth import jwt as google_jwt
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.google.nonce import GoogleNonceStore

logger = get_logger(__name__)

__all__ = [
    "GoogleIdentity",
    "GoogleTokenError",
    "GoogleTokenVerifier",
]

#: Only these scopes are ever requested. Anything wider — Gmail, Drive, contacts
#: — would be asking for access this product has no use for, and users are right
#: to refuse an app that does.
GOOGLE_SCOPES: Final[tuple[str, ...]] = ("openid", "email", "profile")


class GoogleTokenError(Exception):
    """The credential is not acceptable.

    Deliberately one exception with a short reason rather than a family of them.
    The caller returns a single generic failure to the browser: telling an
    attacker *which* check failed turns this into an oracle for crafting a token
    that passes the next one.
    """


@dataclass(frozen=True, slots=True)
class GoogleIdentity:
    """What a verified credential actually establishes."""

    subject: str
    email: str
    email_verified: bool
    name: str | None
    nonce: str | None

    def __str__(self) -> str:  # pragma: no cover - diagnostic only
        # Reaches log lines. Neither the subject nor the address belongs there.
        return "<GoogleIdentity verified>"


class _BoundedRequest(google_requests.Request):
    """Google's transport, with a timeout it cannot exceed.

    `asyncio.to_thread` moves the fetch off the event loop but does **not**
    bound it: the worker thread blocks for as long as the socket does, and a
    slow or black-holed Google endpoint accumulates one stuck thread per
    sign-in until the executor is exhausted and every other `to_thread` call in
    the process stalls behind it. The review was right that the thread alone is
    not a control.

    `requests` applies `(connect, read)` separately, so a connection that opens
    and then goes quiet is bounded too.
    """

    def __init__(self, timeout: tuple[float, float]) -> None:
        session = requests_lib.Session()
        super().__init__(session=session)
        self._timeout = timeout

    # Signature mirrors the supertype exactly so the override is sound.
    def __call__(
        self,
        url: Any,
        method: Any = "GET",
        body: Any = None,
        headers: Any = None,
        timeout: Any = None,
        **kwargs: Any,
    ) -> Any:
        # Only when the caller did not ask for something specific, so a future
        # call that wants its own bound is not silently overridden.
        effective = timeout if timeout is not None else self._timeout
        # `google-auth` ships no stubs, so every call into it reads as untyped.
        return super().__call__(  # type: ignore[no-untyped-call]
            url, method=method, body=body, headers=headers, timeout=effective, **kwargs
        )


class _CertificateCache:
    """Google's signing keys, cached with a bounded lifetime.

    Without this every sign-in fetches the certificate set again — an outbound
    request on the critical path, and a hard dependency on Google being
    reachable at that instant.

    The single-flight lock matters as much as the cache: on a cold start or
    immediately after expiry, a burst of sign-ins would otherwise each miss and
    each fetch. One caller refreshes; the rest wait for it.

    The TTL is a fixed ceiling rather than something parsed out of a
    `Cache-Control` header, because trusting a remote header to decide how long
    to trust remote keys is a control the remote end sets.
    """

    def __init__(self, ttl_seconds: int) -> None:
        self._ttl = ttl_seconds
        self._certs: dict[str, Any] | None = None
        self._fetched_at = 0.0
        self._lock = asyncio.Lock()

    def _fresh(self) -> bool:
        return self._certs is not None and (time.monotonic() - self._fetched_at) < self._ttl

    async def get(self, fetch: Any) -> dict[str, Any]:
        if self._fresh():
            assert self._certs is not None
            return self._certs

        async with self._lock:
            # Re-check: another caller may have refreshed while we queued.
            if self._fresh():
                assert self._certs is not None
                return self._certs
            certs: dict[str, Any] = await asyncio.to_thread(fetch)
            self._certs = certs
            self._fetched_at = time.monotonic()
            return certs

    def clear(self) -> None:
        self._certs = None
        self._fetched_at = 0.0


#: Process-wide. The keys are public and identical for every request.
_CERT_CACHE = _CertificateCache(ttl_seconds=3600)


def reset_certificate_cache() -> None:
    """Drop the cached keys. For tests and for forced rotation."""
    _CERT_CACHE.clear()


class GoogleTokenVerifier:
    """Turns a browser-supplied credential into a verified identity, or nothing."""

    def __init__(self, *, client_id: str | None = None) -> None:
        self._client_id = client_id if client_id is not None else settings.google_oauth.client_id
        self._config = settings.google_oauth

    @property
    def configured(self) -> bool:
        return bool(self._client_id)

    def _fetch_certs(self) -> dict[str, Any]:
        """Retrieve Google's signing keys through the bounded transport."""
        request = _BoundedRequest(
            (self._config.connect_timeout_seconds, self._config.read_timeout_seconds)
        )
        return dict(
            google_id_token._fetch_certs(  # type: ignore[no-untyped-call]
                request, google_id_token._GOOGLE_OAUTH2_CERTS_URL
            )
        )

    def _verify_blocking(self, credential: str, certs: dict[str, Any]) -> dict[str, Any]:
        """Verify against already-fetched keys, on a worker thread.

        `google.auth.jwt.decode` is what `verify_oauth2_token` calls once it has
        the certificates — it checks the signature, the audience and the expiry.
        Calling it directly is what lets the fetch be cached and bounded
        separately; `verify_token` takes a `certs_url` and always fetches, so
        every sign-in would hit the network.

        Signature verification is short CPU work, but still synchronous, so it
        stays off the event loop.

        `google-auth` ships no type stubs, hence the suppression here rather
        than on the whole module.
        """
        decoded: dict[str, Any] = dict(
            google_jwt.decode(  # type: ignore[no-untyped-call]
                credential,
                certs=certs,
                audience=self._client_id,
                clock_skew_in_seconds=self._config.clock_skew_seconds,
            )
        )
        return decoded

    async def verify(self, credential: str, *, expected_nonce: str) -> GoogleIdentity:
        """Verify a credential and return what it establishes.

        `expected_nonce` is **required**. The previous signature defaulted it to
        `None` and skipped the check when absent, which meant a caller could
        decline replay protection by simply not sending one. A defence a client
        can opt out of is not a defence.

        Raises `GoogleTokenError` for every failure, with a reason that is
        logged but never returned to the caller.
        """
        if not expected_nonce or not expected_nonce.strip():
            raise GoogleTokenError("a nonce is required")
        if not self.configured:
            raise GoogleTokenError("Google sign-in is not configured on this server.")
        if not credential or not credential.strip():
            raise GoogleTokenError("empty credential")

        try:
            certs = await _CERT_CACHE.get(self._fetch_certs)
        except Exception as exc:
            # Fail closed. A key fetch that timed out is not a passed check.
            logger.warning("google_certificates_unavailable", reason=type(exc).__name__)
            raise GoogleTokenError("verification unavailable") from exc

        try:
            # Signature, `aud` and `exp` are checked here, by Google's code.
            claims = await asyncio.to_thread(self._verify_blocking, credential, certs)
        except ValueError as exc:
            # The library raises ValueError for every rejection: bad signature,
            # wrong audience, expired, malformed.
            logger.warning("google_credential_rejected", reason=type(exc).__name__)
            raise GoogleTokenError("credential failed verification") from exc
        except Exception as exc:
            logger.warning("google_verification_unavailable", reason=type(exc).__name__)
            raise GoogleTokenError("verification unavailable") from exc

        issuer = str(claims.get("iss", ""))
        if issuer not in self._config.allowed_issuers:
            # The library does not check this, and a token from elsewhere that
            # happened to validate would be a complete bypass.
            logger.warning("google_credential_bad_issuer")
            raise GoogleTokenError("unexpected issuer")

        # Belt and braces: the library was given the audience, but an explicit
        # check costs nothing and survives a future refactor that forgets to
        # pass it.
        audience = claims.get("aud")
        if audience != self._client_id:
            logger.warning("google_credential_bad_audience")
            raise GoogleTokenError("unexpected audience")

        subject = str(claims.get("sub") or "")
        if not subject:
            raise GoogleTokenError("credential carries no subject")

        email = str(claims.get("email") or "").strip().lower()
        if not email:
            raise GoogleTokenError("credential carries no email")

        # Google sends this as a bool, and has historically sent the string
        # "true" as well. Anything else is not a verification.
        raw_verified = claims.get("email_verified")
        email_verified = raw_verified is True or str(raw_verified).lower() == "true"
        if not email_verified:
            logger.warning("google_credential_email_unverified")
            raise GoogleTokenError("email is not verified with Google")

        nonce = claims.get("nonce")
        nonce_value = str(nonce) if nonce is not None else None
        # `compare_digest`, not `==`: these are secrets, and a short-circuiting
        # comparison leaks the prefix through timing.
        if not GoogleNonceStore.matches(nonce_value, expected_nonce):
            # Absent, blank, malformed or minted for a different attempt.
            logger.warning("google_credential_nonce_mismatch")
            raise GoogleTokenError("nonce mismatch")

        return GoogleIdentity(
            subject=subject,
            email=email,
            email_verified=True,
            name=(str(claims["name"]) if claims.get("name") else None),
            nonce=nonce_value,
        )
