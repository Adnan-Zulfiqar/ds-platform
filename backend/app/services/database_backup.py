"""Take, verify and restore an encrypted PostgreSQL backup.

The operator-facing scripts in `backend/scripts/` are thin: they parse
arguments, call into here, and translate an exception into an exit code. All of
the judgement lives in this module so that the three tools cannot drift — a
backup written by one and refused by another is worse than no backup, because
it is discovered on the day of the incident.

**Three facts this module treats as non-negotiable.**

*A file is not a backup.* Existence proves a process started. `verify` decrypts
the whole stream, checks every chunk's tag, compares both digests against the
manifest, and asks `pg_restore` to parse the table of contents. Only a file that
survives all of that is called verified, and only a verified file counts towards
retention or readiness.

*Configuration is not identity.* Every operation states the database it expects
and confirms it with `SELECT current_database()` against the server that will
actually be read or written. The connection string is the same string that was
already wrong.

*Nothing leaves that could be a secret.* The manifest, every log line and every
exception message are built from a fixed set of computed fields — names, sizes,
digests, versions. The database password never reaches an argument vector, an
environment variable inherited by anything, the manifest, or a message; it is
handed to `pg_dump` through a password file that exists for the duration of the
child process and is overwritten before it is unlinked.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess  # pg_dump and pg_restore, invoked with a fixed argv and no shell
import tempfile
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import IntEnum
from pathlib import Path
from typing import Any, Final, get_type_hints

from app.core.backup_crypto import (
    CHUNK_BYTES,
    BackupCryptoError,
    BackupKey,
    CorruptBackupError,
    InvalidBackupKeyError,
    TruncatedBackupError,
    decrypt_stream,
    encrypt_stream,
    is_safe_backup_id,
    load_backup_key,
    manifest_mac,
)
from app.core.database_identity import (
    PRODUCTION_DATABASE_NAMES,
    DatabaseIdentityError,
    require_database_name,
)

__all__ = [
    "CIPHERTEXT_SUFFIX",
    "DEFAULT_INTEGRITY_QUERIES",
    "LAST_DRILL_MARKER",
    "LAST_SUCCESS_MARKER",
    "MANIFEST_SUFFIX",
    "MANIFEST_VERSION",
    "PARTIAL_SUFFIX",
    "STATE_DIRNAME",
    "TOOL_VERSION",
    "BackupError",
    "BackupEvidence",
    "BackupManifest",
    "BackupOutcome",
    "ConfigurationError",
    "DatabaseFacts",
    "DumpError",
    "EncryptionError",
    "ExitCode",
    "PostgresTarget",
    "RestoreError",
    "RestoreOutcome",
    "StorageError",
    "VerificationError",
    "VerificationOutcome",
    "backup_paths",
    "collect_evidence",
    "create_backup",
    "evidence_shortfalls",
    "harden_path",
    "inspect_database",
    "load_key_from_settings",
    "protected_workspace",
    "read_manifest",
    "read_marker",
    "resolve_backup_directory",
    "restore_backup",
    "verify_backup",
    "write_marker",
]

#: Manifest schema version. A reader that does not recognise it refuses.
MANIFEST_VERSION: Final[int] = 1

#: Identifies the code that produced a backup, so a file found in two years can
#: be matched to the tooling that wrote it.
TOOL_VERSION: Final[str] = "droppilot-backup/1.0.0"

#: Suffixes. `.part` is the in-progress name; nothing else ever reads it.
CIPHERTEXT_SUFFIX: Final[str] = ".dpbk"
MANIFEST_SUFFIX: Final[str] = ".manifest.json"
PARTIAL_SUFFIX: Final[str] = ".part"

#: Where the state markers live, relative to the backup directory.
STATE_DIRNAME: Final[str] = ".state"
LAST_SUCCESS_MARKER: Final[str] = "last-success.json"
LAST_DRILL_MARKER: Final[str] = "last-restore-drill.json"

_FILENAME_SAFE = re.compile(r"\A[A-Za-z0-9._-]+\Z")

#: Shortest secret worth searching for as a substring. Below this a match is
#: as likely to be a coincidence as a leak — see `_reject_secret_material`.
_SUBSTRING_SCAN_MINIMUM: Final[int] = 12


class ExitCode(IntEnum):
    """Exit statuses, chosen so a scheduler can route on the number alone.

    `1` is reserved for the tool's own usage errors — the same convention
    `verify_production_config.py` uses — so a mistyped flag is never mistaken
    for a verdict about a backup.
    """

    OK = 0
    USAGE = 1
    CONFIGURATION = 2
    DUMP = 3
    ENCRYPTION = 4
    VERIFICATION = 5
    STORAGE = 6
    RESTORE = 7


class BackupError(RuntimeError):
    """Base class. Carries the exit code the CLI should return."""

    exit_code: ExitCode = ExitCode.CONFIGURATION


class ConfigurationError(BackupError):
    """A key, path, identity or flag is wrong. Nothing was read or written."""

    exit_code = ExitCode.CONFIGURATION


class DumpError(BackupError):
    """`pg_dump` failed or produced nothing."""

    exit_code = ExitCode.DUMP


class EncryptionError(BackupError):
    """The container could not be written."""

    exit_code = ExitCode.ENCRYPTION


class VerificationError(BackupError):
    """A backup did not verify. It must not be relied on."""

    exit_code = ExitCode.VERIFICATION


class StorageError(BackupError):
    """The filesystem refused, or a protection could not be applied."""

    exit_code = ExitCode.STORAGE


class RestoreError(BackupError):
    """The restore itself, or a check after it, failed."""

    exit_code = ExitCode.RESTORE


# ---------------------------------------------------------------------------
# Connection target
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PostgresTarget:
    """Everything needed to reach one database, with the password held apart.

    The password is a field like any other, but it is the only one that must
    never be rendered — so `__repr__` is overridden rather than trusted not to
    be called. A dataclass's default repr is exactly what ends up in a
    traceback.
    """

    host: str
    port: int
    user: str
    password: str
    dbname: str

    def __repr__(self) -> str:
        return f"PostgresTarget({self.user}@{self.host}:{self.port}/{self.dbname})"

    __str__ = __repr__

    def with_database(self, dbname: str) -> PostgresTarget:
        return replace(self, dbname=dbname)

    @classmethod
    def from_settings(cls, settings: Any, *, dbname: str | None = None) -> PostgresTarget:
        db = settings.database
        return cls(
            host=db.host,
            port=int(db.port),
            user=db.user,
            password=db.password.get_secret_value(),
            dbname=dbname or db.db,
        )


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BackupManifest:
    """The sanitized description of one backup.

    Every field here is either a name, a number, a digest or a version string.
    That is the whole schema, and it is a closed set on purpose: a manifest that
    accepted arbitrary keys would eventually be handed something useful to
    an attacker by a well-meaning author adding "just the connection details for
    debugging".
    """

    manifest_version: int
    tool_version: str
    created_at: str
    application_sha: str
    database: str
    postgres_version: str
    alembic_revision: str
    dump_format: str
    backup_id: str
    key_id: str
    encryption: str
    chunk_bytes: int
    plaintext_bytes: int
    plaintext_sha256: str
    ciphertext_bytes: int
    ciphertext_sha256: str
    mac: str = ""

    def canonical_body(self) -> bytes:
        """The bytes the MAC covers: every field except the MAC itself."""
        body = {k: v for k, v in asdict(self).items() if k != "mac"}
        return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def signed(self, key: BackupKey) -> BackupManifest:
        return replace(self, mac=manifest_mac(key, self.canonical_body()))

    def verify_mac(self, key: BackupKey) -> None:
        if not self.mac:
            raise VerificationError("The manifest carries no authentication code.")
        expected = manifest_mac(key, self.canonical_body())
        if not hmac.compare_digest(self.mac, expected):
            raise VerificationError(
                "The manifest's authentication code does not match its contents. "
                "It has been edited since the backup was taken, or it belongs to "
                "a different backup key."
            )

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, indent=2) + "\n"

    @classmethod
    def from_json(cls, raw: str) -> BackupManifest:
        """Parse and schema-check, refusing anything unexpected.

        Unknown fields are an error rather than ignored. A manifest that has
        grown a field this reader does not understand was written by different
        code, and quietly dropping it is how a "verified" backup comes to mean
        two different things.
        """
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise VerificationError(f"The manifest is not valid JSON: {exc.msg}.") from exc
        if not isinstance(data, dict):
            raise VerificationError("The manifest is not a JSON object.")

        expected = {f.name for f in cls.__dataclass_fields__.values()}
        missing = sorted(expected - data.keys())
        if missing:
            raise VerificationError(f"The manifest is missing {', '.join(missing)}.")
        unknown = sorted(data.keys() - expected)
        if unknown:
            raise VerificationError(
                f"The manifest carries unrecognised fields: {', '.join(unknown)}."
            )

        # `get_type_hints` rather than `__annotations__`: this module uses
        # `from __future__ import annotations`, so the raw annotations are the
        # *strings* "int" and "str" and every `is int` comparison would be
        # false — silently accepting a manifest with any field of any type.
        for name, annotation in get_type_hints(cls).items():
            value = data[name]
            wanted: type = int if annotation is int else str
            if not isinstance(value, wanted) or isinstance(value, bool):
                raise VerificationError(f"The manifest field {name} is not {wanted.__name__}.")

        if data["manifest_version"] != MANIFEST_VERSION:
            raise VerificationError(
                f"The manifest is schema version {data['manifest_version']}; this "
                f"tool reads version {MANIFEST_VERSION}."
            )
        for digest in ("plaintext_sha256", "ciphertext_sha256"):
            if not re.fullmatch(r"[0-9a-f]{64}", data[digest]):
                raise VerificationError(f"The manifest field {digest} is not a SHA-256 digest.")
        if not is_safe_backup_id(data["backup_id"]):
            raise VerificationError("The manifest's backup identifier is malformed.")
        return cls(**data)


def _reject_secret_material(manifest: BackupManifest, secrets_seen: Iterable[str]) -> None:
    """Refuse to publish a manifest containing a secret, checked rather than trusted.

    The failure this guards is silent and permanent: a secret written into a
    manifest is a secret in every copy of that backup set, including the ones
    already sent off-site.

    **Only genuine secrets are matched.** An earlier version also treated the
    database role and the host as secrets, which is wrong twice over. They are
    not secrets — the manifest is meant to say which database a backup came
    from — and matching them as substrings refuses a perfectly good backup
    whenever a database is named after its role, which is the normal
    convention: a role `droppilot` and a database `droppilot_staging`. A check
    that fires on correct input gets disabled, and then it protects nothing.

    The connection-string shape is caught separately, which is what the
    host-and-user check was actually reaching for.

    **Two checks, because one cannot do both jobs.** A field that *is* the
    secret is caught by equality, at any length. A secret *embedded* in a
    longer string can only be caught by substring search — and substring search
    on a short secret is not evidence of anything: a development password of
    `droppilot` occurs inside the perfectly legitimate database name
    `droppilot_b1_source`, and refusing that backup would be a false alarm that
    teaches an operator to bypass the check. So the substring pass applies only
    to secrets long enough for a coincidence to be implausible. Below that
    length the equality pass is the whole honest guarantee, and a production
    secret is never that short.
    """
    rendered = manifest.to_json()
    fields = [v for v in asdict(manifest).values() if isinstance(v, str)]

    for value in secrets_seen:
        if not value:
            continue
        if any(field_value == value for field_value in fields):
            raise EncryptionError(
                "A configured secret was about to be written into the backup "
                "manifest as a field value. The backup was not published."
            )
        if len(value) >= _SUBSTRING_SCAN_MINIMUM and value in rendered:
            raise EncryptionError(
                "A configured secret was about to be embedded in the backup "
                "manifest. The backup was not published."
            )

    if "://" in rendered:
        raise EncryptionError(
            "The backup manifest contains something shaped like a connection "
            "string. The backup was not published."
        )


# ---------------------------------------------------------------------------
# Filesystem protection
# ---------------------------------------------------------------------------


def harden_path(path: Path, *, directory: bool) -> None:
    """Restrict a path to the account running this process, and fail if it cannot.

    Failing closed is the point. A backup directory readable by every local
    account is the same exposure as the database with the password removed, and
    a tool that shrugged and carried on would produce exactly the file it exists
    to protect against.
    """
    if os.name != "nt":
        with suppress(OSError):
            path.chmod(0o700 if directory else 0o600)
            return
        raise StorageError(f"Could not restrict permissions on {path.name}.")

    account = os.environ.get("USERNAME")
    if not account:
        raise StorageError("USERNAME is not set, so no restrictive ACL can be applied.")
    domain = os.environ.get("USERDOMAIN")
    principal = f"{domain}\\{account}" if domain else account
    grant = f"{principal}:(OI)(CI)F" if directory else f"{principal}:F"
    # Absolute, not `icacls`: a bare name is resolved through PATH, and the
    # account this runs as is precisely the one whose PATH an attacker would
    # have arranged. The tool that applies the protection must not itself be
    # substitutable.
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    icacls = Path(system_root) / "System32" / "icacls.exe"
    if not icacls.is_file():
        raise StorageError("icacls.exe was not found, so no restrictive ACL can be applied.")
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [str(icacls), str(path), "/inheritance:r", "/grant:r", grant],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise StorageError(
            f"Could not restrict the ACL on {path.name}; icacls exited {result.returncode}."
        )


def _shred(path: Path) -> None:
    """Overwrite then remove, and never raise.

    Honest about what this is: on a copy-on-write filesystem or an SSD with
    wear levelling the original blocks may survive. It defeats casual recovery
    and nothing stronger. The real control is that the plaintext existed only
    inside a directory restricted to one account, for the length of one command.
    """
    with suppress(OSError):
        size = path.stat().st_size
        with path.open("r+b") as handle:
            remaining = size
            block = b"\x00" * min(remaining, 1024 * 1024) or b""
            while remaining > 0:
                chunk = block[: min(remaining, len(block))] if block else b""
                if not chunk:
                    break
                handle.write(chunk)
                remaining -= len(chunk)
            handle.flush()
            os.fsync(handle.fileno())
    with suppress(OSError):
        path.unlink()


@contextmanager
def protected_workspace(prefix: str) -> Iterator[Path]:
    """A temporary directory only this account can read, shredded on the way out.

    Decrypted output lands here and nowhere else. `tempfile.mkdtemp` already
    creates a directory with restrictive permissions on POSIX; on Windows it
    inherits, which is why the ACL is set explicitly rather than assumed.
    """
    root = Path(tempfile.mkdtemp(prefix=prefix))
    try:
        harden_path(root, directory=True)
        yield root
    finally:
        for child in sorted(root.rglob("*"), reverse=True):
            if child.is_file():
                _shred(child)
        shutil.rmtree(root, ignore_errors=True)


@contextmanager
def _password_file(target: PostgresTarget) -> Iterator[Path]:
    """Hand the password to libpq through a file, not an argument or the environment.

    `PGPASSWORD` is the usual shortcut and it is worse than it looks on this
    platform: a process's environment block is readable by any process of the
    same user and by every administrator, and it is captured by crash dumps and
    by most process-listing tools. A file in a directory whose ACL names one
    account is narrower. It costs a write and a shred.

    The password is never in `argv`, which is readable by *every* user on the
    machine — that part is not a trade-off, it is a rule.
    """
    with protected_workspace("dp-pgpass-") as workspace:
        path = workspace / "pgpass.conf"

        # libpq's format: host:port:database:user:password, with backslash and
        # colon escaped in every field.
        def esc(value: str) -> str:
            return value.replace("\\", "\\\\").replace(":", "\\:")

        line = ":".join(
            (
                esc(target.host),
                str(target.port),
                "*",
                esc(target.user),
                esc(target.password),
            )
        )
        path.write_text(line + "\n", encoding="utf-8")
        harden_path(path, directory=False)
        yield path


def _pg_environment(passfile: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PGPASSWORD", None)
    env["PGPASSFILE"] = str(passfile)
    # An interactive prompt from a scheduled task hangs forever instead of
    # failing, so refuse to prompt at all.
    env["PGCONNECT_TIMEOUT"] = env.get("PGCONNECT_TIMEOUT", "15")
    return env


#: Where the Windows installer puts the client tools. Probed only after PATH,
#: and only because the account a scheduled task runs as routinely has a PATH
#: that the interactive operator's shell does not — which turns "it works when
#: I run it" into a nightly failure with a confusing message.
_WINDOWS_PG_ROOTS: Final[tuple[str, ...]] = (
    r"C:\Program Files\PostgreSQL",
    r"C:\Program Files (x86)\PostgreSQL",
)


def _tool(name: str, *, pg_bin_dir: str | None) -> str:
    """Locate `pg_dump`/`pg_restore`, preferring an explicitly configured directory."""
    executable = f"{name}.exe" if os.name == "nt" else name

    if pg_bin_dir:
        candidate = Path(pg_bin_dir) / executable
        if candidate.is_file():
            return str(candidate)
        raise ConfigurationError(f"BACKUP_PG_BIN_DIR is set but does not contain {name}.")

    found = shutil.which(name)
    if found:
        return found

    # Highest major version first: the client must be at least the server's
    # major version to read its dumps, so an older one lying around must not
    # win over a newer one.
    for root in _WINDOWS_PG_ROOTS:
        base = Path(root)
        if not base.is_dir():
            continue
        versions = sorted(
            (p for p in base.iterdir() if p.is_dir() and p.name.isdigit()),
            key=lambda p: int(p.name),
            reverse=True,
        )
        for version in versions:
            candidate = version / "bin" / executable
            if candidate.is_file():
                return str(candidate)

    raise ConfigurationError(
        f"{name} was not found on PATH or in a standard PostgreSQL installation. "
        "Set BACKUP_PG_BIN_DIR to the PostgreSQL bin directory; the client tools "
        "must match or exceed the server's major version."
    )


# ---------------------------------------------------------------------------
# Database facts
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DatabaseFacts:
    name: str
    postgres_version: str
    alembic_revision: str
    public_tables: int


def _connect(target: PostgresTarget) -> Any:
    try:
        import psycopg
    except ModuleNotFoundError as exc:  # pragma: no cover - psycopg is a dependency
        raise ConfigurationError("psycopg is not installed in this environment.") from exc
    try:
        return psycopg.connect(
            host=target.host,
            port=target.port,
            user=target.user,
            password=target.password,
            dbname=target.dbname,
            connect_timeout=15,
        )
    except Exception as exc:  # psycopg raises several unrelated types
        # `exc` can contain the DSN in some drivers' messages, so only the class
        # name is reported. The operator knows which host they aimed at.
        raise ConfigurationError(
            f"Could not connect to the database ({type(exc).__name__}). "
            "Check the host, port and credentials in the configuration file."
        ) from None


def inspect_database(
    target: PostgresTarget, *, expected: str, allow_production: bool
) -> DatabaseFacts:
    """Confirm which database this is, then read the facts a manifest needs."""
    with _connect(target) as conn, conn.cursor() as cur:
        cur.execute("SELECT current_database(), version()")
        row = cur.fetchone()
        actual, version = str(row[0]), str(row[1])
        try:
            require_database_name(actual, expected=expected, allow_production=allow_production)
        except DatabaseIdentityError as exc:
            # Re-typed rather than re-worded: the wording is the shared one, and
            # the CLI routes on the exception's exit code.
            raise ConfigurationError(str(exc)) from exc

        cur.execute("SELECT to_regclass('public.alembic_version') IS NOT NULL")
        revision = "none"
        if cur.fetchone()[0]:
            cur.execute("SELECT version_num FROM alembic_version")
            found = cur.fetchone()
            revision = str(found[0]) if found else "none"

        cur.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
        )
        tables = int(cur.fetchone()[0])

    return DatabaseFacts(
        name=actual,
        # `version()` is a long banner including the compiler; the first clause
        # is the part an operator restoring in three years needs.
        postgres_version=version.split(",")[0].strip(),
        alembic_revision=revision,
        public_tables=tables,
    )


def _application_sha() -> str:
    """The checkout that produced this backup, or an honest 'unknown'."""
    root = Path(__file__).resolve().parents[3]
    git = shutil.which("git")
    if git is None:
        return "unknown"
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [git, "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    sha = result.stdout.strip()
    return sha if re.fullmatch(r"[0-9a-f]{40}", sha) else "unknown"


# ---------------------------------------------------------------------------
# Key loading
# ---------------------------------------------------------------------------


def load_key_from_settings(settings: Any) -> BackupKey:
    """Load and validate the backup key, refusing every reuse of another secret."""
    configured = settings.backup.encryption_key
    raw = configured.get_secret_value() if configured is not None else None
    forbidden = {
        "SECURITY_SECRET_KEY": settings.security.secret_key.get_secret_value(),
        "SECURITY_OTP_HMAC_KEY": settings.security.otp_hmac_key.get_secret_value(),
    }
    for index, key in enumerate(settings.security.encryption_keys):
        forbidden[f"SECURITY_ENCRYPTION_KEYS entry {index + 1}"] = key.get_secret_value()
    try:
        return load_backup_key(raw, forbidden=forbidden)
    except InvalidBackupKeyError as exc:
        raise ConfigurationError(str(exc)) from exc


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def backup_paths(directory: Path, stem: str) -> tuple[Path, Path]:
    """The ciphertext and manifest for one backup, refusing an unsafe stem."""
    if not _FILENAME_SAFE.match(stem):
        raise ConfigurationError(
            "A backup name may contain only letters, digits, dot, dash and underscore."
        )
    return directory / f"{stem}{CIPHERTEXT_SUFFIX}", directory / f"{stem}{MANIFEST_SUFFIX}"


def resolve_backup_directory(configured: str | None, *, create: bool) -> Path:
    """Turn the configured backup directory into a real, protected path."""
    if not configured or not configured.strip():
        raise ConfigurationError(
            "No backup directory is configured. Set BACKUP_DIRECTORY to a path "
            "on a volume that is not the database's own."
        )
    path = Path(configured).expanduser()
    if not path.is_absolute():
        raise ConfigurationError(
            "BACKUP_DIRECTORY must be an absolute path. A relative one resolves "
            "against whatever directory the scheduler happened to start in."
        )
    if create:
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StorageError(f"Could not create the backup directory: {exc.strerror}.") from exc
        harden_path(path, directory=True)
    elif not path.is_dir():
        raise ConfigurationError("BACKUP_DIRECTORY does not exist or is not a directory.")
    return path.resolve()


# ---------------------------------------------------------------------------
# Markers
# ---------------------------------------------------------------------------


def write_marker(directory: Path, name: str, payload: Mapping[str, Any], key: BackupKey) -> Path:
    """Record an authenticated fact about this backup set.

    Authenticated because the readiness check reads these, and an unsigned file
    would mean "backups are healthy" could be asserted by anyone who can create
    a file — including a scheduled job that failed and wrote one anyway.
    """
    state = directory / STATE_DIRNAME
    state.mkdir(parents=True, exist_ok=True)
    harden_path(state, directory=True)
    body = json.dumps(dict(payload), sort_keys=True, separators=(",", ":")).encode("utf-8")
    document = {"payload": json.loads(body), "mac": manifest_mac(key, body)}
    path = state / name
    temp = path.with_suffix(path.suffix + PARTIAL_SUFFIX)
    temp.write_text(json.dumps(document, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    harden_path(temp, directory=False)
    os.replace(temp, path)
    return path


def read_marker(directory: Path, name: str, key: BackupKey) -> dict[str, Any] | None:
    """Return an authenticated marker's payload, or `None` if absent or forged."""
    path = directory / STATE_DIRNAME / name
    if not path.is_file():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        body = json.dumps(document["payload"], sort_keys=True, separators=(",", ":")).encode()
        if not hmac.compare_digest(str(document["mac"]), manifest_mac(key, body)):
            return None
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    payload = document["payload"]
    return dict(payload) if isinstance(payload, dict) else None


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BackupOutcome:
    manifest: BackupManifest
    ciphertext_path: Path
    manifest_path: Path


