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
from dataclasses import dataclass
from typing import Any, Final

from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

from app.core.config import settings
from app.core.logging import get_logger

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


class GoogleTokenVerifier:
    """Turns a browser-supplied credential into a verified identity, or nothing."""

    def __init__(self, *, client_id: str | None = None) -> None:
        self._client_id = client_id if client_id is not None else settings.google_oauth.client_id
        self._config = settings.google_oauth

    @property
    def configured(self) -> bool:
        return bool(self._client_id)

    def _verify_blocking(self, credential: str) -> dict[str, Any]:
        """Google's library, called on a worker thread.

        `verify_oauth2_token` is synchronous and fetches Google's certificates
        over the network. Running it inline would block the event loop for the
        duration of that fetch on every sign-in.
        """
        # `google-auth` ships no type stubs, so mypy sees an untyped call.
        # The suppression is on the call rather than the module so anything else
        # from that package still gets checked.
        return dict(
            google_id_token.verify_oauth2_token(  # type: ignore[no-untyped-call]
                credential,
                google_requests.Request(),
                self._client_id,
                clock_skew_in_seconds=self._config.clock_skew_seconds,
            )
        )

    async def verify(self, credential: str, *, expected_nonce: str | None = None) -> GoogleIdentity:
        """Verify a credential and return what it establishes.

        Raises `GoogleTokenError` for every failure, with a reason that is
        logged but never returned to the caller.
        """
        if not self.configured:
            raise GoogleTokenError("Google sign-in is not configured on this server.")
        if not credential or not credential.strip():
            raise GoogleTokenError("empty credential")

        try:
            # Signature, `aud` and `exp` are checked here, by Google's code.
            claims = await asyncio.to_thread(self._verify_blocking, credential)
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
        if expected_nonce is not None and nonce_value != expected_nonce:
            # A credential minted for a different sign-in attempt.
            logger.warning("google_credential_nonce_mismatch")
            raise GoogleTokenError("nonce mismatch")

        return GoogleIdentity(
            subject=subject,
            email=email,
            email_verified=True,
            name=(str(claims["name"]) if claims.get("name") else None),
            nonce=nonce_value,
        )
