"""Password hashing and strength validation.

**Argon2id**, not bcrypt. Both are acceptable, but Argon2id is memory-hard: an
attacker with GPUs or custom silicon gains far less advantage per unit of cost,
because memory bandwidth does not parallelise the way raw compute does. Argon2id
also has no 72-byte truncation limit, so a long passphrase is hashed in full
rather than silently cut short as bcrypt would.

Nothing in this module logs, returns, or stores a plaintext password.
"""

from __future__ import annotations

import re
import unicodedata

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import settings
from app.core.exceptions import ValidationError
from app.core.logging import get_logger

logger = get_logger(__name__)

_hasher = PasswordHasher(
    time_cost=settings.security.argon2_time_cost,
    memory_cost=settings.security.argon2_memory_cost_kib,
    parallelism=settings.security.argon2_parallelism,
)

# A dummy hash of a value nobody knows, used to spend the same CPU time when a
# login names an account that does not exist. See `verify_password`.
_DUMMY_HASH = _hasher.hash("a-password-that-is-never-a-real-password")

_SYMBOL_PATTERN = re.compile(r"[^A-Za-z0-9]")

# Rejected outright regardless of whether they satisfy the composition rules.
# A short denylist catches the passwords that appear at the top of every
# credential-stuffing list; it is not a substitute for a breach-corpus check
# (see the note in `validate_password_strength`).
_COMMON_PASSWORDS = frozenset(
    {
        "password",
        "password1",
        "password123",
        "passw0rd",
        "qwerty",
        "qwerty123",
        "letmein",
        "welcome",
        "welcome1",
        "admin",
        "administrator",
        "iloveyou",
        "monkey",
        "dragon",
        "sunshine",
        "princess",
        "football",
        "baseball",
        "abc123",
        "123456",
        "1234567",
        "12345678",
        "123456789",
        "1234567890",
        "changeme",
        "droppilot",
    }
)


def normalise_password(password: str) -> str:
    """Apply Unicode NFKC normalisation.

    Without it, a password typed with a composed accent (U+00E9) and the same
    password typed with a combining accent (U+0065 U+0301) are different byte
    sequences and produce different hashes — so a user who switches keyboard or
    platform is locked out of their own account for no visible reason.
    """
    return unicodedata.normalize("NFKC", password)


def hash_password(password: str) -> str:
    """Hash a password for storage.

    The returned string is a PHC-format hash embedding the algorithm, its
    parameters, and the per-password salt. Storing the parameters alongside the
    hash is what makes `needs_rehash` possible later without a flag day.
    """
    return _hasher.hash(normalise_password(password))


def _burn_cpu_to_match_timing() -> None:
    """Perform one Argon2 verification that is guaranteed to fail.

    Called when there is no hash to check against, so that the code path costs
    the same as a genuine verification.
    """
    try:
        _hasher.verify(_DUMMY_HASH, "not-the-dummy-password")
    except VerifyMismatchError:
        pass


def verify_password(password: str, password_hash: str | None) -> bool:
    """Check a password against a stored hash.

    ``password_hash`` may be ``None`` — a user provisioned by invite or SSO has
    no local password. That case still runs a hash verification against a dummy
    value before returning ``False``.

    **Why the dummy hash.** Returning early for an unknown account makes the
    response measurably faster than one for a known account, and that timing
    difference lets an attacker enumerate valid email addresses. Spending the
    same CPU either way removes the signal. Callers must also return an
    identical error message in both cases for this to be worth anything.
    """
    if password_hash is None:
        _burn_cpu_to_match_timing()
        return False

    try:
        return _hasher.verify(password_hash, normalise_password(password))
    except VerifyMismatchError:
        return False
    except (VerificationError, InvalidHashError):
        # A stored hash that cannot be parsed is data corruption or a hash
        # written by a different scheme. Log it — this needs investigation —
        # but never tell the client, and never treat it as a successful login.
        logger.error("password_hash_unreadable")
        return False


def needs_rehash(password_hash: str) -> bool:
    """Whether a stored hash was produced with weaker parameters than current.

    Hardware gets faster, so Argon2 parameters are raised over time. Checking on
    each successful login lets hashes be upgraded transparently — the plaintext
    is only available at that moment, so it is the single opportunity to do it.
    """
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def validate_password_strength(password: str, *, email: str | None = None) -> None:
    """Raise :class:`ValidationError` if the password is unacceptable.

    Every failing rule is reported at once rather than one per attempt — a form
    that reveals one new requirement per submission is hostile.

    **What this deliberately does not do.** It does not check the password
    against a corpus of breached credentials. That check catches far more real
    compromise than any composition rule, and should be added (k-anonymity
    range query against Have I Been Pwned, or a local corpus) before this
    platform holds live customer accounts.
    """
    security = settings.security
    normalised = normalise_password(password)
    failures: list[str] = []

    if len(normalised) < security.password_min_length:
        failures.append(f"must be at least {security.password_min_length} characters")
    if len(normalised) > security.password_max_length:
        failures.append(f"must be at most {security.password_max_length} characters")

    if security.password_require_lowercase and not any(c.islower() for c in normalised):
        failures.append("must contain a lowercase letter")
    if security.password_require_uppercase and not any(c.isupper() for c in normalised):
        failures.append("must contain an uppercase letter")
    if security.password_require_digit and not any(c.isdigit() for c in normalised):
        failures.append("must contain a digit")
    if security.password_require_symbol and not _SYMBOL_PATTERN.search(normalised):
        failures.append("must contain a symbol")

    if normalised.lower() in _COMMON_PASSWORDS:
        failures.append("is too common")

    # A password containing the local part of the user's own address is trivially
    # guessable by anyone who knows the address — which is everyone who can see
    # the login form.
    if email:
        local_part = email.split("@", 1)[0].strip().lower()
        if len(local_part) >= 4 and local_part in normalised.lower():
            failures.append("must not contain your email address")

    if failures:
        raise ValidationError(
            "The password does not meet the security requirements.",
            details={"password": "; ".join(failures)},
        )


__all__ = [
    "hash_password",
    "needs_rehash",
    "normalise_password",
    "validate_password_strength",
    "verify_password",
]