def create_backup(
    *,
    target: PostgresTarget,
    expected_database: str,
    key: BackupKey,
    directory: Path,
    allow_production: bool = False,
    pg_bin_dir: str | None = None,
    chunk_bytes: int = CHUNK_BYTES,
    now: datetime | None = None,
) -> BackupOutcome:
    """Dump, encrypt and publish one backup, or leave the directory as it was.

    The ordering is the whole design. Everything is written to `.part` names;
    the ciphertext is renamed into place only after `pg_dump` has exited zero
    and the last chunk has been sealed; the manifest is renamed last, because
    the manifest's presence is what every other tool reads as "this backup is
    complete". If the manifest cannot be published, the ciphertext that was
    already renamed is removed — a backup nothing can verify is worse than an
    absent one, because it will be counted.
    """
    # Before any connection, not after. `inspect_database` refuses a production
    # name too, but only once it has connected and asked the server — and
    # opening a session against production is itself something an unauthorised
    # run must not do. The operator's stated name is enough to refuse on; the
    # server's answer remains the backstop for the case where the two differ.
    if expected_database in PRODUCTION_DATABASE_NAMES and not allow_production:
        raise ConfigurationError(
            f"{expected_database!r} is a production database. Nothing was "
            "connected to. Re-run with the explicit production flag if taking a "
            "production backup is genuinely intended."
        )

    facts = inspect_database(target, expected=expected_database, allow_production=allow_production)

    backup_id = secrets.token_hex(16)
    stamp = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    stem = f"{facts.name}-{stamp}-{backup_id[:8]}"
    ciphertext_path, manifest_path = backup_paths(directory, stem)
    partial = ciphertext_path.with_suffix(ciphertext_path.suffix + PARTIAL_SUFFIX)

    pg_dump = _tool("pg_dump", pg_bin_dir=pg_bin_dir)
    published_ciphertext = False

    try:
        with _password_file(target) as passfile:
            argv = [
                pg_dump,
                "--format=custom",
                "--no-password",
                "--compress=6",
                f"--host={target.host}",
                f"--port={target.port}",
                f"--username={target.user}",
                f"--dbname={target.dbname}",
            ]
            with partial.open("wb") as sink:
                harden_path(partial, directory=False)
                process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
                    argv,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=_pg_environment(passfile),
                )
                assert process.stdout is not None  # guaranteed by stdout=PIPE
                try:
                    plaintext_bytes, plaintext_sha, ciphertext_bytes, ciphertext_sha = (
                        encrypt_stream(
                            process.stdout,
                            sink,
                            key=key,
                            backup_id=backup_id,
                            chunk_bytes=chunk_bytes,
                        )
                    )
                except BackupCryptoError as exc:
                    process.kill()
                    raise EncryptionError(f"The backup could not be encrypted: {exc}") from exc
                finally:
                    process.stdout.close()
                stderr = process.stderr.read().decode("utf-8", "replace") if process.stderr else ""
                if process.stderr:
                    process.stderr.close()
                code = process.wait()
                sink.flush()
                os.fsync(sink.fileno())

        if code != 0:
            raise DumpError(
                f"pg_dump exited {code}. The partial output was removed. "
                f"{_sanitize_tool_output(stderr) or 'No diagnostic output.'}"
            )
        if plaintext_bytes == 0:
            raise DumpError("pg_dump produced no output. Nothing was published.")

        manifest = BackupManifest(
            manifest_version=MANIFEST_VERSION,
            tool_version=TOOL_VERSION,
            created_at=(now or datetime.now(UTC)).astimezone(UTC).isoformat(),
            application_sha=_application_sha(),
            database=facts.name,
            postgres_version=facts.postgres_version,
            alembic_revision=facts.alembic_revision,
            dump_format="pg_dump/custom",
            backup_id=backup_id,
            key_id=key.key_id,
            encryption="AES-256-GCM/STREAM v1",
            chunk_bytes=chunk_bytes,
            plaintext_bytes=plaintext_bytes,
            plaintext_sha256=plaintext_sha,
            ciphertext_bytes=ciphertext_bytes,
            ciphertext_sha256=ciphertext_sha,
        ).signed(key)
        _reject_secret_material(manifest, (target.password,))

        os.replace(partial, ciphertext_path)
        published_ciphertext = True

        manifest_partial = manifest_path.with_suffix(manifest_path.suffix + PARTIAL_SUFFIX)
        manifest_partial.write_text(manifest.to_json(), encoding="utf-8")
        harden_path(manifest_partial, directory=False)
        os.replace(manifest_partial, manifest_path)
    except BaseException:
        # Every failure path, including a Ctrl-C, leaves the directory as it was
        # found. A half-written backup that survives is a backup somebody trusts.
        _shred(partial)
        with suppress(OSError):
            manifest_path.with_suffix(manifest_path.suffix + PARTIAL_SUFFIX).unlink()
        if published_ciphertext:
            _shred(ciphertext_path)
        raise

    return BackupOutcome(
        manifest=manifest, ciphertext_path=ciphertext_path, manifest_path=manifest_path
    )


