"""``X-EBAY-SIGNATURE`` parsing and verification.

**Every fact in this module was read from official sources, not recalled**, and
each is verified by a test against eBay's own published vector. The format is
not documented as prose anywhere in eBay's guides — the guides point at the
Event Notification SDKs — so the SDK *is* the specification here, and
``tests/unit/test_ebay_c0_signature.py`` pins the vector it came from.

Sources, read 23 August 2026:

* `eBay/event-notification-nodejs-sdk` — ``lib/validator.js`` and
  ``lib/constants.js``: base64-decode the header, parse it as JSON, take
  ``kid``, fetch the key, verify with OpenSSL algorithm ``ssl3-sha1`` against a
  PEM public key, signature supplied as base64.
* the same repository's ``test/test.json`` — the official vector reproduced in
  the test module.
* the Marketplace Account Deletion guide's AsyncAPI contract, which describes
  ``X-EBAY-SIGNATURE`` as an *"ECC message signature"*.

What that resolves to, confirmed by actually verifying the official vector in
Python before this module was written:

===================  =========================================================
header               base64 of JSON ``{alg, kid, signature, digest}``
``kid``              UUID key id for ``GET /commerce/notification/v1/public_key/{id}``
``signature``        base64 of a **DER**-encoded ECDSA signature
key                  PEM ``SubjectPublicKeyInfo``, **P-256**, delivered without
                     newlines around the armour
digest               SHA-1 — ``ssl3-sha1`` on an EC key is ECDSA-with-SHA-1
signed bytes         the notification body exactly as received
===================  =========================================================

SHA-1 is weak for collision resistance and is eBay's choice, not one this
codebase would make. It is safe in this position: an attacker would need a
second preimage of a body eBay signed, not a colliding pair they authored, and
the signing key is eBay's. It is called out here so nobody later "modernises"
it to SHA-256 and silently rejects every real notification.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import uuid
from dataclasses import dataclass
from typing import Final

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePublicKey

from app.core.logging import get_logger
from app.integrations.ebay.exceptions import EbaySignatureError

logger = get_logger(__name__)

#: The header eBay sends. Compared case-insensitively — HTTP header names are,
#: and eBay's own SDK constant is lowercase while its documentation is upper.
SIGNATURE_HEADER: Final = "x-ebay-signature"

#: A base64 header far larger than any real one is an attack, not a delivery.
#: eBay's own vector is ~230 bytes; this leaves two orders of magnitude of room
#: while still refusing a megabyte of base64 before any parsing happens.
MAX_SIGNATURE_HEADER_BYTES: Final = 8_192

#: Digests eBay is documented to use, mapped to the implementation. Anything
#: else is refused rather than guessed at: silently substituting a digest would
#: mean verifying something other than what eBay signed.
_SUPPORTED_DIGESTS: Final[dict[str, hashes.HashAlgorithm]] = {
    "SHA1": hashes.SHA1(),  # noqa: S303 - eBay's documented choice; see module docstring
    "SHA-1": hashes.SHA1(),  # noqa: S303 - same, tolerating the hyphenated spelling
    "SHA256": hashes.SHA256(),
    "SHA-256": hashes.SHA256(),
}

#: eBay's SDK sends ``"ecdsa"``; the getPublicKey response says ``"ECDSA"``.
_SUPPORTED_ALGORITHMS: Final = frozenset({"ECDSA"})

_PEM_START: Final = "-----BEGIN PUBLIC KEY-----"
_PEM_END: Final = "-----END PUBLIC KEY-----"
_BASE64_BODY: Final = re.compile(r"[A-Za-z0-9+/=\s]+")


@dataclass(frozen=True, slots=True)
class SignatureHeader:
    """The decoded ``X-EBAY-SIGNATURE``.

    ``key_id`` is the only value from this header that is ever used to build a
    URL, and it is a validated UUID by construction — see ``_parse_key_id``.
    That is what makes the public-key fetch structurally incapable of being
    pointed anywhere but eBay.
    """

    key_id: uuid.UUID
    signature: bytes
    algorithm: str
    digest: str


def parse_signature_header(raw: str | None) -> SignatureHeader:
    """Decode and validate the header, refusing anything ambiguous.

    Every failure raises the same typed error with the same merchant-facing
    message. The *reason* is logged as a category, never as the header content:
    a rejected signature is attacker-controlled data and echoing it back is how
    a log becomes an injection surface.
    """
    if not raw or not raw.strip():
        raise _reject("missing")
    if len(raw.encode("utf-8", errors="ignore")) > MAX_SIGNATURE_HEADER_BYTES:
        raise _reject("oversized_header")

    try:
        decoded = base64.b64decode(raw.strip(), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise _reject("not_base64") from exc

    try:
        payload = json.loads(decoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _reject("not_json") from exc
    if not isinstance(payload, dict):
        raise _reject("not_an_object")

    key_id = _parse_key_id(payload.get("kid"))

    signature_b64 = payload.get("signature")
    if not isinstance(signature_b64, str) or not signature_b64:
        raise _reject("missing_signature")
    try:
        signature = base64.b64decode(signature_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise _reject("signature_not_base64") from exc
    if not signature:
        raise _reject("empty_signature")

    algorithm = str(payload.get("alg") or "").strip().upper()
    digest = str(payload.get("digest") or "").strip().upper()
    return SignatureHeader(
        key_id=key_id,
        signature=signature,
        algorithm=algorithm,
        digest=digest,
    )


def _parse_key_id(value: object) -> uuid.UUID:
    """The single choke point that makes the key fetch SSRF-proof.

    eBay's key ids are UUIDs. Parsing to ``uuid.UUID`` and reformatting from the
    parsed object means nothing containing ``/``, ``..``, a scheme or a host can
    survive into a URL path — not because a filter rejected it, but because a
    UUID cannot represent it.
    """
    if not isinstance(value, str) or not value.strip():
        raise _reject("missing_key_id")
    try:
        return uuid.UUID(value.strip())
    except ValueError as exc:
        raise _reject("key_id_not_a_uuid") from exc


def format_public_key_pem(key: str) -> str:
    """Reflow eBay's PEM, which arrives without newlines around the armour.

    The SDK does exactly this — replace the BEGIN marker with itself plus a
    newline, and the END marker with a newline plus itself. Nothing else is
    touched, because the base64 body is what the signature was made over and
    "helpfully" re-wrapping it risks corrupting a key that would otherwise load.
    """
    candidate = key.strip()
    if _PEM_START not in candidate or _PEM_END not in candidate:
        raise _reject("key_not_pem")
    body = candidate.split(_PEM_START, 1)[1].split(_PEM_END, 1)[0]
    if not _BASE64_BODY.fullmatch(body):
        raise _reject("key_body_not_base64")
    return f"{_PEM_START}\n{body.strip()}\n{_PEM_END}\n"


def load_public_key(key: str) -> EllipticCurvePublicKey:
    """Load eBay's public key, refusing anything that is not an EC key.

    The type check is not decoration. ``load_pem_public_key`` happily returns an
    RSA key, and ``verify`` on an RSA key takes a padding argument this code
    does not supply — so without this the failure would be a ``TypeError`` deep
    inside verification rather than a clean rejection.
    """
    try:
        loaded = serialization.load_pem_public_key(format_public_key_pem(key).encode("ascii"))
    except (ValueError, UnsupportedAlgorithm) as exc:
        raise _reject("key_unloadable") from exc
    if not isinstance(loaded, EllipticCurvePublicKey):
        raise _reject("key_not_elliptic_curve")
    return loaded


def verify_notification_signature(
    *,
    raw_body: bytes,
    header: SignatureHeader,
    public_key_pem: str,
    key_algorithm: str,
    key_digest: str,
) -> None:
    """Verify the signature over the **exact bytes eBay sent**.

    Deliberately not over a re-serialisation of the parsed JSON. eBay's Node SDK
    verifies ``JSON.stringify(message)``, which is only correct while a
    round-trip through that parser reproduces the original bytes exactly — true
    today for eBay's compact output, and untrue the moment a key order, a space
    or a unicode escape differs. Verifying the received bytes has no such
    dependency, and it is what the Java and PHP SDKs do.

    ``key_algorithm``/``key_digest`` come from ``getPublicKey``, not from the
    attacker-supplied header, so a forged header cannot downgrade the digest.
    The header's own values are cross-checked and a disagreement is a rejection.
    """
    algorithm = (key_algorithm or "").strip().upper()
    if algorithm not in _SUPPORTED_ALGORITHMS:
        raise _reject("unsupported_algorithm")

    digest_name = (key_digest or "").strip().upper()
    hash_algorithm = _SUPPORTED_DIGESTS.get(digest_name)
    if hash_algorithm is None:
        raise _reject("unsupported_digest")
    if header.digest and header.digest not in _SUPPORTED_DIGESTS:
        raise _reject("unsupported_header_digest")
    if header.digest and _SUPPORTED_DIGESTS[header.digest].name != hash_algorithm.name:
        # The header claims one digest and eBay's key metadata says another.
        # Trusting either would mean choosing which of two disagreeing sources
        # to believe about how to check a signature.
        raise _reject("digest_mismatch")

    key = load_public_key(public_key_pem)
    try:
        key.verify(header.signature, raw_body, ec.ECDSA(hash_algorithm))
    except InvalidSignature as exc:
        raise _reject("signature_mismatch") from exc
    except (ValueError, UnsupportedAlgorithm) as exc:
        raise _reject("verification_error") from exc


def _reject(reason: str) -> EbaySignatureError:
    """One rejection path, one log line, no attacker-controlled content in it."""
    logger.warning("ebay_notification_signature_rejected", reason=reason)
    return EbaySignatureError(details={"reason": reason})


__all__ = [
    "MAX_SIGNATURE_HEADER_BYTES",
    "SIGNATURE_HEADER",
    "SignatureHeader",
    "format_public_key_pem",
    "load_public_key",
    "parse_signature_header",
    "verify_notification_signature",
]
