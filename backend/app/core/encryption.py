"""Encryption for credentials held on behalf of a tenant.

This module exists for one class of data: **secrets belonging to someone else.**
A tenant's AliExpress app secret and OAuth tokens are their credentials, not
ours. A database dump must not yield working access to a customer's supplier
account.

**Encryption, not hashing.** Passwords are hashed because they only ever need
verifying (see ``app.core.password``). These values must be *recovered* to sign
outbound API requests, so they are encrypted with a key the application holds.

**Fernet**, from ``cryptography``: AES-128-CBC with an HMAC-SHA256 authentication
tag and a random IV per message. Authenticated, so tampering is detected rather
than producing garbage plaintext; randomised, so encrypting the same token twice
yields different ciphertext and an observer cannot tell which tenants share a
credential.

**Key rotation** is supported through ``MultiFernet``. Keys are configured
newest-first: encryption always uses the first, decryption tries each in turn.
Rotating therefore means prepending a new key, re-encrypting stored rows in the
background, and only then dropping the old one — with no window in which
existing data cannot be read.

Nothing here logs a plaintext value, a ciphertext, or a key.
"""

from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import settings
from app.core.exceptions import AppError
from app.core.logging import get_logger

logger = get_logger(__name__)


class EncryptionNotConfiguredError(AppError):
    """Raised when credential encryption is used with no key configured.

    A deployment fault, not a client error. Storing a customer's supplier
    credentials in plaintext because a key was missing is far worse than
    refusing the operation, so this fails the request loudly.
    """

    code = "encryption_not_configured"
    status_code = 500
    message = "Credential encryption is not configured on this server."


class DecryptionError(AppError):
    """Raised when stored ciphertext cannot be decrypted.

    Means the value was written with a key no longer configured, or the row was
    tampered with. Both need investigation, and neither should be reported to
    the client in any detail.
    """

    code = "decryption_failed"
    status_code = 500
    message = "A stored credential could not be read."


@lru_cache
def _cipher() -> MultiFernet:
    """Build the cipher from configured keys.

    Cached: constructing Fernet instances parses and validates each key, and
    this runs on every credential read.

    Tests that change the configured keys must call ``_cipher.cache_clear()``,
    which :func:`reset_cipher_cache` does.
    """
    raw_keys = settings.security.encryption_keys
    if not raw_keys:
        raise EncryptionNotConfiguredError()

    try:
        ciphers = [Fernet(key.get_secret_value().encode("utf-8")) for key in raw_keys]
    except (ValueError, TypeError) as exc:
        # The exception message from `cryptography` can echo the malformed key,
        # so it is deliberately not chained into anything user-visible.
        logger.error("encryption_key_malformed", key_count=len(raw_keys))
        raise EncryptionNotConfiguredError("An encryption key is not a valid Fernet key.") from exc

    return MultiFernet(ciphers)


def reset_cipher_cache() -> None:
    """Discard the cached cipher. For tests that reconfigure keys."""
    _cipher.cache_clear()


def is_encryption_configured() -> bool:
    """Whether credential encryption can be used.

    Lets a health check or a startup log report the problem before a customer
    hits it mid-flow.
    """
    return bool(settings.security.encryption_keys)


def encrypt(plaintext: str) -> str:
    """Encrypt a value for storage.

    Returns urlsafe base64 text, so the column is a plain ``String`` and the
    value survives any transport that handles text.
    """
    if not plaintext:
        # Distinguishing "empty" from "encrypted empty" costs nothing and avoids
        # storing ciphertext that decrypts to nothing useful.
        return ""
    return _cipher().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(ciphertext: str) -> str:
    """Decrypt a stored value.

    Raises :class:`DecryptionError` if no configured key can read it. Note this
    also fires when the ciphertext has been altered — Fernet authenticates, so
    tampering is detected rather than silently yielding wrong plaintext.
    """
    if not ciphertext:
        return ""

    try:
        return _cipher().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        # No ciphertext in the log line. It is customer credential material even
        # when unreadable.
        logger.error("credential_decryption_failed")
        raise DecryptionError() from exc


def rotate(ciphertext: str) -> str:
    """Re-encrypt an existing value under the newest key.

    The operation a rotation job performs on each stored row. ``MultiFernet``
    decrypts with whichever key still works and re-encrypts with the first,
    so this is safe to run repeatedly and is a no-op once a value is current.
    """
    if not ciphertext:
        return ""

    try:
        return _cipher().rotate(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        logger.error("credential_rotation_failed")
        raise DecryptionError() from exc


def mask(value: str, *, visible: int = 4) -> str:
    """Render a credential for display without revealing it.

    For UI and support: enough to confirm *which* credential is stored, not
    enough to use it. Values too short to mask safely become a fixed string
    rather than leaking most of themselves.

    >>> mask("1234567890abcdef")
    '••••cdef'
    >>> mask("short")
    '••••'
    """
    if not value or len(value) <= visible:
        return "•" * 4
    return f"{'•' * 4}{value[-visible:]}"


__all__ = [
    "DecryptionError",
    "EncryptionNotConfiguredError",
    "decrypt",
    "encrypt",
    "is_encryption_configured",
    "mask",
    "reset_cipher_cache",
    "rotate",
]