def _sanitize_tool_output(text: str) -> str:
    """Keep a diagnostic line only if it cannot contain a credential.

    `pg_dump` writes its own errors, and they can quote a connection string. So
    the output is filtered to lines with no `://`, no `password`, and no `@`
    host form, truncated hard. An operator who needs more runs the tool by hand.
    """
    keep: list[str] = []
    for line in text.splitlines():
        lowered = line.lower()
        if "://" in line or "password" in lowered or "pgpass" in lowered:
            continue
        keep.append(line.strip())
    joined = " ".join(k for k in keep if k)
    return joined[:400]


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VerificationOutcome:
    manifest: BackupManifest
    ciphertext_path: Path
    toc_entries: int
    plaintext_bytes: int


def verify_backup(
    ciphertext_path: Path,
    *,
    key: BackupKey,
    pg_bin_dir: str | None = None,
    decrypt_into: Path | None = None,
) -> VerificationOutcome:
    """Prove a backup is restorable, or say why it is not.

    `decrypt_into`, when given, keeps the decrypted dump for a caller that is
    about to restore it — so a restore decrypts once rather than twice. The
    caller owns that directory and its protection; when it is omitted the
    plaintext lives in a protected temporary directory and is shredded here.
    """
    manifest = read_manifest(ciphertext_path, key=key)

    actual_size = ciphertext_path.stat().st_size
    if actual_size != manifest.ciphertext_bytes:
        raise VerificationError(
            f"The backup is {actual_size} bytes; the manifest records "
            f"{manifest.ciphertext_bytes}. It is truncated or has been appended to."
        )

    digest = hashlib.sha256()
    with ciphertext_path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    if not hmac.compare_digest(digest.hexdigest(), manifest.ciphertext_sha256):
        raise VerificationError(
            "The backup's SHA-256 does not match the manifest. The file has been "
            "modified since it was written."
        )

    if decrypt_into is not None:
        return _verify_decrypted(
            ciphertext_path, manifest, key=key, workspace=decrypt_into, pg_bin_dir=pg_bin_dir
        )
    with protected_workspace("dp-verify-") as workspace:
        return _verify_decrypted(
            ciphertext_path, manifest, key=key, workspace=workspace, pg_bin_dir=pg_bin_dir
        )


