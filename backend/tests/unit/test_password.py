"""Tests for password hashing and strength validation."""

from __future__ import annotations

import pytest

from app.core.exceptions import ValidationError
from app.core.password import (
    hash_password,
    normalise_password,
    validate_password_strength,
    verify_password,
)

pytestmark = pytest.mark.unit

VALID_PASSWORD = "Correct-Horse-Battery9"


class TestHashing:
    def test_verifies_the_original_password(self) -> None:
        assert verify_password(VALID_PASSWORD, hash_password(VALID_PASSWORD))

    def test_rejects_a_different_password(self) -> None:
        assert not verify_password("something-else-entirely", hash_password(VALID_PASSWORD))

    def test_hash_is_not_the_plaintext(self) -> None:
        digest = hash_password(VALID_PASSWORD)
        assert VALID_PASSWORD not in digest

    def test_same_password_hashes_differently_each_time(self) -> None:
        """Per-password salting.

        Identical hashes for identical passwords would let an attacker with the
        table see instantly which users share a password, and make a single
        rainbow table effective against all of them.
        """
        assert hash_password(VALID_PASSWORD) != hash_password(VALID_PASSWORD)

    def test_uses_argon2id(self) -> None:
        assert hash_password(VALID_PASSWORD).startswith("$argon2id$")

    def test_missing_hash_returns_false(self) -> None:
        """A user with no local password — invited or SSO — cannot sign in."""
        assert not verify_password(VALID_PASSWORD, None)

    def test_corrupt_hash_returns_false_rather_than_raising(self) -> None:
        """Unreadable stored data must deny access, never crash or admit."""
        assert not verify_password(VALID_PASSWORD, "not-a-valid-argon2-hash")

    def test_long_passphrase_is_not_truncated(self) -> None:
        """Argon2 has no 72-byte limit, unlike bcrypt.

        Under bcrypt these two would share a prefix beyond the cut-off and
        verify against each other — silently making a long passphrase weaker
        than it appears.
        """
        base = "A9" + "x" * 100
        assert not verify_password(base + "DIFFERENT", hash_password(base + "ORIGINAL"))


class TestNormalisation:
    def test_composed_and_decomposed_forms_match(self) -> None:
        """A password must verify regardless of how the platform encodes accents.

        Without NFKC normalisation the same typed characters produce different
        byte sequences on different systems, locking a user out of their own
        account with no visible cause.
        """
        composed = "Passe-Café-2024"  # é as one code point
        decomposed = "Passe-Café-2024"  # e + combining acute

        assert normalise_password(composed) == normalise_password(decomposed)
        assert verify_password(decomposed, hash_password(composed))


class TestStrengthValidation:
    def test_accepts_a_strong_password(self) -> None:
        validate_password_strength(VALID_PASSWORD)

    def test_rejects_a_short_password(self) -> None:
        with pytest.raises(ValidationError):
            validate_password_strength("Ab3short")

    def test_rejects_a_password_with_no_digit(self) -> None:
        with pytest.raises(ValidationError):
            validate_password_strength("NoDigitsInHereAtAll")

    def test_rejects_a_password_with_no_uppercase(self) -> None:
        with pytest.raises(ValidationError):
            validate_password_strength("all-lowercase-9999")

    @pytest.mark.parametrize("common", ["password123", "Password123", "qwerty123"])
    def test_rejects_common_passwords(self, common: str) -> None:
        """Composition rules alone would pass some of these."""
        with pytest.raises(ValidationError):
            validate_password_strength(common)

    def test_rejects_a_password_containing_the_email_local_part(self) -> None:
        """Guessable by anyone who knows the address — which is everyone."""
        with pytest.raises(ValidationError):
            validate_password_strength("Adnan1234567", email="adnan@example.com")

    def test_reports_every_failure_at_once(self) -> None:
        """A form revealing one new rule per submission is hostile."""
        with pytest.raises(ValidationError) as exc_info:
            validate_password_strength("short")

        detail = str(exc_info.value.details.get("password", ""))
        assert detail.count(";") >= 1

    def test_rejection_message_does_not_echo_the_password(self) -> None:
        """Error text reaches logs and error reporters."""
        secret = "tinysecret"
        with pytest.raises(ValidationError) as exc_info:
            validate_password_strength(secret)

        assert secret not in str(exc_info.value)
        assert secret not in str(exc_info.value.details)
