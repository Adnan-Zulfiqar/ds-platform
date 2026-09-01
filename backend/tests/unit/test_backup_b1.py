"""BACKUP-B1 — the encrypted backup, verification, restore and retention tooling.

These tests are adversarial by design. A backup system's happy path is trivial
and proves almost nothing: the failures that matter are a file that looks fine
and is not, a policy that deletes the last good copy, a password that reaches a
command line, and a readiness check that says "ready" because code was merged
rather than because anything was done.

**No PostgreSQL is required.** `pg_dump` and `pg_restore` are replaced by fakes
that let a test choose the exit code, the bytes and the timing. The real thing
is exercised end-to-end in `tests/integration/test_backup_restore_drill.py`,
which builds a database by running the migrations, backs it up, restores it
into a second database and compares digests. Both are necessary: the fakes can
reach failure paths a real `pg_dump` will not produce on demand, and only the
real one proves the dump is restorable.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import subprocess
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import pytest
from pydantic import SecretStr

from app.core import backup_crypto
from app.core.backup_crypto import (
    BackupKey,
    CorruptBackupError,
    InvalidBackupKeyError,
    TruncatedBackupError,
    WrongBackupKeyError,
    decrypt_stream,
    encrypt_stream,
    load_backup_key,
)
from app.core.production_readiness import RULES, Status
from app.services import backup_retention, database_backup
from app.services.backup_retention import (
    RetentionPolicy,
    apply_plan,
    assert_safe_backup_directory,
    plan_retention,
    scan_directory,
)
from app.services.database_backup import (
    LAST_DRILL_MARKER,
    LAST_SUCCESS_MARKER,
    BackupManifest,
    ConfigurationError,
    DumpError,
    ExitCode,
    PostgresTarget,
    VerificationError,
    collect_evidence,
    create_backup,
    evidence_shortfalls,
    load_key_from_settings,
    read_marker,
    resolve_backup_directory,
    restore_backup,
    verify_backup,
    write_marker,
)

pytestmark = pytest.mark.unit

#: A password distinctive enough that finding it anywhere is unambiguous.
SECRET_PASSWORD = "correct-horse-battery-staple-7f3a9c"

#: Bytes standing in for a `pg_dump` custom-format stream. Its first five bytes
#: are the real magic so that anything inspecting the shape is satisfied.
FAKE_DUMP = b"PGDMP" + bytes(range(256)) * 40


def a_key(seed: bytes = b"\x01") -> BackupKey:
    material = hashlib.sha256(b"backup-b1-test-key" + seed).digest()
    return load_backup_key(base64.urlsafe_b64encode(material).decode())


class _Settings:
    """The narrow slice of `Settings` the backup code reads."""

    def __init__(self, directory: Path | None, key: str | None, **overrides: Any) -> None:
        self.backup = _BackupSettings(directory, key, **overrides)
        self.security = _SecuritySettings()
        self.database = _DatabaseSettings()


class _BackupSettings:
    def __init__(self, directory: Path | None, key: str | None, **overrides: Any) -> None:
        self.directory = str(directory) if directory else None
        self.encryption_key = SecretStr(key) if key else None
        self.pg_bin_dir = None
        self.offsite_destination = overrides.get("offsite_destination")
        self.retention_daily_days = overrides.get("retention_daily_days", 14)
        self.retention_weekly_weeks = overrides.get("retention_weekly_weeks", 8)
        self.retention_monthly_months = overrides.get("retention_monthly_months", 12)
        self.max_backup_age_hours = overrides.get("max_backup_age_hours", 30)
        self.max_drill_age_days = overrides.get("max_drill_age_days", 90)


class _SecuritySettings:
    def __init__(self) -> None:
        self.secret_key = SecretStr("a-production-signing-key-that-is-long-enough")
        self.otp_hmac_key = SecretStr("a-distinct-production-otp-key-also-long")
        self.encryption_keys: list[SecretStr] = []


class _DatabaseSettings:
    def __init__(self) -> None:
        self.host = "db.internal"
        self.port = 5432
        self.user = "droppilot_backup"
        self.password = SecretStr(SECRET_PASSWORD)
        self.db = "droppilot_staging"


def a_target(dbname: str = "droppilot_staging") -> PostgresTarget:
    return PostgresTarget(
        host="db.internal",
        port=5432,
        user="droppilot_backup",
        password=SECRET_PASSWORD,
        dbname=dbname,
    )


# ---------------------------------------------------------------------------
# Fakes for pg_dump / pg_restore / psycopg
# ---------------------------------------------------------------------------


class FakePopen:
    """Stands in for `pg_dump`, recording exactly how it was invoked."""

    calls: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, argv: list[str], **kwargs: Any) -> None:
        type(self).calls.append({"argv": list(argv), "env": dict(kwargs.get("env") or {})})
        self.stdout = io.BytesIO(self._payload())
        self.stderr = io.BytesIO(self._diagnostic())
        self._code = self._returncode()

    #: Overridden per test by subclassing or monkeypatching these hooks.
    @staticmethod
    def _payload() -> bytes:
        return FAKE_DUMP

    @staticmethod
    def _diagnostic() -> bytes:
        return b""

    @staticmethod
    def _returncode() -> int:
        return 0

    def kill(self) -> None:
        self._code = -9

    def wait(self) -> int:
        return self._code


class FakeCursor:
    def __init__(self, facts: dict[str, Any]) -> None:
        self._facts = facts
        self._row: tuple[Any, ...] | None = None

    def execute(self, sql: str, params: Any = None) -> None:
        lowered = " ".join(sql.lower().split())
        if "current_database" in lowered:
            self._row = (self._facts["database"], self._facts["version"])
        elif "to_regclass" in lowered:
            self._row = (self._facts.get("has_alembic", True),)
        elif "alembic_version" in lowered:
            self._row = (self._facts.get("revision", "0032"),)
        elif "information_schema.tables" in lowered:
            self._row = (self._facts.get("tables", 0),)
        else:
            self._row = (self._facts.get("scalar", 0),)

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._row

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class FakeConnection:
    def __init__(self, facts: dict[str, Any]) -> None:
        self._facts = facts

    def cursor(self) -> FakeCursor:
        return FakeCursor(self._facts)

    def rollback(self) -> None:
        return None

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def link_directory(link: Path, target: Path) -> None:
    """Create a directory link, by whichever mechanism this account can use.

    A symlink needs elevation or Developer Mode on Windows; a **junction** does
    not, which is exactly why a junction is the form that turns up in the wild
    when an administrator moves an archive. Trying the symlink first and
    falling back keeps the test meaningful on an unprivileged account rather
    than skipping the control entirely.
    """
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        pass
    if os.name != "nt":
        pytest.skip("this account cannot create symlinks")
    result = REAL_RUN(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not link.exists():
        pytest.skip("this account can create neither a symlink nor a junction")


#: The genuine `subprocess.Popen`, captured before any test replaces it.
REAL_POPEN = subprocess.Popen

#: The genuine `subprocess.run`, captured before any test replaces it.
#: `harden_path` uses it to apply the restrictive ACL, so a fake that answered
#: *every* call would make the protection fail and disguise the failure as a
#: verification error.
REAL_RUN = subprocess.run


def only_pg_restore(handler: Any) -> Any:
    """Fake `pg_restore`, and let everything else — icacls — through."""

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if argv and "pg_restore" in str(argv[0]):
            return handler(argv)
        return REAL_RUN(argv, **kwargs)

    return run


@pytest.fixture
def fake_postgres(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace the database and both PostgreSQL binaries with controllable fakes."""
    facts: dict[str, Any] = {
        "database": "droppilot_staging",
        "version": "PostgreSQL 17.10 on x86_64-windows",
        "revision": "0032",
        "tables": 0,
    }
    FakePopen.calls = []

    # `database_backup.subprocess` IS the stdlib module, so patching `Popen`
    # patches it for everything — including `subprocess.run`, which
    # `harden_path` uses for icacls. Dispatch on the executable rather than
    # replacing it wholesale, or the ACL step fails and every test downstream
    # reports the wrong cause.
    def dispatch_popen(argv: list[str], **kwargs: Any) -> Any:
        if argv and "pg_dump" in str(argv[0]):
            return FakePopen(argv, **kwargs)
        return REAL_POPEN(argv, **kwargs)

    monkeypatch.setattr(database_backup, "_connect", lambda target: FakeConnection(facts))
    monkeypatch.setattr(database_backup, "_tool", lambda name, *, pg_bin_dir: f"/fake/{name}")
    monkeypatch.setattr(database_backup.subprocess, "Popen", dispatch_popen)

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if "--list" in argv:
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout="; Archive\n1; TABLE tenants\n2; TABLE users\n",
                stderr="",
            )
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(database_backup.subprocess, "run", fake_run)
    return facts


