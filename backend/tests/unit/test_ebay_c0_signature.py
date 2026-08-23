"""EBAY-C0 — ``X-EBAY-SIGNATURE`` verification against eBay's official vector.

The vector below is copied verbatim from eBay's own Event Notification SDK,
`eBay/event-notification-nodejs-sdk`, file ``test/test.json``, read on
23 August 2026. That repository is what eBay's Marketplace Account Deletion
guide points developers at for signature verification, so it is the closest
thing to a published specification for this format.

Pinning it here is the point. A verifier can be made to pass against fixtures it
generated itself and still reject every real notification; the only way to know
the format is right is to verify something eBay actually signed. The private key
does not exist in this repository and never will — these bytes were produced by
eBay.

Nothing here is a credential. The public key is public by definition, and the
identifiers in the sample payload are eBay's own test values.
"""

from __future__ import annotations

import base64
import json
import uuid
from typing import Any

import pytest

from app.integrations.ebay.exceptions import EbaySignatureError
from app.integrations.ebay.signature import (
    MAX_SIGNATURE_HEADER_BYTES,
    parse_signature_header,
    verify_notification_signature,
)

pytestmark = pytest.mark.unit

# --------------------------------------------------------- the official vector
OFFICIAL_SIGNATURE_HEADER = (
    "eyJhbGciOiJlY2RzYSIsImtpZCI6Ijk5MzYyNjFhLTdkN2ItNDYyMS1hMGYxLTk2Y2NiNDI4YWY0OSIsInNpZ25h"
    "dHVyZSI6Ik1FWUNJUUNmeGZJV3V4bVdjSUJRSjljNS9YN2lHREpxczJSQ0dzQkVhQWppbnlycmZBSWhBSVY2d0dj"
    "VGlCdVY1S0pVaWYyaG9reXJMK1E5c3NIa2FkK214Mm5FRTI1dyIsImRpZ2VzdCI6IlNIQTEifQ=="
)

OFFICIAL_PUBLIC_KEY = (
    "-----BEGIN PUBLIC KEY-----"
    "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEZhhxXKtR+TOvtDbgTPCkSof02qgBB7Is"
    "YOyf76ilExJ/upAa/vKIKheOoCyOpcLmi4t0b4uepb7LLjmMr90FUg=="
    "-----END PUBLIC KEY-----"
)
OFFICIAL_KEY_ALGORITHM = "ECDSA"
OFFICIAL_KEY_DIGEST = "SHA1"
OFFICIAL_KEY_ID = uuid.UUID("9936261a-7d7b-4621-a0f1-96ccb428af49")

#: The signed message. eBay's SDK vector stores it as parsed JSON; the bytes
#: that were signed are its compact serialisation, which is what eBay puts on
#: the wire. Verified: this exact byte sequence validates against the official
#: signature above.
OFFICIAL_MESSAGE: dict[str, Any] = {
    "metadata": {
        "topic": "MARKETPLACE_ACCOUNT_DELETION",
        "schemaVersion": "1.0",
        "deprecated": False,
    },
    "notification": {
        "notificationId": (
            "49feeaeb-4982-42d9-a377-9645b8479411_33f7e043-fed8-442b-9d44-791923bd9a6d"
        ),
        "eventDate": "2021-03-19T20:43:59.462Z",
        "publishDate": "2021-03-19T20:43:59.679Z",
        "publishAttemptCount": 1,
        "data": {
            "username": "test_user",
            "userId": "ma8vp1jySJC",
            "eiasToken": "nY+sHZ2PrBmdj6wVnY+sEZ2PrA2dj6wJnY+gAZGEpwmdj6x9nY+seQ==",
        },
    },
}


def official_body() -> bytes:
    """The exact bytes eBay signed."""
    return json.dumps(OFFICIAL_MESSAGE, separators=(",", ":")).encode("utf-8")


