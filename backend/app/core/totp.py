"""Time-based one-time passwords (RFC 6238), for platform administrators (E5).

Implemented here rather than with a new dependency: the algorithm is a dozen
lines over ``hmac``, the RFC publishes test vectors (``test_totp.py`` checks
them), and every added package is one more thing the dependency lock and the
supply-chain review have to carry.

Parameters are the ones every authenticator app assumes: SHA-1, 30-second
steps, 6 digits. Verification accepts the current step and one either side,
for clock drift, and returns the step that matched so the caller can refuse a
code that was already used (RFC 6238 §5.2).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from typing import Final
from urllib.parse import quote

STEP_SECONDS: Final = 30
DIGITS: Final = 6
#: Steps accepted either side of now: ±30 s of clock drift.
DRIFT_STEPS: Final = 1


def generate_secret() -> str:
    """160 random bits, base32 without padding, as authenticator apps expect."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _key(secret: str) -> bytes:
    padded = secret.strip().upper() + "=" * (-len(secret.strip()) % 8)
    return base64.b32decode(padded, casefold=True)


def code_at(secret: str, step: int, *, digits: int = DIGITS, digest: str = "sha1") -> str:
    """The code for one time step (RFC 4226 HOTP with the step as counter)."""
    mac = hmac.new(_key(secret), struct.pack(">Q", step), getattr(hashlib, digest)).digest()
    offset = mac[-1] & 0x0F
    value = struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10**digits)).zfill(digits)


def current_step(now: float | None = None) -> int:
    return int((time.time() if now is None else now) // STEP_SECONDS)


def verify(secret: str, code: str, *, now: float | None = None) -> int | None:
    """The matching step, or ``None``. Constant-time per comparison."""
    candidate = code.strip().replace(" ", "")
    if len(candidate) != DIGITS or not candidate.isdigit():
        return None
    step = current_step(now)
    for offset in range(-DRIFT_STEPS, DRIFT_STEPS + 1):
        if hmac.compare_digest(code_at(secret, step + offset), candidate):
            return step + offset
    return None


def provisioning_uri(secret: str, *, account: str, issuer: str = "DropPilot Platform") -> str:
    """``otpauth://`` URI an authenticator app imports (as text or a QR code)."""
    label = quote(f"{issuer}:{account}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}&digits={DIGITS}&period={STEP_SECONDS}"


__all__ = ["code_at", "current_step", "generate_secret", "provisioning_uri", "verify"]
