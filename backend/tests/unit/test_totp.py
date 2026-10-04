"""Track E5 — TOTP against the RFC 6238 Appendix B test vectors (SHA-1)."""

from __future__ import annotations

import base64

import pytest

from app.core import totp

pytestmark = pytest.mark.unit

#: RFC 6238's SHA-1 seed is the ASCII string "12345678901234567890".
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode().rstrip("=")


@pytest.mark.parametrize(
    ("unix_time", "expected"),
    [
        (59, "94287082"),
        (1111111109, "07081804"),
        (1111111111, "14050471"),
        (1234567890, "89005924"),
        (2000000000, "69279037"),
        (20000000000, "65353130"),
    ],
)
def test_rfc_6238_vectors(unix_time: int, expected: str) -> None:
    step = totp.current_step(unix_time)
    assert totp.code_at(RFC_SECRET, step, digits=8) == expected


def test_a_current_code_verifies_and_reports_its_step() -> None:
    now = 1_700_000_000.0
    step = totp.current_step(now)
    code = totp.code_at(RFC_SECRET, step)
    assert totp.verify(RFC_SECRET, code, now=now) == step


def test_one_step_of_drift_either_way_is_accepted_and_no_more() -> None:
    now = 1_700_000_000.0
    step = totp.current_step(now)
    assert totp.verify(RFC_SECRET, totp.code_at(RFC_SECRET, step - 1), now=now) == step - 1
    assert totp.verify(RFC_SECRET, totp.code_at(RFC_SECRET, step + 1), now=now) == step + 1
    assert totp.verify(RFC_SECRET, totp.code_at(RFC_SECRET, step - 2), now=now) is None


@pytest.mark.parametrize("bad", ["", "12345", "1234567", "abcdef", "12 34 5x"])
def test_malformed_codes_are_rejected(bad: str) -> None:
    assert totp.verify(RFC_SECRET, bad, now=1_700_000_000.0) is None


def test_generated_secrets_are_160_bit_base32_and_distinct() -> None:
    a, b = totp.generate_secret(), totp.generate_secret()
    assert a != b and len(a) == 32
    assert len(base64.b32decode(a + "=" * (-len(a) % 8))) == 20


def test_the_provisioning_uri_names_issuer_account_and_period() -> None:
    uri = totp.provisioning_uri("ABC", account="ops@example.com")
    assert uri.startswith("otpauth://totp/DropPilot%20Platform%3Aops%40example.com?")
    assert "secret=ABC" in uri and "digits=6" in uri and "period=30" in uri