def signature_header(**overrides: Any) -> str:
    payload = json.loads(base64.b64decode(OFFICIAL_SIGNATURE_HEADER))
    payload.update(overrides)
    return base64.b64encode(json.dumps(payload).encode()).decode("ascii")


def verify(
    *,
    body: bytes | None = None,
    header: str | None = None,
    algorithm: str = OFFICIAL_KEY_ALGORITHM,
    digest: str = OFFICIAL_KEY_DIGEST,
    key: str = OFFICIAL_PUBLIC_KEY,
) -> None:
    verify_notification_signature(
        raw_body=official_body() if body is None else body,
        header=parse_signature_header(header or OFFICIAL_SIGNATURE_HEADER),
        public_key_pem=key,
        key_algorithm=algorithm,
        key_digest=digest,
    )


# --------------------------------------------------------------------- header
class TestSignatureHeader:
    def test_the_official_header_decodes_to_its_documented_fields(self) -> None:
        parsed = parse_signature_header(OFFICIAL_SIGNATURE_HEADER)
        assert parsed.key_id == OFFICIAL_KEY_ID
        assert parsed.digest == "SHA1"
        assert parsed.algorithm == "ECDSA"
        assert parsed.signature

    @pytest.mark.parametrize("raw", [None, "", "   "])
    def test_a_missing_header_is_refused(self, raw: str | None) -> None:
        with pytest.raises(EbaySignatureError):
            parse_signature_header(raw)

    def test_a_non_base64_header_is_refused(self) -> None:
        with pytest.raises(EbaySignatureError):
            parse_signature_header("not base64 at all !!!")

    def test_a_header_that_is_not_json_is_refused(self) -> None:
        with pytest.raises(EbaySignatureError):
            parse_signature_header(base64.b64encode(b"plain text").decode())

    def test_an_oversized_header_is_refused_before_parsing(self) -> None:
        with pytest.raises(EbaySignatureError):
            parse_signature_header("A" * (MAX_SIGNATURE_HEADER_BYTES + 1))

    @pytest.mark.parametrize(
        "kid",
        [
            "",
            "not-a-uuid",
            "../../etc/passwd",
            "9936261a-7d7b-4621-a0f1-96ccb428af49/../../evil",
            "https://attacker.test/key",
            "9936261a_7d7b_4621_a0f1_96ccb428af49",
        ],
    )
    def test_a_key_id_that_is_not_a_uuid_is_refused(self, kid: str) -> None:
        """The SSRF and traversal guard, at its only choke point.

        A ``uuid.UUID`` cannot represent a slash, a scheme or a host, so a key
        id that survives parsing cannot steer the fetch anywhere but eBay.
        """
        with pytest.raises(EbaySignatureError):
            parse_signature_header(signature_header(kid=kid))

    def test_a_missing_or_unusable_signature_is_refused(self) -> None:
        for value in ("", None, "not base64 !!"):
            with pytest.raises(EbaySignatureError):
                parse_signature_header(signature_header(signature=value))