@pytest.fixture
def backup_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "droppilot" / "backups"
    directory.mkdir(parents=True)
    return directory


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------


class TestTheBackupKeyIsRefusedUnlessItIsOne:
    def test_a_missing_key_is_refused_with_generation_instructions(self) -> None:
        with pytest.raises(InvalidBackupKeyError, match="No backup encryption key"):
            load_backup_key(None)

    def test_an_empty_key_is_refused(self) -> None:
        with pytest.raises(InvalidBackupKeyError, match="empty"):
            load_backup_key("   ")

    def test_a_key_published_in_this_repository_is_refused(self) -> None:
        for published in backup_crypto.PUBLISHED_BACKUP_KEYS:
            with pytest.raises(InvalidBackupKeyError, match="published in this repository"):
                load_backup_key(published)

    def test_a_key_of_the_wrong_length_is_refused(self) -> None:
        with pytest.raises(InvalidBackupKeyError, match="decodes to 16 bytes"):
            load_backup_key(base64.urlsafe_b64encode(os.urandom(16)).decode())

    def test_a_key_that_is_not_base64_is_refused(self) -> None:
        with pytest.raises(InvalidBackupKeyError, match="not valid base64"):
            load_backup_key("this is definitely not base64 !!!! ????")

    def test_a_repeating_pattern_is_refused(self) -> None:
        with pytest.raises(InvalidBackupKeyError, match="repeating pattern"):
            load_backup_key(base64.urlsafe_b64encode(b"A" * 32).decode())

    def test_reusing_the_signing_key_is_refused(self) -> None:
        shared = base64.urlsafe_b64encode(os.urandom(32)).decode()
        with pytest.raises(InvalidBackupKeyError, match="SECURITY_SECRET_KEY"):
            load_backup_key(shared, forbidden={"SECURITY_SECRET_KEY": shared})

    def test_reusing_a_key_in_a_different_encoding_is_still_refused(self) -> None:
        """The obvious evasion: same bytes, standard alphabet instead of urlsafe."""
        # High bytes on purpose: they are what encode to `+` and `/` in the
        # standard alphabet and `-` and `_` in the urlsafe one. Low bytes give
        # identical strings in both, which the plain equality check catches
        # first and would make this test prove nothing.
        material = bytes(range(224, 256))
        urlsafe = base64.urlsafe_b64encode(material).decode()
        standard = base64.b64encode(material).decode()
        assert urlsafe != standard
        with pytest.raises(InvalidBackupKeyError, match="same bytes"):
            load_backup_key(urlsafe, forbidden={"SECURITY_ENCRYPTION_KEYS entry 1": standard})

    def test_a_forbidden_setting_that_is_not_a_key_does_not_break_loading(self) -> None:
        """`SECURITY_SECRET_KEY` is a passphrase, not base64. It must not crash."""
        key = load_backup_key(
            base64.urlsafe_b64encode(os.urandom(32)).decode(),
            forbidden={"SECURITY_SECRET_KEY": "a plain long passphrase, not base64 at all"},
        )
        assert key.key_id

    def test_the_key_identifier_reveals_nothing_and_is_stable(self) -> None:
        material = base64.urlsafe_b64encode(bytes(range(32))).decode()
        first, second = load_backup_key(material), load_backup_key(material)
        assert first.key_id == second.key_id
        assert len(first.key_id) == 16
        assert base64.urlsafe_b64decode(material).hex().find(first.key_id) == -1

    def test_repr_never_renders_the_key(self) -> None:
        key = a_key()
        assert key.material.hex() not in repr(key)
        assert key.key_id in repr(key)
        assert str(key) == repr(key)

    def test_settings_reuse_of_an_application_encryption_key_is_refused(self) -> None:
        shared = base64.urlsafe_b64encode(os.urandom(32)).decode()
        settings = _Settings(None, shared)
        settings.security.encryption_keys = [SecretStr(shared)]
        with pytest.raises(ConfigurationError, match="SECURITY_ENCRYPTION_KEYS"):
            load_key_from_settings(settings)


# ---------------------------------------------------------------------------
# The container
# ---------------------------------------------------------------------------


def seal(data: bytes, key: BackupKey, *, chunk_bytes: int = 1024) -> bytes:
    out = io.BytesIO()
    encrypt_stream(io.BytesIO(data), out, key=key, backup_id="ab" * 16, chunk_bytes=chunk_bytes)
    return out.getvalue()