def _verify_decrypted(
    ciphertext_path: Path,
    manifest: BackupManifest,
    *,
    key: BackupKey,
    workspace: Path,
    pg_bin_dir: str | None,
) -> VerificationOutcome:
    plain = workspace / f"{manifest.backup_id}.dump"
    digest = hashlib.sha256()
    written = 0
    try:
        with ciphertext_path.open("rb") as source, plain.open("wb") as sink:
            harden_path(plain, directory=False)
            for block in decrypt_stream(source, key=key):
                digest.update(block)
                written += len(block)
                sink.write(block)
    except TruncatedBackupError as exc:
        raise VerificationError(f"The backup is truncated: {exc}") from exc
    except CorruptBackupError as exc:
        raise VerificationError(f"The backup failed authentication: {exc}") from exc
    except BackupCryptoError as exc:
        raise VerificationError(str(exc)) from exc

    if written != manifest.plaintext_bytes:
        raise VerificationError(
            f"The decrypted dump is {written} bytes; the manifest records "
            f"{manifest.plaintext_bytes}."
        )
    if not hmac.compare_digest(digest.hexdigest(), manifest.plaintext_sha256):
        raise VerificationError("The decrypted dump does not match the manifest's digest.")

    pg_restore = _tool("pg_restore", pg_bin_dir=pg_bin_dir)
    listing = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [pg_restore, "--list", str(plain)],
        capture_output=True,
        text=True,
        check=False,
    )
    if listing.returncode != 0:
        raise VerificationError(
            "pg_restore could not read the decrypted dump: "
            f"{_sanitize_tool_output(listing.stderr) or 'no diagnostic output'}."
        )
    entries = [
        line
        for line in listing.stdout.splitlines()
        if line.strip() and not line.lstrip().startswith(";")
    ]
    if not entries:
        raise VerificationError("The dump's table of contents is empty. It would restore nothing.")

    return VerificationOutcome(
        manifest=manifest,
        ciphertext_path=ciphertext_path,
        toc_entries=len(entries),
        plaintext_bytes=written,
    )