# ---------------------------------------------------------------- verification
class TestOfficialVector:
    def test_the_official_signature_verifies(self) -> None:
        """The one test that proves the format was read, not guessed."""
        verify()

    def test_an_altered_body_fails(self) -> None:
        tampered = official_body().replace(b"test_user", b"evil_user")
        assert tampered != official_body()
        with pytest.raises(EbaySignatureError):
            verify(body=tampered)

    def test_a_single_flipped_byte_fails(self) -> None:
        body = bytearray(official_body())
        body[10] ^= 0x01
        with pytest.raises(EbaySignatureError):
            verify(body=bytes(body))

    def test_an_altered_signature_fails(self) -> None:
        original = json.loads(base64.b64decode(OFFICIAL_SIGNATURE_HEADER))["signature"]
        swapped = base64.b64decode(original)
        mutated = bytearray(swapped)
        mutated[-1] ^= 0xFF
        with pytest.raises(EbaySignatureError):
            verify(header=signature_header(signature=base64.b64encode(bytes(mutated)).decode()))

    def test_a_signature_from_a_different_message_fails(self) -> None:
        """eBay's own SIGNATURE_MISMATCH vector, from the same SDK file."""
        mismatched = (
            "MEUCIQCGcSnmAkTfr+ZZ2Vg0dJ4/o5hRnq1cvFOEqIVJgHOxZAIgQqTBhFLXaLp1EQIqEUJz"
            "eqDNVdyPqRJmp6nBCTKmvSk="
        )
        with pytest.raises(EbaySignatureError):
            verify(header=signature_header(signature=mismatched))

    def test_re_serialising_the_payload_is_not_relied_on(self) -> None:
        """Verification is over received bytes, not a re-encoding of them.

        Pretty-printing the same object produces different bytes and must fail.
        A verifier that re-serialised before checking would pass this and then
        reject any real notification whose formatting differed by a space.
        """
        pretty = json.dumps(OFFICIAL_MESSAGE, indent=2).encode()
        with pytest.raises(EbaySignatureError):
            verify(body=pretty)


class TestAlgorithmAndDigest:
    @pytest.mark.parametrize("algorithm", ["RSA", "HMAC", "", "ed25519"])
    def test_an_unsupported_algorithm_is_refused(self, algorithm: str) -> None:
        with pytest.raises(EbaySignatureError):
            verify(algorithm=algorithm)

    @pytest.mark.parametrize("digest", ["MD5", "", "SHA512", "crc32"])
    def test_an_unsupported_digest_is_refused(self, digest: str) -> None:
        with pytest.raises(EbaySignatureError):
            verify(digest=digest)

    def test_the_key_metadata_decides_the_digest_not_the_header(self) -> None:
        """A forged header must not be able to downgrade verification.

        The header claims SHA-256 while eBay's key says SHA-1. Believing the
        header would mean letting the sender choose how their own signature is
        checked, so the disagreement is a rejection.
        """
        with pytest.raises(EbaySignatureError):
            verify(header=signature_header(digest="SHA256"))

    def test_a_header_with_no_digest_defers_to_the_key(self) -> None:
        """eBay's key metadata alone is sufficient and authoritative."""
        verify(header=signature_header(digest=""))


class TestKeyHandling:
    def test_a_key_without_pem_armour_is_refused(self) -> None:
        with pytest.raises(EbaySignatureError):
            verify(key="MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE")

    def test_a_key_with_a_corrupt_body_is_refused(self) -> None:
        with pytest.raises(EbaySignatureError):
            verify(key="-----BEGIN PUBLIC KEY-----!!!not base64!!!-----END PUBLIC KEY-----")

    def test_an_rsa_key_is_refused_rather_than_crashing(self) -> None:
        """eBay signs with EC; an RSA key here means something is wrong.

        Without the explicit type check this would fail as a ``TypeError`` from
        inside ``verify`` — a 500 rather than a clean 412.
        """
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        rsa_pem = (
            rsa.generate_private_key(public_exponent=65537, key_size=2048)
            .public_key()
            .public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode()
        )
        with pytest.raises(EbaySignatureError):
            verify(key=rsa_pem)

    def test_a_key_that_already_has_newlines_still_loads(self) -> None:
        """Reflowing must be tolerant of a key eBay formats differently."""
        reflowed = OFFICIAL_PUBLIC_KEY.replace(
            "-----BEGIN PUBLIC KEY-----", "-----BEGIN PUBLIC KEY-----\n"
        ).replace("-----END PUBLIC KEY-----", "\n-----END PUBLIC KEY-----")
        verify(key=reflowed)

    def test_a_different_valid_key_does_not_verify(self) -> None:
        """A well-formed EC key that simply is not eBay's must be rejected."""
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        other = (
            ec.generate_private_key(ec.SECP256R1())
            .public_key()
            .public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode()
        )
        with pytest.raises(EbaySignatureError):
            verify(key=other)