class TestTheContainerRefusesEveryDamagedForm:
    def test_a_round_trip_returns_the_original_bytes(self) -> None:
        key = a_key()
        data = os.urandom(5000)
        assert b"".join(decrypt_stream(io.BytesIO(seal(data, key)), key=key)) == data

    def test_an_empty_dump_round_trips(self) -> None:
        key = a_key()
        assert b"".join(decrypt_stream(io.BytesIO(seal(b"", key)), key=key)) == b""

    def test_two_backups_of_identical_data_produce_different_ciphertext(self) -> None:
        """Otherwise an observer learns that nothing changed between two nights."""
        key = a_key()
        data = b"identical plaintext" * 500
        assert seal(data, key) != seal(data, key)

    def test_a_wrong_key_is_refused_before_anything_is_decrypted(self) -> None:
        sealed = seal(b"payload" * 500, a_key(b"\x01"))
        with pytest.raises(WrongBackupKeyError, match="written for key"):
            list(decrypt_stream(io.BytesIO(sealed), key=a_key(b"\x02")))

    def test_truncation_is_refused_as_truncation(self) -> None:
        sealed = seal(os.urandom(6000), a_key())
        with pytest.raises(TruncatedBackupError):
            list(decrypt_stream(io.BytesIO(sealed[: len(sealed) // 2]), key=a_key()))

    def test_losing_only_the_final_chunk_is_still_refused(self) -> None:
        """The dangerous truncation: a file that ends on a chunk boundary."""
        key = a_key()
        sealed = seal(os.urandom(4096), key, chunk_bytes=1024)
        # Each chunk is 4 length bytes + 1024 + 16 tag.
        with pytest.raises(TruncatedBackupError):
            list(decrypt_stream(io.BytesIO(sealed[: -(1024 + 16 + 4)]), key=key))

    def test_a_flipped_bit_in_the_body_is_refused(self) -> None:
        key = a_key()
        sealed = bytearray(seal(os.urandom(4000), key))
        sealed[-30] ^= 0x01
        with pytest.raises(CorruptBackupError, match="failed authentication"):
            list(decrypt_stream(io.BytesIO(bytes(sealed)), key=key))

    def test_editing_the_header_is_refused(self) -> None:
        key = a_key()
        sealed = seal(os.urandom(4000), key)
        edited = sealed.replace(b'"chunk_bytes":1024', b'"chunk_bytes":2048')
        assert edited != sealed
        with pytest.raises(CorruptBackupError):
            list(decrypt_stream(io.BytesIO(edited), key=key))

    def test_appending_bytes_after_the_final_chunk_is_refused(self) -> None:
        key = a_key()
        with pytest.raises(CorruptBackupError, match="past its final chunk"):
            list(decrypt_stream(io.BytesIO(seal(b"x" * 500, key) + b"junk"), key=key))

    def test_a_chunk_size_the_reader_could_not_accept_is_refused_before_writing(
        self,
    ) -> None:
        """`--chunk-bytes` is an operator flag, and this is what it could do.

        A chunk larger than the reading ceiling writes happily and then fails
        to decrypt — a backup that exists, verifies as a file, and cannot be
        read back. Discovered on the day of the restore, which is the one day
        it must not be.
        """
        out = io.BytesIO()
        with pytest.raises(backup_crypto.BackupCryptoError, match="read back"):
            encrypt_stream(
                io.BytesIO(b"payload"),
                out,
                key=a_key(),
                backup_id="ab" * 16,
                chunk_bytes=128 * 1024 * 1024,
            )
        assert out.getvalue() == b"", "bytes were written before the refusal"

    def test_a_zero_chunk_size_is_refused(self) -> None:
        with pytest.raises(backup_crypto.BackupCryptoError, match="read back"):
            encrypt_stream(
                io.BytesIO(b"payload"),
                io.BytesIO(),
                key=a_key(),
                backup_id="ab" * 16,
                chunk_bytes=0,
            )

    def test_the_largest_permitted_chunk_size_still_round_trips(self) -> None:
        key = a_key()
        data = os.urandom(3000)
        sealed = seal(data, key, chunk_bytes=64 * 1024 * 1024)
        assert b"".join(decrypt_stream(io.BytesIO(sealed), key=key)) == data

    def test_a_file_that_is_not_a_backup_is_refused_immediately(self) -> None:
        with pytest.raises(CorruptBackupError, match="does not begin with"):
            list(decrypt_stream(io.BytesIO(b"just some other file entirely"), key=a_key()))

    def test_chunks_may_not_be_reordered_between_backups(self) -> None:
        """Splicing one backup's chunk into another must not authenticate."""
        key = a_key()
        first = seal(b"A" * 3000, key, chunk_bytes=1024)
        second = seal(b"B" * 3000, key, chunk_bytes=1024)
        spliced = first[: len(first) // 2] + second[len(second) // 2 :]
        with pytest.raises(CorruptBackupError):
            list(decrypt_stream(io.BytesIO(spliced), key=key))


# ---------------------------------------------------------------------------
# Creating a backup
# ---------------------------------------------------------------------------


class TestCreatingABackup:
    def test_a_source_database_mismatch_refuses_before_dumping(
        self, fake_postgres: dict[str, Any], backup_dir: Path
    ) -> None:
        fake_postgres["database"] = "droppilot_other"
        with pytest.raises(ConfigurationError, match="but 'droppilot_staging' was expected"):
            create_backup(
                target=a_target(),
                expected_database="droppilot_staging",
                key=a_key(),
                directory=backup_dir,
            )
        assert FakePopen.calls == [], "pg_dump ran despite the identity check failing"
        assert list(backup_dir.iterdir()) == []

    def test_the_production_database_is_refused_without_the_explicit_flag(
        self, fake_postgres: dict[str, Any], backup_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """And refused before connecting, not after asking the server."""

        def refuse_to_connect(target: Any) -> Any:
            raise AssertionError("a connection was opened before the name was judged")

        monkeypatch.setattr(database_backup, "_connect", refuse_to_connect)
        with pytest.raises(ConfigurationError, match="Nothing was connected to"):
            create_backup(
                target=a_target("droppilot"),
                expected_database="droppilot",
                key=a_key(),
                directory=backup_dir,
            )
        assert FakePopen.calls == []

    def test_the_server_answering_with_a_production_name_is_still_refused(
        self, fake_postgres: dict[str, Any], backup_dir: Path
    ) -> None:
        """The backstop: the operator claimed one name, the server said another.

        This is the case the upfront check cannot catch, and it is the case
        that matters — a staging `.env` left pointing at production.
        """
        fake_postgres["database"] = "droppilot"
        with pytest.raises(ConfigurationError, match="but 'droppilot_staging' was expected"):
            create_backup(
                target=a_target("droppilot_staging"),
                expected_database="droppilot_staging",
                key=a_key(),
                directory=backup_dir,
                allow_production=True,
            )
        assert FakePopen.calls == []

    def test_the_password_never_reaches_the_argument_vector_or_the_environment(
        self, fake_postgres: dict[str, Any], backup_dir: Path
    ) -> None:
        """argv is readable by every user on the machine. This is not negotiable."""
        create_backup(
            target=a_target(),
            expected_database="droppilot_staging",
            key=a_key(),
            directory=backup_dir,
        )
        call = FakePopen.calls[0]
        assert SECRET_PASSWORD not in " ".join(call["argv"])
        assert "PGPASSWORD" not in call["env"]
        assert SECRET_PASSWORD not in json.dumps(call["env"])
        assert call["env"]["PGPASSFILE"]
        assert "--no-password" in call["argv"]

    def test_the_password_file_is_removed_after_the_dump(
        self, fake_postgres: dict[str, Any], backup_dir: Path
    ) -> None:
        create_backup(
            target=a_target(),
            expected_database="droppilot_staging",
            key=a_key(),
            directory=backup_dir,
        )
        assert not Path(FakePopen.calls[0]["env"]["PGPASSFILE"]).exists()

    def test_the_manifest_records_the_facts_and_no_secret(
        self, fake_postgres: dict[str, Any], backup_dir: Path
    ) -> None:
        outcome = create_backup(
            target=a_target(),
            expected_database="droppilot_staging",
            key=a_key(),
            directory=backup_dir,
        )
        raw = outcome.manifest_path.read_text(encoding="utf-8")
        assert SECRET_PASSWORD not in raw
        assert "db.internal" not in raw
        assert "://" not in raw
        assert "droppilot_backup" not in raw

        manifest = outcome.manifest
        assert manifest.database == "droppilot_staging"
        assert manifest.alembic_revision == "0032"
        assert manifest.postgres_version.startswith("PostgreSQL 17")
        assert manifest.dump_format == "pg_dump/custom"
        assert manifest.key_id == a_key().key_id
        assert manifest.plaintext_bytes == len(FAKE_DUMP)
        assert manifest.ciphertext_bytes == outcome.ciphertext_path.stat().st_size
        assert manifest.tool_version == database_backup.TOOL_VERSION

    def test_a_failed_dump_publishes_nothing_and_leaves_no_partial(
        self, fake_postgres: dict[str, Any], backup_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(FakePopen, "_returncode", staticmethod(lambda: 1))
        monkeypatch.setattr(
            FakePopen, "_diagnostic", staticmethod(lambda: b"pg_dump: error: connection failed")
        )
        with pytest.raises(DumpError, match="exited 1"):
            create_backup(
                target=a_target(),
                expected_database="droppilot_staging",
                key=a_key(),
                directory=backup_dir,
            )
        assert list(backup_dir.iterdir()) == [], "a failed dump left files behind"

    def test_a_dump_that_produces_nothing_is_refused(
        self, fake_postgres: dict[str, Any], backup_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(FakePopen, "_payload", staticmethod(lambda: b""))
        with pytest.raises(DumpError, match="produced no output"):
            create_backup(
                target=a_target(),
                expected_database="droppilot_staging",
                key=a_key(),
                directory=backup_dir,
            )
        assert list(backup_dir.iterdir()) == []

    def test_a_dump_error_message_cannot_carry_a_connection_string(
        self, fake_postgres: dict[str, Any], backup_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        leak = f"postgresql://droppilot_backup:{SECRET_PASSWORD}@db.internal/x".encode()
        monkeypatch.setattr(FakePopen, "_returncode", staticmethod(lambda: 1))
        monkeypatch.setattr(
            FakePopen,
            "_diagnostic",
            staticmethod(lambda: b"pg_dump: error: could not connect\n" + leak),
        )
        with pytest.raises(DumpError) as caught:
            create_backup(
                target=a_target(),
                expected_database="droppilot_staging",
                key=a_key(),
                directory=backup_dir,
            )
        assert SECRET_PASSWORD not in str(caught.value)
        assert "://" not in str(caught.value)
        assert "could not connect" in str(caught.value)

    def test_publication_is_atomic_the_manifest_arriving_last(
        self, fake_postgres: dict[str, Any], backup_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the manifest cannot be published, the ciphertext must not survive.

        A ciphertext with no manifest is counted by nothing and verified by
        nothing — but it looks exactly like a backup in a directory listing,
        which is how somebody comes to believe they have one.
        """
        real_replace = os.replace
        seen: list[str] = []

        def failing_replace(src: Any, dst: Any) -> None:
            seen.append(str(dst))
            if str(dst).endswith(database_backup.MANIFEST_SUFFIX):
                raise OSError(13, "permission denied")
            real_replace(src, dst)

        monkeypatch.setattr(database_backup.os, "replace", failing_replace)
        with pytest.raises(OSError):
            create_backup(
                target=a_target(),
                expected_database="droppilot_staging",
                key=a_key(),
                directory=backup_dir,
            )
        assert seen[0].endswith(database_backup.CIPHERTEXT_SUFFIX)
        assert list(backup_dir.iterdir()) == [], "an unverifiable ciphertext survived"

    def test_an_interruption_leaves_the_directory_as_it_was(
        self, fake_postgres: dict[str, Any], backup_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def interrupt(*args: Any, **kwargs: Any) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(database_backup, "encrypt_stream", interrupt)
        with pytest.raises(KeyboardInterrupt):
            create_backup(
                target=a_target(),
                expected_database="droppilot_staging",
                key=a_key(),
                directory=backup_dir,
            )
        assert list(backup_dir.iterdir()) == []

    def test_a_secret_assigned_to_a_manifest_field_is_refused_at_any_length(
        self, fake_postgres: dict[str, Any], backup_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The failure this guards: a future author adds a field and fills it in."""
        short = "pw12"
        target = replace(a_target(), password=short)
        fake_postgres["revision"] = short  # a field value equal to the password
        with pytest.raises(database_backup.EncryptionError, match="as a field value"):
            create_backup(
                target=target,
                expected_database="droppilot_staging",
                key=a_key(),
                directory=backup_dir,
            )
        assert list(backup_dir.iterdir()) == []

    def test_a_long_secret_embedded_anywhere_is_refused(
        self, fake_postgres: dict[str, Any], backup_dir: Path
    ) -> None:
        long_secret = "an-actual-production-password-9f2c"
        fake_postgres["revision"] = f"0032-{long_secret}-suffix"
        with pytest.raises(database_backup.EncryptionError, match="embedded"):
            create_backup(
                target=replace(a_target(), password=long_secret),
                expected_database="droppilot_staging",
                key=a_key(),
                directory=backup_dir,
            )

    def test_a_short_password_occurring_inside_a_database_name_is_not_a_leak(
        self, fake_postgres: dict[str, Any], backup_dir: Path
    ) -> None:
        """The false positive that made the first version of this guard unusable.

        A development role and password of `droppilot`, and a database honestly
        named `droppilot_staging`, must not be mistaken for a disclosure. A
        check that refuses correct input is a check that gets bypassed.
        """
        outcome = create_backup(
            target=replace(a_target(), password="droppilot"),
            expected_database="droppilot_staging",
            key=a_key(),
            directory=backup_dir,
        )
        assert outcome.manifest.database == "droppilot_staging"

    def test_a_manifest_field_shaped_like_a_dsn_is_refused(
        self, fake_postgres: dict[str, Any], backup_dir: Path
    ) -> None:
        fake_postgres["version"] = "postgresql://someone@somewhere/db"
        with pytest.raises(database_backup.EncryptionError, match="connection string"):
            create_backup(
                target=a_target(),
                expected_database="droppilot_staging",
                key=a_key(),
                directory=backup_dir,
            )

    def test_the_backup_name_carries_the_database_and_an_instant(
        self, fake_postgres: dict[str, Any], backup_dir: Path
    ) -> None:
        outcome = create_backup(
            target=a_target(),
            expected_database="droppilot_staging",
            key=a_key(),
            directory=backup_dir,
            now=datetime(2026, 9, 1, 2, 30, tzinfo=UTC),
        )
        assert outcome.ciphertext_path.name.startswith("droppilot_staging-20260901T023000Z-")
        assert outcome.ciphertext_path.name.endswith(".dpbk")


# ---------------------------------------------------------------------------
# Verifying
# ---------------------------------------------------------------------------


@pytest.fixture
def a_backup(fake_postgres: dict[str, Any], backup_dir: Path) -> Any:
    return create_backup(
        target=a_target(),
        expected_database="droppilot_staging",
        key=a_key(),
        directory=backup_dir,
        chunk_bytes=1024,
    )


class TestVerificationNeverAcceptsMereExistence:
    def test_a_good_backup_verifies_and_reports_its_table_of_contents(self, a_backup: Any) -> None:
        outcome = verify_backup(a_backup.ciphertext_path, key=a_key())
        assert outcome.toc_entries == 2
        assert outcome.plaintext_bytes == len(FAKE_DUMP)

    def test_a_ciphertext_with_no_manifest_is_not_a_backup(self, a_backup: Any) -> None:
        a_backup.manifest_path.unlink()
        with pytest.raises(VerificationError, match="no manifest"):
            verify_backup(a_backup.ciphertext_path, key=a_key())

    def test_a_truncated_file_is_refused(self, a_backup: Any) -> None:
        data = a_backup.ciphertext_path.read_bytes()
        a_backup.ciphertext_path.write_bytes(data[: len(data) - 200])
        with pytest.raises(VerificationError, match="truncated or has been appended"):
            verify_backup(a_backup.ciphertext_path, key=a_key())

    def test_a_corrupted_byte_is_refused_even_when_the_size_still_matches(
        self, a_backup: Any
    ) -> None:
        data = bytearray(a_backup.ciphertext_path.read_bytes())
        data[len(data) // 2] ^= 0xFF
        a_backup.ciphertext_path.write_bytes(bytes(data))
        with pytest.raises(VerificationError, match="SHA-256 does not match"):
            verify_backup(a_backup.ciphertext_path, key=a_key())

    def test_corruption_that_survives_the_digest_still_fails_authentication(
        self, a_backup: Any
    ) -> None:
        """Defence in depth: rewrite the manifest's digest to match the damage."""
        data = bytearray(a_backup.ciphertext_path.read_bytes())
        data[-40] ^= 0x01
        a_backup.ciphertext_path.write_bytes(bytes(data))
        manifest = replace(
            a_backup.manifest, ciphertext_sha256=hashlib.sha256(bytes(data)).hexdigest()
        ).signed(a_key())
        a_backup.manifest_path.write_text(manifest.to_json(), encoding="utf-8")
        with pytest.raises(VerificationError, match="failed authentication"):
            verify_backup(a_backup.ciphertext_path, key=a_key())

    def test_the_wrong_key_is_refused(self, a_backup: Any) -> None:
        with pytest.raises(VerificationError, match="does not match its contents"):
            verify_backup(a_backup.ciphertext_path, key=a_key(b"\x99"))

    def test_an_edited_manifest_is_refused(self, a_backup: Any) -> None:
        data = json.loads(a_backup.manifest_path.read_text(encoding="utf-8"))
        data["database"] = "droppilot"
        a_backup.manifest_path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(VerificationError, match="does not match its contents"):
            verify_backup(a_backup.ciphertext_path, key=a_key())

    def test_a_manifest_with_its_mac_removed_is_refused(self, a_backup: Any) -> None:
        data = json.loads(a_backup.manifest_path.read_text(encoding="utf-8"))
        data["mac"] = ""
        a_backup.manifest_path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(VerificationError, match="no authentication code"):
            verify_backup(a_backup.ciphertext_path, key=a_key())

    def test_a_manifest_with_an_extra_field_is_refused_rather_than_ignored(
        self, a_backup: Any
    ) -> None:
        data = json.loads(a_backup.manifest_path.read_text(encoding="utf-8"))
        data["connection_string_for_debugging"] = "postgresql://x"
        a_backup.manifest_path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(VerificationError, match="unrecognised fields"):
            verify_backup(a_backup.ciphertext_path, key=a_key())

    def test_a_manifest_of_a_future_schema_version_is_refused(self, a_backup: Any) -> None:
        data = json.loads(a_backup.manifest_path.read_text(encoding="utf-8"))
        data["manifest_version"] = 99
        a_backup.manifest_path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(VerificationError, match="schema version 99"):
            verify_backup(a_backup.ciphertext_path, key=a_key())

    def test_an_unreadable_dump_is_refused_even_when_the_bytes_are_intact(
        self, a_backup: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The last check: pg_restore must be able to parse what came out."""

        monkeypatch.setattr(
            database_backup.subprocess,
            "run",
            only_pg_restore(
                lambda argv: subprocess.CompletedProcess(
                    argv, 1, stdout="", stderr="not a valid archive"
                )
            ),
        )
        with pytest.raises(VerificationError, match="could not read the decrypted dump"):
            verify_backup(a_backup.ciphertext_path, key=a_key())

    def test_an_empty_table_of_contents_is_refused(
        self, a_backup: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def empty_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(argv, 0, stdout="; only a comment\n", stderr="")

        monkeypatch.setattr(database_backup.subprocess, "run", empty_run)
        with pytest.raises(VerificationError, match="restore nothing"):
            verify_backup(a_backup.ciphertext_path, key=a_key())

    def test_no_plaintext_survives_verification(self, a_backup: Any, tmp_path: Path) -> None:
        verify_backup(a_backup.ciphertext_path, key=a_key())
        leftovers = [
            p for p in Path(os.environ.get("TEMP", tmp_path)).glob("dp-verify-*") if p.is_dir()
        ]
        assert leftovers == []


# ---------------------------------------------------------------------------
# Restoring
# ---------------------------------------------------------------------------


class TestRestoreRefusesEveryWrongTarget:
    def test_the_default_is_a_rehearsal_that_writes_nothing(
        self, a_backup: Any, fake_postgres: dict[str, Any]
    ) -> None:
        fake_postgres["database"] = "droppilot_restore_check"
        outcome = restore_backup(
            a_backup.ciphertext_path,
            target=a_target("droppilot_restore_check"),
            expected_source="droppilot_staging",
            expected_target="droppilot_restore_check",
            key=a_key(),
        )
        assert outcome.dry_run is True
        assert "rehearsal only; nothing was written" in outcome.notes
        assert not any("pg_restore" in str(c) for c in FakePopen.calls)

    def test_a_backup_from_another_database_is_refused(
        self, a_backup: Any, fake_postgres: dict[str, Any]
    ) -> None:
        fake_postgres["database"] = "droppilot_restore_check"
        with pytest.raises(ConfigurationError, match="was taken from 'droppilot_staging'"):
            restore_backup(
                a_backup.ciphertext_path,
                target=a_target("droppilot_restore_check"),
                expected_source="droppilot_production_snapshot",
                expected_target="droppilot_restore_check",
                key=a_key(),
            )

    def test_the_protected_production_target_is_refused(self, a_backup: Any) -> None:
        with pytest.raises(ConfigurationError, match="protected production database"):
            restore_backup(
                a_backup.ciphertext_path,
                target=a_target("droppilot"),
                expected_source="droppilot_staging",
                expected_target="droppilot",
                key=a_key(),
            )

    def test_a_target_that_disagrees_with_the_connection_is_refused(self, a_backup: Any) -> None:
        with pytest.raises(ConfigurationError, match="but 'droppilot_elsewhere' was stated"):
            restore_backup(
                a_backup.ciphertext_path,
                target=a_target("droppilot_restore_check"),
                expected_source="droppilot_staging",
                expected_target="droppilot_elsewhere",
                key=a_key(),
            )

    def test_a_non_empty_target_is_refused_without_the_destructive_flag(
        self, a_backup: Any, fake_postgres: dict[str, Any]
    ) -> None:
        fake_postgres["database"] = "droppilot_restore_check"
        fake_postgres["tables"] = 37
        with pytest.raises(ConfigurationError, match="already holds 37 tables"):
            restore_backup(
                a_backup.ciphertext_path,
                target=a_target("droppilot_restore_check"),
                expected_source="droppilot_staging",
                expected_target="droppilot_restore_check",
                key=a_key(),
            )

    def test_a_non_empty_target_is_permitted_with_the_flag_and_noted(
        self, a_backup: Any, fake_postgres: dict[str, Any]
    ) -> None:
        fake_postgres["database"] = "droppilot_restore_check"
        fake_postgres["tables"] = 37
        outcome = restore_backup(
            a_backup.ciphertext_path,
            target=a_target("droppilot_restore_check"),
            expected_source="droppilot_staging",
            expected_target="droppilot_restore_check",
            key=a_key(),
            allow_non_empty=True,
        )
        assert any("target was not empty" in note for note in outcome.notes)

    def test_a_backup_that_does_not_verify_is_never_restored(
        self, a_backup: Any, fake_postgres: dict[str, Any]
    ) -> None:
        fake_postgres["database"] = "droppilot_restore_check"
        data = bytearray(a_backup.ciphertext_path.read_bytes())
        data[-40] ^= 0xFF
        a_backup.ciphertext_path.write_bytes(bytes(data))
        with pytest.raises(VerificationError):
            restore_backup(
                a_backup.ciphertext_path,
                target=a_target("droppilot_restore_check"),
                expected_source="droppilot_staging",
                expected_target="droppilot_restore_check",
                key=a_key(),
                apply=True,
            )

    def test_a_restore_decrypts_the_backup_exactly_once(
        self, a_backup: Any, fake_postgres: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Identity is judged from the manifest; the stream is read through once.

        The earlier version verified in full to learn which database the backup
        came from, then verified again to obtain the plaintext — two complete
        passes over a file that in production is measured in tens of gigabytes.
        """
        fake_postgres["database"] = "droppilot_restore_check"
        passes = 0
        real = database_backup.decrypt_stream

        def counting(*args: Any, **kwargs: Any) -> Any:
            nonlocal passes
            passes += 1
            return real(*args, **kwargs)

        monkeypatch.setattr(database_backup, "decrypt_stream", counting)
        restore_backup(
            a_backup.ciphertext_path,
            target=a_target("droppilot_restore_check"),
            expected_source="droppilot_staging",
            expected_target="droppilot_restore_check",
            key=a_key(),
            apply=True,
        )
        assert passes == 1

    def test_a_wrong_source_is_refused_without_decrypting_anything(
        self, a_backup: Any, fake_postgres: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refuse(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("the backup was decrypted before identity was judged")

        monkeypatch.setattr(database_backup, "decrypt_stream", refuse)
        with pytest.raises(ConfigurationError, match="was taken from"):
            restore_backup(
                a_backup.ciphertext_path,
                target=a_target("droppilot_restore_check"),
                expected_source="some_other_database",
                expected_target="droppilot_restore_check",
                key=a_key(),
            )

    def test_the_restore_runs_in_one_transaction_and_prints_no_rows(
        self, a_backup: Any, fake_postgres: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_postgres["database"] = "droppilot_restore_check"
        seen: list[list[str]] = []

        def capture(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            seen.append(list(argv))
            if "--list" in argv:
                return subprocess.CompletedProcess(argv, 0, stdout="1; TABLE tenants\n", stderr="")
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

        monkeypatch.setattr(database_backup.subprocess, "run", capture)
        outcome = restore_backup(
            a_backup.ciphertext_path,
            target=a_target("droppilot_restore_check"),
            expected_source="droppilot_staging",
            expected_target="droppilot_restore_check",
            key=a_key(),
            apply=True,
        )
        restore_argv = next(a for a in seen if "--single-transaction" in a)
        assert "--exit-on-error" in restore_argv
        assert SECRET_PASSWORD not in " ".join(restore_argv)
        assert outcome.dry_run is False
        assert all(not v.startswith("row") for v in outcome.integrity.values())


class TestTheProductionRestoreFlagStillDemandsATypedName:
    """The CLI's own gate, tested through the CLI because that is where it lives.

    **BACKUP-B1-R1 moved where the prompt happens.** It used to run in `main()`
    against the name on the command line; it now runs after the server has been
    asked what database it is, and confirms *that* name. So the refusal that
    needs no configuration is still asserted through `main()` here, and the
    prompt's own contract is asserted against `_confirm_production` directly —
    driving it through `main()` would now be a test of key loading.
    `tests/unit/test_backup_b1_r1_protected_names.py` covers the end-to-end
    path with a connection in place.
    """

    def _cli(self) -> Any:
        import scripts.restore_database_backup as module

        return module

    def test_production_is_refused_outright_without_the_flag(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = self._cli().main(
            ["--file", "x.dpbk", "--expect-source", "droppilot", "--target", "droppilot"]
        )
        assert code == int(ExitCode.CONFIGURATION)
        assert "typed confirmation" in capsys.readouterr().err

    def test_a_non_terminal_session_cannot_confirm_a_production_restore(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A scheduled task must never be able to answer this prompt."""
        module = self._cli()
        monkeypatch.setattr(module.sys.stdin, "isatty", lambda: False, raising=False)
        assert module._confirm_production("droppilot") is False
        assert "no terminal" in capsys.readouterr().err

    def test_an_eof_at_the_prompt_refuses_rather_than_crashing(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """`isatty` can be wrong; the prompt must not turn that into a traceback.

        A traceback exits 1, which this tooling reserves for "bad flag". A
        monitor reading exit codes would file an unattended production-restore
        attempt as the operator's typo.
        """
        module = self._cli()
        monkeypatch.setattr(module.sys.stdin, "isatty", lambda: True, raising=False)

        def eof(*args: Any) -> str:
            raise EOFError

        monkeypatch.setattr("builtins.input", eof)
        assert module._confirm_production("droppilot") is False
        assert "no confirmation was given" in capsys.readouterr().err

    def test_a_mistyped_name_refuses(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        module = self._cli()
        monkeypatch.setattr(module.sys.stdin, "isatty", lambda: True, raising=False)
        monkeypatch.setattr("builtins.input", lambda *a: "droppilot_staging")
        assert module._confirm_production("droppilot") is False
        assert "did not match" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------


def place_backup(directory: Path, key: BackupKey, created: datetime, *, valid: bool = True) -> Path:
    """Write a real, verifiable backup file dated `created`."""
    payload = FAKE_DUMP + created.isoformat().encode()
    out = io.BytesIO()
    backup_id = hashlib.sha256(created.isoformat().encode()).hexdigest()[:32]
    plain_len, plain_sha, cipher_len, cipher_sha = encrypt_stream(
        io.BytesIO(payload), out, key=key, backup_id=backup_id, chunk_bytes=1024
    )
    stem = f"droppilot_staging-{created.strftime('%Y%m%dT%H%M%SZ')}-{backup_id[:8]}"
    ciphertext, manifest_path = database_backup.backup_paths(directory, stem)
    ciphertext.write_bytes(out.getvalue())
    manifest = BackupManifest(
        manifest_version=database_backup.MANIFEST_VERSION,
        tool_version=database_backup.TOOL_VERSION,
        created_at=created.isoformat(),
        application_sha="0" * 40,
        database="droppilot_staging",
        postgres_version="PostgreSQL 17.10",
        alembic_revision="0032",
        dump_format="pg_dump/custom",
        backup_id=backup_id,
        key_id=key.key_id,
        encryption="AES-256-GCM/STREAM v1",
        chunk_bytes=1024,
        plaintext_bytes=plain_len,
        plaintext_sha256=plain_sha,
        ciphertext_bytes=cipher_len,
        ciphertext_sha256=cipher_sha,
    ).signed(key)
    manifest_path.write_text(manifest.to_json(), encoding="utf-8")
    if not valid:
        ciphertext.write_bytes(out.getvalue()[:-50])
    return ciphertext


NOW = datetime(2026, 9, 1, 3, 0, tzinfo=UTC)


class TestRetentionRefusesMoreThanItDeletes:
    def test_a_backup_inside_the_daily_window_is_kept(self, backup_dir: Path) -> None:
        key = a_key()
        for days in (0, 1, 5, 13):
            place_backup(backup_dir, key, NOW - timedelta(days=days))
        plan = plan_retention(scan_directory(backup_dir, key), RetentionPolicy(), now=NOW)
        assert plan.deletable == ()
        assert len(plan.retained) == 4

    def test_a_backup_outside_every_window_is_deletable(self, backup_dir: Path) -> None:
        key = a_key()
        place_backup(backup_dir, key, NOW)
        # Two in the same old week and month; only the newer survives as that
        # period's representative, and both are outside the monthly window.
        old = NOW - timedelta(days=500)
        place_backup(backup_dir, key, old)
        place_backup(backup_dir, key, old - timedelta(hours=6))
        plan = plan_retention(scan_directory(backup_dir, key), RetentionPolicy(), now=NOW)
        assert len(plan.deletable) == 2
        assert all("outside every retention window" in plan.reasons[n] for n in plan.deletable)

    def test_the_newest_verified_backup_is_never_deletable(self, backup_dir: Path) -> None:
        """Even when every window has expired around it."""
        key = a_key()
        newest = place_backup(backup_dir, key, NOW - timedelta(days=900))
        place_backup(backup_dir, key, NOW - timedelta(days=1200))
        plan = plan_retention(scan_directory(backup_dir, key), RetentionPolicy(), now=NOW)
        assert newest.name in plan.retained
        assert newest.name not in plan.deletable
        assert "newest backup that passed" in plan.reasons[newest.name]

    def test_the_only_verified_backup_is_never_deletable(self, backup_dir: Path) -> None:
        key = a_key()
        only = place_backup(backup_dir, key, NOW - timedelta(days=5000))
        plan = plan_retention(scan_directory(backup_dir, key), RetentionPolicy(), now=NOW)
        assert plan.deletable == ()
        assert only.name in plan.retained
        assert any("only one backup" in r for r in plan.refusals)

    def test_nothing_is_deleted_when_nothing_verifies(self, backup_dir: Path) -> None:
        key = a_key()
        place_backup(backup_dir, key, NOW - timedelta(days=900), valid=False)
        place_backup(backup_dir, key, NOW - timedelta(days=800), valid=False)
        plan = plan_retention(scan_directory(backup_dir, key), RetentionPolicy(), now=NOW)
        assert plan.deletable == ()
        assert len(plan.quarantine) == 2
        assert any("no backup passed" in r for r in plan.refusals)

    def test_a_corrupted_backup_is_quarantined_not_deleted(self, backup_dir: Path) -> None:
        key = a_key()
        place_backup(backup_dir, key, NOW)
        broken = place_backup(backup_dir, key, NOW - timedelta(days=1), valid=False)
        entries = scan_directory(backup_dir, key)
        plan = plan_retention(entries, RetentionPolicy(), now=NOW)
        assert broken.name in plan.quarantine
        assert broken.name not in plan.deletable

        apply_plan(backup_dir, plan, entries, apply=True)
        assert not broken.exists()
        assert (backup_dir / backup_retention.QUARANTINE_DIRNAME / broken.name).is_file()

    def test_a_ciphertext_with_no_manifest_is_quarantined(self, backup_dir: Path) -> None:
        key = a_key()
        place_backup(backup_dir, key, NOW)
        orphan = place_backup(backup_dir, key, NOW - timedelta(days=2))
        orphan.with_name(
            orphan.name[: -len(database_backup.CIPHERTEXT_SUFFIX)] + database_backup.MANIFEST_SUFFIX
        ).unlink()
        plan = plan_retention(scan_directory(backup_dir, key), RetentionPolicy(), now=NOW)
        assert orphan.name in plan.quarantine
        assert "no manifest" in plan.reasons[orphan.name]

    def test_an_abandoned_part_file_is_quarantined(self, backup_dir: Path) -> None:
        key = a_key()
        place_backup(backup_dir, key, NOW)
        (backup_dir / "droppilot_staging-old.dpbk.part").write_bytes(b"half a backup")
        plan = plan_retention(scan_directory(backup_dir, key), RetentionPolicy(), now=NOW)
        assert "droppilot_staging-old.dpbk.part" in plan.quarantine

    def test_a_backup_written_for_a_different_key_is_quarantined_not_deleted(
        self, backup_dir: Path
    ) -> None:
        place_backup(backup_dir, a_key(), NOW)
        foreign = place_backup(backup_dir, a_key(b"\x77"), NOW - timedelta(days=3))
        plan = plan_retention(scan_directory(backup_dir, a_key()), RetentionPolicy(), now=NOW)
        assert foreign.name in plan.quarantine

    def test_a_rehearsal_changes_nothing(self, backup_dir: Path) -> None:
        key = a_key()
        place_backup(backup_dir, key, NOW)
        doomed = place_backup(backup_dir, key, NOW - timedelta(days=900))
        entries = scan_directory(backup_dir, key)
        plan = plan_retention(entries, RetentionPolicy(), now=NOW)
        actions = apply_plan(backup_dir, plan, entries, apply=False)
        assert doomed.exists()
        assert all(a.startswith("would ") for a in actions)

    def test_applying_removes_the_manifest_with_the_backup(self, backup_dir: Path) -> None:
        key = a_key()
        place_backup(backup_dir, key, NOW)
        doomed = place_backup(backup_dir, key, NOW - timedelta(days=900))
        manifest = doomed.with_name(
            doomed.name[: -len(database_backup.CIPHERTEXT_SUFFIX)] + database_backup.MANIFEST_SUFFIX
        )
        entries = scan_directory(backup_dir, key)
        plan = plan_retention(entries, RetentionPolicy(), now=NOW)
        apply_plan(backup_dir, plan, entries, apply=True)
        assert not doomed.exists()
        assert not manifest.exists()

    def test_the_marker_directory_is_never_touched(self, backup_dir: Path) -> None:
        key = a_key()
        place_backup(backup_dir, key, NOW)
        place_backup(backup_dir, key, NOW - timedelta(days=900))
        write_marker(backup_dir, LAST_SUCCESS_MARKER, {"completed_at": NOW.isoformat()}, key)
        entries = scan_directory(backup_dir, key)
        plan = plan_retention(entries, RetentionPolicy(), now=NOW)
        apply_plan(backup_dir, plan, entries, apply=True)
        assert backup_retention.state_directory_untouched(backup_dir)
        assert read_marker(backup_dir, LAST_SUCCESS_MARKER, key) is not None

    def test_the_report_is_byte_identical_across_two_runs(self, backup_dir: Path) -> None:
        key = a_key()
        for days in (0, 40, 400):
            place_backup(backup_dir, key, NOW - timedelta(days=days))
        first = plan_retention(scan_directory(backup_dir, key), RetentionPolicy(), now=NOW)
        second = plan_retention(scan_directory(backup_dir, key), RetentionPolicy(), now=NOW)
        assert first == second

    def test_a_policy_window_below_one_is_refused(self) -> None:
        with pytest.raises(ConfigurationError, match="at least 1"):
            RetentionPolicy(daily_days=0)

    def test_apply_cannot_exceed_its_plan(self, backup_dir: Path) -> None:
        """A plan naming a file the scan never verified must not delete it."""
        key = a_key()
        kept = place_backup(backup_dir, key, NOW)
        entries = scan_directory(backup_dir, key)
        forged = plan_retention(entries, RetentionPolicy(), now=NOW)
        forged = replace(forged, deletable=("something-else.dpbk",))
        actions = apply_plan(backup_dir, forged, entries, apply=True)
        assert kept.exists()
        assert any("refused to delete" in a for a in actions)


class TestTheBackupDirectoryMustBeADedicatedPlace:
    def test_a_relative_path_is_refused(self) -> None:
        with pytest.raises(ConfigurationError, match="absolute path"):
            assert_safe_backup_directory(Path("backups"))

    def test_a_drive_or_filesystem_root_is_refused(self) -> None:
        root = Path("C:/") if os.name == "nt" else Path("/")
        with pytest.raises(ConfigurationError, match="too close to the filesystem root"):
            assert_safe_backup_directory(root)

    def test_the_home_directory_itself_is_refused(self) -> None:
        with pytest.raises(ConfigurationError, match="home directory"):
            assert_safe_backup_directory(Path.home())

    def test_a_source_checkout_is_refused(self, tmp_path: Path) -> None:
        checkout = tmp_path / "some" / "project"
        checkout.mkdir(parents=True)
        (checkout / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        with pytest.raises(ConfigurationError, match="source checkout"):
            assert_safe_backup_directory(checkout)

    def test_a_system_directory_is_refused(self, tmp_path: Path) -> None:
        system = tmp_path / "windows" / "system32"
        system.mkdir(parents=True)
        with pytest.raises(ConfigurationError, match="system directory"):
            assert_safe_backup_directory(system)

    def test_a_missing_directory_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError, match="does not exist"):
            assert_safe_backup_directory(tmp_path / "a" / "nowhere")

    def test_a_symlinked_directory_is_refused(self, tmp_path: Path) -> None:
        real = tmp_path / "real" / "backups"
        real.mkdir(parents=True)
        link = tmp_path / "real" / "link"
        link_directory(link, real)
        with pytest.raises(ConfigurationError, match="symlink or junction"):
            assert_safe_backup_directory(link)

    def test_a_traversing_name_cannot_reach_outside_the_directory(self, backup_dir: Path) -> None:
        with pytest.raises(ConfigurationError, match="only letters, digits"):
            database_backup.backup_paths(backup_dir, "../../escaped")

    def test_a_file_resolving_outside_the_directory_is_refused(
        self, backup_dir: Path, tmp_path: Path
    ) -> None:
        """Containment is judged on the *resolved* parent, not the given path."""
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        stray = elsewhere / "droppilot_staging-stray.dpbk"
        stray.write_bytes(b"not in the backup directory")
        with pytest.raises(ConfigurationError, match="resolves outside"):
            backup_retention.assert_contained(stray, backup_dir.resolve())

    def test_a_directory_link_inside_the_backup_directory_is_not_followed(
        self, backup_dir: Path, tmp_path: Path
    ) -> None:
        """A junction to another archive must not pull its files into scope.

        This is the shape a prune run destroys someone else's data through:
        somebody links a second archive in for convenience, and the retention
        policy applies to files it was never told about.
        """
        other = tmp_path / "other" / "archive"
        other.mkdir(parents=True)
        key = a_key()
        place_backup(backup_dir, key, NOW)
        place_backup(other, key, NOW - timedelta(days=900))
        link_directory(backup_dir / "linked", other)

        names = {e.name for e in scan_directory(backup_dir, key)}
        assert not any(n.endswith("linked") for n in names)
        assert len(names) == 1

    def test_a_second_hard_link_to_a_backup_is_seen_as_the_file_it_is(
        self, backup_dir: Path
    ) -> None:
        """A hard link is not a reparse point, so it is a second name, not a trick.

        Worth pinning: the scan must not crash on one, and it must not silently
        double-count a single file's bytes as two independent backups when
        deciding whether more than one good copy exists. The manifest is what
        makes a copy count, and a bare second name has none.
        """
        key = a_key()
        real = place_backup(backup_dir, key, NOW)
        link = backup_dir / "droppilot_staging-linked.dpbk"
        try:
            link.symlink_to(real)
            expected_ok = False
        except (OSError, NotImplementedError):
            os.link(real, link)
            expected_ok = False  # no manifest under the second name

        entry = next(e for e in scan_directory(backup_dir, key) if e.name == link.name)
        assert entry.ok is expected_ok
        plan = plan_retention(scan_directory(backup_dir, key), RetentionPolicy(), now=NOW)
        assert link.name in plan.quarantine
        assert real.name in plan.retained


# ---------------------------------------------------------------------------
# Markers and readiness
# ---------------------------------------------------------------------------


class TestEvidenceCannotBeAsserted:
    def test_a_marker_survives_a_round_trip(self, backup_dir: Path) -> None:
        key = a_key()
        write_marker(backup_dir, LAST_SUCCESS_MARKER, {"completed_at": NOW.isoformat()}, key)
        assert read_marker(backup_dir, LAST_SUCCESS_MARKER, key) == {
            "completed_at": NOW.isoformat()
        }

    def test_a_forged_marker_is_not_read(self, backup_dir: Path) -> None:
        """Creating a file must not be able to assert that backups are healthy."""
        state = backup_dir / database_backup.STATE_DIRNAME
        state.mkdir()
        (state / LAST_SUCCESS_MARKER).write_text(
            json.dumps({"payload": {"completed_at": NOW.isoformat()}, "mac": "0" * 64}),
            encoding="utf-8",
        )
        assert read_marker(backup_dir, LAST_SUCCESS_MARKER, a_key()) is None

    def test_a_marker_written_under_another_key_is_not_read(self, backup_dir: Path) -> None:
        write_marker(backup_dir, LAST_SUCCESS_MARKER, {"completed_at": NOW.isoformat()}, a_key())
        assert read_marker(backup_dir, LAST_SUCCESS_MARKER, a_key(b"\x55")) is None

    def test_a_failed_backup_never_updates_the_last_success_marker(
        self,
        fake_postgres: dict[str, Any],
        backup_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The single most important property of the schedule's evidence."""
        import scripts.create_database_backup as cli

        key = a_key()
        settings = _Settings(backup_dir, base64.urlsafe_b64encode(key.material).decode())
        monkeypatch.setattr(cli, "settings", settings)
        monkeypatch.setattr(database_backup, "_connect", lambda t: FakeConnection(fake_postgres))

        assert cli.main(["--expect-database", "droppilot_staging"]) == int(ExitCode.OK)
        first = read_marker(backup_dir, LAST_SUCCESS_MARKER, key)
        assert first is not None

        monkeypatch.setattr(FakePopen, "_returncode", staticmethod(lambda: 1))
        assert cli.main(["--expect-database", "droppilot_staging"]) == int(ExitCode.DUMP)
        assert read_marker(backup_dir, LAST_SUCCESS_MARKER, key) == first

    def test_the_cli_connects_to_the_configured_database_not_the_expected_one(
        self,
        fake_postgres: dict[str, Any],
        backup_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Otherwise `current_database()` agrees by construction and proves nothing.

        The failure being guarded is a configuration file pointing somewhere
        unintended. A tool that dials the name the operator expected can never
        detect that, however loudly it compares afterwards.
        """
        import scripts.create_database_backup as cli

        key = a_key()
        settings = _Settings(backup_dir, base64.urlsafe_b64encode(key.material).decode())
        settings.database.db = "droppilot_configured"
        monkeypatch.setattr(cli, "settings", settings)

        dialled: list[str] = []

        def record(target: Any) -> Any:
            dialled.append(target.dbname)
            return FakeConnection(fake_postgres)

        monkeypatch.setattr(database_backup, "_connect", record)
        cli.main(["--expect-database", "droppilot_staging"])
        assert dialled == ["droppilot_configured"]

    def test_an_identity_failure_exits_two_and_writes_no_marker(
        self,
        fake_postgres: dict[str, Any],
        backup_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import scripts.create_database_backup as cli

        key = a_key()
        monkeypatch.setattr(
            cli, "settings", _Settings(backup_dir, base64.urlsafe_b64encode(key.material).decode())
        )
        monkeypatch.setattr(database_backup, "_connect", lambda t: FakeConnection(fake_postgres))
        assert cli.main(["--expect-database", "droppilot_wrong"]) == int(ExitCode.CONFIGURATION)
        assert read_marker(backup_dir, LAST_SUCCESS_MARKER, key) is None


class TestReadinessStaysBlockedUntilSomethingHasActuallyHappened:
    def _rule(self) -> Any:
        return next(rule for rule in RULES if rule.setting == "BACKUPS")

    def test_an_unconfigured_installation_is_blocked_and_says_why(self) -> None:
        finding = self._rule().check(_Settings(None, None))
        assert finding.status is Status.BLOCKED
        assert "no valid backup encryption key" in finding.reason
        assert "no backup directory" in finding.reason
        assert "no authenticated record of a successful backup" in finding.reason
        assert "no authenticated record of a successful restore drill" in finding.reason
        assert "no off-site destination" in finding.reason

    def test_merging_the_tooling_does_not_make_it_pass(self, backup_dir: Path) -> None:
        """Configuration alone is never evidence. This is the whole point."""
        key = a_key()
        settings = _Settings(
            backup_dir,
            base64.urlsafe_b64encode(key.material).decode(),
            offsite_destination="an S3 bucket somebody promised to create",
        )
        finding = self._rule().check(settings)
        assert finding.status is Status.BLOCKED
        assert "no authenticated record of a successful backup" in finding.reason

    def test_a_backup_alone_is_not_enough_without_a_drill(self, backup_dir: Path) -> None:
        key = a_key()
        settings = _Settings(
            backup_dir,
            base64.urlsafe_b64encode(key.material).decode(),
            offsite_destination="uk-south object storage, write-once",
        )
        write_marker(
            backup_dir,
            LAST_SUCCESS_MARKER,
            {"completed_at": datetime.now(UTC).isoformat()},
            key,
        )
        finding = self._rule().check(settings)
        assert finding.status is Status.BLOCKED
        assert "restore drill" in finding.reason

    def test_a_stale_backup_is_reported_by_age(self, backup_dir: Path) -> None:
        key = a_key()
        settings = _Settings(
            backup_dir,
            base64.urlsafe_b64encode(key.material).decode(),
            offsite_destination="uk-south object storage",
        )
        write_marker(
            backup_dir,
            LAST_SUCCESS_MARKER,
            {"completed_at": (datetime.now(UTC) - timedelta(days=4)).isoformat()},
            key,
        )
        write_marker(
            backup_dir, LAST_DRILL_MARKER, {"completed_at": datetime.now(UTC).isoformat()}, key
        )
        finding = self._rule().check(settings)
        assert finding.status is Status.BLOCKED
        assert "hours old" in finding.reason

    def test_every_piece_of_evidence_together_can_pass(self, backup_dir: Path) -> None:
        """The rule must be reachable, or it is a constant wearing a check's clothes."""
        key = a_key()
        settings = _Settings(
            backup_dir,
            base64.urlsafe_b64encode(key.material).decode(),
            offsite_destination="uk-south object storage, write-once credentials",
        )
        for marker in (LAST_SUCCESS_MARKER, LAST_DRILL_MARKER):
            write_marker(backup_dir, marker, {"completed_at": datetime.now(UTC).isoformat()}, key)
        finding = self._rule().check(settings)
        assert finding.status is Status.PASS

    def test_the_finding_never_contains_an_equals_sign(self, backup_dir: Path) -> None:
        """The PROD-H1 reason contract applies here too, and is easy to breach."""
        for settings in (
            _Settings(None, None),
            _Settings(backup_dir, base64.urlsafe_b64encode(a_key().material).decode()),
        ):
            assert "=" not in self._rule().check(settings).reason

    def test_shortfalls_are_listed_in_reading_order(self) -> None:
        evidence = collect_evidence(_Settings(None, None))
        reasons = evidence_shortfalls(
            evidence, max_backup_age=timedelta(hours=30), max_drill_age=timedelta(days=90)
        )
        assert reasons[0].startswith("no valid backup encryption key")
        assert reasons[-1].startswith("no off-site destination")


class TestTheDirectoryResolverRefusesTheObviousMistakes:
    def test_an_unset_directory_is_refused(self) -> None:
        with pytest.raises(ConfigurationError, match="No backup directory is configured"):
            resolve_backup_directory(None, create=False)

    def test_a_relative_directory_is_refused(self) -> None:
        with pytest.raises(ConfigurationError, match="absolute path"):
            resolve_backup_directory("backups", create=False)

    def test_a_missing_directory_is_refused_when_not_creating(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError, match="does not exist"):
            resolve_backup_directory(str(tmp_path / "absent"), create=False)