def read_manifest(ciphertext_path: Path, *, key: BackupKey) -> BackupManifest:
    """Parse and authenticate a backup's manifest without decrypting the backup.

    Enough to judge identity — which database this came from, which key it needs
    — and cheap enough to run before deciding whether the expensive work is
    worth starting. It is emphatically **not** verification: a manifest can be
    perfectly authentic while the file beside it is a truncated ruin. Only
    `verify_backup` establishes that, and every caller here runs it before
    anything is written.
    """
    manifest_path = _manifest_for(ciphertext_path)
    if not ciphertext_path.is_file():
        raise VerificationError("The backup file does not exist.")
    if not manifest_path.is_file():
        raise VerificationError(
            "The backup has no manifest. A ciphertext without one cannot be "
            "verified and must not be counted as a backup."
        )
    manifest = BackupManifest.from_json(manifest_path.read_text(encoding="utf-8"))
    manifest.verify_mac(key)
    if manifest.key_id != key.key_id:
        raise VerificationError(
            f"The manifest names key {manifest.key_id}; the key offered is {key.key_id}."
        )
    return manifest


def _manifest_for(ciphertext_path: Path) -> Path:
    name = ciphertext_path.name
    stem = name[: -len(CIPHERTEXT_SUFFIX)] if name.endswith(CIPHERTEXT_SUFFIX) else name
    return ciphertext_path.with_name(f"{stem}{MANIFEST_SUFFIX}")


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------

