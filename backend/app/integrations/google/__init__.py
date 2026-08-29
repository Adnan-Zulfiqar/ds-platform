"""Google Identity Services integration (AUTH-G1).

Sign-in only: the browser obtains a signed ID token from Google's official
button and this package verifies it. No authorization code, no client secret,
no Google access or refresh token is ever requested or stored.
"""

from __future__ import annotations

from app.integrations.google.nonce import GoogleIntent, GoogleNonceStore, NonceRecord
from app.integrations.google.verification import (
    GOOGLE_SCOPES,
    GoogleIdentity,
    GoogleTokenError,
    GoogleTokenVerifier,
)

__all__ = [
    "GOOGLE_SCOPES",
    "GoogleIdentity",
    "GoogleIntent",
    "GoogleNonceStore",
    "GoogleTokenError",
    "GoogleTokenVerifier",
    "NonceRecord",
]
