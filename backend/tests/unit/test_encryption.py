"""Tests for credential encryption.

These guard the property the whole integration phase rests on: a database dump
must not yield working access to a customer's supplier account.
"""

from __future__ import annotations

import pytest

from app.core import encryption
from app.core.encryption import (
    DecryptionError,
    decrypt,
    encrypt,
    is_encryption_configured,
    mask,
    reset_cipher_cache,
    rotate,
)

pytestmark = pytest.mark.unit

SECRET = "aliexpress-app-secret-value-12345"


@pytest.fixture(autouse=True)
def _fresh_cipher() -> None:
    """The cipher is cached; tests that change keys must invalidate it."""
    reset_cipher_cache()


class TestRoundTrip:
    def test_decrypt_recovers_the_original(self) -> None:
        assert decrypt(encrypt(SECRET)) == SECRET

    def test_ciphertext_does_not_contain_the_plaintext(self) -> None:
        """The point of the exercise."""
        assert SECRET not in encrypt(SECRET)

    def test_encryption_is_non_deterministic(self) -> None:
        """Fernet uses a random IV per message.

        Deterministic ciphertext would let anyone with the table see which
        tenants share a credential, without decrypting anything.
        """
        assert encrypt(SECRET) != encrypt(SECRET)

    def test_both_ciphertexts_still_decrypt(self) -> None:
        assert decrypt(encrypt(SECRET)) == decrypt(encrypt(SECRET)) == SECRET

    @pytest.mark.parametrize(
        "value",
        ["a", "x" * 5000, "unicode-Ünïcödé-秘密", "with spaces and\nnewlines", "!@#$%^&*()"],
    )
    def test_handles_varied_input(self, value: str) -> None:
        assert decrypt(encrypt(value)) == value

    def test_empty_string_round_trips_without_ciphertext(self) -> None:
        """Distinguishing "absent" from "encrypted empty" costs nothing."""
        assert encrypt("") == ""
        assert decrypt("") == ""


class TestTampering:
    def test_altered_ciphertext_is_rejected(self) -> None:
        """Fernet authenticates, so tampering is detected.

        Without authentication, a modified ciphertext would decrypt to garbage
        that the application might then send to a supplier as a credential.
        """
        ciphertext = encrypt(SECRET)
        tampered = ciphertext[:-4] + ("AAAA" if not ciphertext.endswith("AAAA") else "BBBB")

        with pytest.raises(DecryptionError):
            decrypt(tampered)

    def test_unrelated_text_is_rejected(self) -> None:
        with pytest.raises(DecryptionError):
            decrypt("not-ciphertext-at-all")

    def test_ciphertext_from_an_unknown_key_is_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A value encrypted under a key that is no longer configured."""
        import base64

        from cryptography.fernet import Fernet
        from pydantic import SecretStr

        foreign_key = base64.urlsafe_b64encode(b"a-completely-different-32-byte!!").decode()
        foreign = Fernet(foreign_key.encode()).encrypt(SECRET.encode()).decode()

        with pytest.raises(DecryptionError):
            decrypt(foreign)

        # Sanity: it decrypts once that key is configured, proving the rejection
        # above was about the key rather than a malformed value.
        monkeypatch.setattr(
            encryption.settings.security,
            "encryption_keys",
            [SecretStr(foreign_key)],
        )
        reset_cipher_cache()
        assert decrypt(foreign) == SECRET


class TestKeyRotation:
    def test_a_value_from_an_older_key_still_decrypts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The property that makes zero-downtime rotation possible.

        Encrypt under the old key, prepend a new one, and the value must remain
        readable — otherwise rotation would require re-encrypting everything
        before the new key could be deployed.
        """
        from pydantic import SecretStr

        original_keys = list(encryption.settings.security.encryption_keys)
        old_key = original_keys[-1]

        # Only the old key configured.
        monkeypatch.setattr(encryption.settings.security, "encryption_keys", [old_key])
        reset_cipher_cache()
        ciphertext = encrypt(SECRET)

        # New key prepended, old retained.
        monkeypatch.setattr(
            encryption.settings.security,
            "encryption_keys",
            [SecretStr(original_keys[0].get_secret_value()), old_key],
        )
        reset_cipher_cache()

        assert decrypt(ciphertext) == SECRET

    def test_rotate_re_encrypts_under_the_newest_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from pydantic import SecretStr

        keys = list(encryption.settings.security.encryption_keys)
        old_key, new_key = keys[-1], keys[0]

        monkeypatch.setattr(encryption.settings.security, "encryption_keys", [old_key])
        reset_cipher_cache()
        old_ciphertext = encrypt(SECRET)

        monkeypatch.setattr(
            encryption.settings.security,
            "encryption_keys",
            [SecretStr(new_key.get_secret_value()), old_key],
        )
        reset_cipher_cache()
        rotated = rotate(old_ciphertext)

        assert rotated != old_ciphertext
        assert decrypt(rotated) == SECRET

        # And the rotated value survives dropping the old key — the final step
        # of a rotation.
        monkeypatch.setattr(
            encryption.settings.security,
            "encryption_keys",
            [SecretStr(new_key.get_secret_value())],
        )
        reset_cipher_cache()
        assert decrypt(rotated) == SECRET


class TestConfiguration:
    def test_reports_configured_when_keys_are_present(self) -> None:
        assert is_encryption_configured()

    def test_encryption_refuses_when_no_key_is_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Refusing beats writing a customer's secret in plaintext."""
        from app.core.encryption import EncryptionNotConfiguredError

        monkeypatch.setattr(encryption.settings.security, "encryption_keys", [])
        reset_cipher_cache()

        assert not is_encryption_configured()
        with pytest.raises(EncryptionNotConfiguredError):
            encrypt(SECRET)


class TestMasking:
    def test_shows_only_the_final_characters(self) -> None:
        assert mask("1234567890abcdef") == "••••cdef"

    @pytest.mark.parametrize("value", ["", "abc", "abcd"])
    def test_short_values_reveal_nothing(self, value: str) -> None:
        """A value too short to mask safely must not leak most of itself."""
        assert mask(value) == "••••"

    def test_never_returns_the_whole_value(self) -> None:
        secret = "super-secret-token-value"
        assert secret not in mask(secret)