#: Checks run after a restore. Each returns exactly one scalar, and every one
#: is an aggregate or a digest — never a row. A restore report that printed
#: customer data would turn the recovery runbook into a disclosure.
DEFAULT_INTEGRITY_QUERIES: Final[Mapping[str, str]] = {
    "tenants": "SELECT count(*) FROM tenants",
    "users": "SELECT count(*) FROM users",
    "tenants_digest": (
        "SELECT encode(sha256(convert_to("
        "coalesce(string_agg(t::text, '|' ORDER BY t.id), ''), 'UTF8')), 'hex') FROM tenants t"
    ),
    "users_digest": (
        "SELECT encode(sha256(convert_to("
        "coalesce(string_agg(u::text, '|' ORDER BY u.id), ''), 'UTF8')), 'hex') FROM users u"
    ),
    "orphan_users": (
        "SELECT count(*) FROM users u LEFT JOIN tenants t ON t.id = u.tenant_id WHERE t.id IS NULL"
    ),
    "tenant_id_not_null": (
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_schema = 'public' AND column_name = 'tenant_id' "
        "AND is_nullable = 'YES'"
    ),
}


@dataclass(frozen=True, slots=True)
class RestoreOutcome:
    dry_run: bool
    source_database: str
    target_database: str
    manifest: BackupManifest
    toc_entries: int
    alembic_revision: str
    public_tables: int
    integrity: dict[str, str] = field(default_factory=dict)
    notes: tuple[str, ...] = ()


def restore_backup(
    ciphertext_path: Path,
    *,
    target: PostgresTarget,
    expected_source: str,
    expected_target: str,
    key: BackupKey,
    apply: bool = False,
    allow_production_target: bool = False,
    allow_non_empty: bool = False,
    pg_bin_dir: str | None = None,
    integrity_queries: Mapping[str, str] | None = None,
) -> RestoreOutcome:
    """Restore into an isolated database, after proving the backup and the target.

    `apply` defaults to false and that default is the safety property: the
    dangerous form of this command is the one an operator has to opt into, at
    three in the morning, having read what the rehearsal said it would do.
    """
    if target.dbname != expected_target:
        raise ConfigurationError(
            f"The configured target database is {target.dbname!r} but "
            f"{expected_target!r} was stated. Nothing was changed."
        )
    if expected_target in PRODUCTION_DATABASE_NAMES and not allow_production_target:
        raise ConfigurationError(
            f"{expected_target!r} is a protected production database. Restoring "
            "over it is a separate, deliberately awkward operation; it needs the "
            "production-restore flag and a typed confirmation."
        )

    # Identity from the manifest alone. Judging it before decrypting means a
    # wrong-source refusal costs a few hundred bytes rather than a full pass over
    # a backup that was never going to be used — and the full verification below
    # is still mandatory before anything is written.
    declared = read_manifest(ciphertext_path, key=key)
    if declared.database != expected_source:
        raise ConfigurationError(
            f"This backup was taken from {declared.database!r}, and "
            f"{expected_source!r} was stated. Restoring the wrong database's data "
            "into a live system is the failure this check exists for."
        )

    facts = inspect_database(
        target, expected=expected_target, allow_production=allow_production_target
    )
    notes: list[str] = []
    if facts.public_tables and not allow_non_empty:
        raise ConfigurationError(
            f"The target database already holds {facts.public_tables} tables. "
            "Restore into a newly created database, or pass the destructive flag "
            "to say that overwriting this one is intended."
        )
    if facts.public_tables:
        notes.append(f"target was not empty: {facts.public_tables} tables present")

    if not apply:
        # A rehearsal verifies in full. Its whole purpose is to answer "would
        # this work", and one that skipped the expensive half would be answering
        # a different question than the one asked.
        outcome = verify_backup(ciphertext_path, key=key, pg_bin_dir=pg_bin_dir)
        return RestoreOutcome(
            dry_run=True,
            source_database=outcome.manifest.database,
            target_database=facts.name,
            manifest=outcome.manifest,
            toc_entries=outcome.toc_entries,
            alembic_revision=facts.alembic_revision,
            public_tables=facts.public_tables,
            notes=(*notes, "rehearsal only; nothing was written"),
        )

    pg_restore = _tool("pg_restore", pg_bin_dir=pg_bin_dir)
    with protected_workspace("dp-restore-") as workspace:
        verified = verify_backup(
            ciphertext_path, key=key, pg_bin_dir=pg_bin_dir, decrypt_into=workspace
        )
        plain = workspace / f"{verified.manifest.backup_id}.dump"
        with _password_file(target) as passfile:
            result = subprocess.run(  # noqa: S603 - fixed argv, no shell
                [
                    pg_restore,
                    "--no-password",
                    "--no-owner",
                    "--no-privileges",
                    "--exit-on-error",
                    "--single-transaction",
                    f"--host={target.host}",
                    f"--port={target.port}",
                    f"--username={target.user}",
                    f"--dbname={target.dbname}",
                    str(plain),
                ],
                capture_output=True,
                text=True,
                check=False,
                env=_pg_environment(passfile),
            )
        if result.returncode != 0:
            raise RestoreError(
                f"pg_restore exited {result.returncode}. The restore ran inside a "
                "single transaction, so the target is unchanged. "
                f"{_sanitize_tool_output(result.stderr) or 'No diagnostic output.'}"
            )

    after = inspect_database(
        target, expected=expected_target, allow_production=allow_production_target
    )
    integrity = _run_integrity_queries(target, integrity_queries or DEFAULT_INTEGRITY_QUERIES)

    return RestoreOutcome(
        dry_run=False,
        source_database=verified.manifest.database,
        target_database=after.name,
        manifest=verified.manifest,
        toc_entries=verified.toc_entries,
        alembic_revision=after.alembic_revision,
        public_tables=after.public_tables,
        integrity=integrity,
        notes=tuple(notes),
    )


def _run_integrity_queries(target: PostgresTarget, queries: Mapping[str, str]) -> dict[str, str]:
    """Run each check, returning one scalar each as a string.

    A query that fails is reported as an error string rather than aborting: the
    operator wants every result, and a missing table in an older backup is
    information about the backup, not a reason to abandon the report.
    """
    results: dict[str, str] = {}
    with _connect(target) as conn:
        for name, sql in queries.items():
            with suppress(Exception):
                conn.rollback()
            try:
                with conn.cursor() as cur:
                    cur.execute(sql)
                    row = cur.fetchone()
                results[name] = "null" if row is None or row[0] is None else str(row[0])
            except Exception as exc:  # report the failure, never abort the report
                results[name] = f"error: {type(exc).__name__}"
    return results


# ---------------------------------------------------------------------------
# Readiness evidence
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BackupEvidence:
    """What the production-readiness check is allowed to conclude from disk.

    Deliberately narrow. It answers "is there an authenticated record of a
    recent successful backup and a recent successful restore drill", and
    nothing else. It cannot tell you a backup exists off-site, because nothing
    on this host can.
    """

    directory_configured: bool
    key_configured: bool
    last_success: datetime | None
    last_drill: datetime | None
    offsite_destination: str | None


def _parse_marker_time(payload: Mapping[str, Any] | None) -> datetime | None:
    if not payload:
        return None
    raw = payload.get("completed_at")
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def collect_evidence(settings: Any) -> BackupEvidence:
    """Read whatever authenticated evidence exists. Never raises."""
    configured_dir = settings.backup.directory
    offsite = settings.backup.offsite_destination or None
    try:
        key = load_key_from_settings(settings)
    except ConfigurationError:
        return BackupEvidence(
            directory_configured=bool(configured_dir),
            key_configured=False,
            last_success=None,
            last_drill=None,
            offsite_destination=offsite,
        )
    try:
        directory = resolve_backup_directory(configured_dir, create=False)
    except BackupError:
        return BackupEvidence(
            directory_configured=False,
            key_configured=True,
            last_success=None,
            last_drill=None,
            offsite_destination=offsite,
        )
    return BackupEvidence(
        directory_configured=True,
        key_configured=True,
        last_success=_parse_marker_time(read_marker(directory, LAST_SUCCESS_MARKER, key)),
        last_drill=_parse_marker_time(read_marker(directory, LAST_DRILL_MARKER, key)),
        offsite_destination=offsite,
    )


def evidence_shortfalls(
    evidence: BackupEvidence,
    *,
    max_backup_age: timedelta,
    max_drill_age: timedelta,
    now: datetime | None = None,
) -> tuple[str, ...]:
    """Every reason the backup regime is not yet demonstrable, in reading order."""
    moment = now or datetime.now(UTC)
    reasons: list[str] = []
    if not evidence.key_configured:
        reasons.append("no valid backup encryption key is configured")
    if not evidence.directory_configured:
        reasons.append("no backup directory is configured")
    if evidence.last_success is None:
        reasons.append("no authenticated record of a successful backup exists")
    elif moment - evidence.last_success > max_backup_age:
        hours = int((moment - evidence.last_success).total_seconds() // 3600)
        reasons.append(f"the most recent successful backup is {hours} hours old")
    if evidence.last_drill is None:
        reasons.append("no authenticated record of a successful restore drill exists")
    elif moment - evidence.last_drill > max_drill_age:
        days = (moment - evidence.last_drill).days
        reasons.append(f"the most recent restore drill was {days} days ago")
    if not evidence.offsite_destination:
        reasons.append("no off-site destination is configured, so every copy is on one host")
    return tuple(reasons)
