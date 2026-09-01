"""Decide which backups may be deleted, and refuse to delete anything else.

This is the most dangerous code in the backup system. Everything else can fail
by producing nothing; retention fails by destroying the thing that was supposed
to save you, and it does it on a schedule, unattended, at the exact moment
nobody is watching. So it is written to refuse rather than to act.

**What it will not do, under any policy.**

* Delete the newest backup that passes its integrity check. Whatever the dates
  say, the most recent good copy stays.
* Delete when only one good copy exists. A policy that would leave zero is a
  policy that is wrong, not an instruction.
* Delete anything it could not verify. An unreadable file is quarantined and
  reported — it is evidence about a failure, and deleting evidence is how the
  failure stays invisible.
* Delete anything outside the one configured directory, reached by any route.
  Every candidate's resolved parent must be the resolved backup directory, and
  a symlink or a Windows reparse point anywhere on the way disqualifies it.
* Operate on a filesystem root, a drive root, a home directory, a source
  checkout, or a path shallow enough to be one of those by accident.

**What "verified" means here, precisely.** Retention checks that a file is
byte-identical to what `create_backup` published, that its manifest is
authentic under the backup key, and that both recorded sizes and the SHA-256
agree. That is an integrity and authenticity proof, and it is *not* proof of
restorability — only decrypting the whole stream and parsing the dump's table
of contents is that, which is what `--deep` does and what the restore drill
does. Running the deep check on every file on every prune would read the entire
archive daily; the honest position is to say which check ran, and the report
does.
"""

from __future__ import annotations

import hashlib
import hmac
import shutil
import stat
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

from app.core.backup_crypto import BackupKey
from app.services.database_backup import (
    CIPHERTEXT_SUFFIX,
    MANIFEST_SUFFIX,
    PARTIAL_SUFFIX,
    STATE_DIRNAME,
    BackupManifest,
    ConfigurationError,
    StorageError,
    VerificationError,
    verify_backup,
)

__all__ = [
    "QUARANTINE_DIRNAME",
    "BackupEntry",
    "RetentionPlan",
    "RetentionPolicy",
    "apply_plan",
    "assert_safe_backup_directory",
    "plan_retention",
    "scan_directory",
]

#: Unverifiable files are moved here rather than removed. Named as a directory
#: an operator will notice.
QUARANTINE_DIRNAME: Final[str] = "quarantine"

#: A backup directory this shallow is almost certainly a mistake — `C:\backups`
#: is fine, `C:\` is a catastrophe. Two components below the anchor means the
#: path names a purpose, not a volume.
_MIN_PATH_DEPTH: Final[int] = 2

#: Directories that are never a backup directory, whatever the configuration
#: says. Compared case-insensitively against the resolved path and its parents.
_FORBIDDEN_ROOTS: Final[tuple[str, ...]] = (
    "windows",
    "program files",
    "program files (x86)",
    "system32",
    "etc",
    "bin",
    "sbin",
    "usr",
    "boot",
    "dev",
    "proc",
    "sys",
)

#: A directory containing any of these is a source checkout or a project, not a
#: backup store. Deleting inside one is how a prune run removes a repository.
_WORKSPACE_MARKERS: Final[tuple[str, ...]] = (
    ".git",
    "pyproject.toml",
    "package.json",
    "go.mod",
    "Cargo.toml",
)


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    """A grandfather-father-son policy, in the units an operator thinks in.

    The proposed values are **for review, not settled**. How long a backup of
    1,268 tenants' personal data may be kept is a data-protection decision with
    a lawful-basis argument behind it, and this module has no opinion on it —
    it only makes whatever is decided enforceable and auditable.
    """

    daily_days: int = 14
    weekly_weeks: int = 8
    monthly_months: int = 12

    def __post_init__(self) -> None:
        for name in ("daily_days", "weekly_weeks", "monthly_months"):
            if getattr(self, name) < 1:
                raise ConfigurationError(f"{name} must be at least 1.")


@dataclass(frozen=True, slots=True)
class BackupEntry:
    """One candidate file, and what could be established about it."""

    ciphertext: Path
    manifest: Path | None
    created_at: datetime | None
    manifest_data: BackupManifest | None
    ok: bool
    reason: str

    @property
    def name(self) -> str:
        return self.ciphertext.name


@dataclass(frozen=True, slots=True)
class RetentionPlan:
    """What would happen, named deterministically so two runs can be diffed."""

    retained: tuple[str, ...] = ()
    deletable: tuple[str, ...] = ()
    quarantine: tuple[str, ...] = ()
    reasons: dict[str, str] = field(default_factory=dict)
    deep: bool = False
    refusals: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------


def _is_reparse_point(path: Path) -> bool:
    """Whether this path is a symlink, junction or other reparse point.

    `Path.is_symlink` covers POSIX symlinks and, on modern Python, Windows
    symlinks — but not directory junctions, which are the form an attacker or a
    tired administrator is most likely to leave behind on Windows. The file
    attribute catches both.
    """
    try:
        info = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def assert_safe_backup_directory(raw: Path) -> Path:
    """Return the resolved directory, or refuse to treat it as one.

    Every check here answers a way a prune run has actually destroyed something
    somewhere: a relative path resolved against the scheduler's directory, an
    environment variable that expanded to empty and left the drive root, a
    junction someone created while moving the archive, a backup directory that
    was really the repository.
    """
    if not raw.is_absolute():
        raise ConfigurationError(
            "The backup directory must be an absolute path. A relative one "
            "resolves against whatever directory the scheduler started in."
        )
    if _is_reparse_point(raw):
        raise ConfigurationError(
            "The backup directory is a symlink or junction. Deletion follows it "
            "to somewhere this tool never checked, so it is refused."
        )

    resolved = raw.resolve()
    if not resolved.is_dir():
        raise ConfigurationError("The backup directory does not exist or is not a directory.")

    parts = [p for p in resolved.parts[1:] if p not in ("/", "\\")]
    if len(parts) < _MIN_PATH_DEPTH:
        raise ConfigurationError(
            f"{resolved.name or 'the drive root'!r} is too close to the "
            "filesystem root to be a backup directory. Use a dedicated path at "
            f"least {_MIN_PATH_DEPTH} levels deep."
        )

    home = Path.home().resolve()
    if resolved == home:
        raise ConfigurationError(
            "The backup directory is the home directory itself. Use a dedicated subdirectory."
        )

    lowered = {p.lower() for p in resolved.parts}
    forbidden = lowered.intersection(_FORBIDDEN_ROOTS)
    if forbidden:
        raise ConfigurationError(
            f"The backup directory is inside a system directory "
            f"({sorted(forbidden)[0]!r}). It must be a dedicated location."
        )

    for marker in _WORKSPACE_MARKERS:
        if (resolved / marker).exists():
            raise ConfigurationError(
                f"The backup directory contains {marker!r}, so it is a source "
                "checkout rather than a backup store."
            )

    return resolved


def assert_contained(candidate: Path, directory: Path) -> Path:
    """Refuse a candidate that is not a plain file directly inside `directory`."""
    if _is_reparse_point(candidate):
        raise ConfigurationError(f"{candidate.name} is a symlink or junction.")
    resolved = candidate.resolve()
    if resolved.parent != directory:
        raise ConfigurationError(f"{candidate.name} resolves outside the backup directory.")
    if not resolved.is_file():
        raise ConfigurationError(f"{candidate.name} is not a regular file.")
    return resolved


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------


def _integrity_ok(
    ciphertext: Path, manifest_path: Path, key: BackupKey
) -> tuple[bool, str, BackupManifest | None]:
    """Cheap authenticity and integrity check: manifest MAC, size, digest."""
    try:
        manifest = BackupManifest.from_json(manifest_path.read_text(encoding="utf-8"))
        manifest.verify_mac(key)
    except (OSError, VerificationError) as exc:
        return False, f"manifest rejected: {exc}", None

    if manifest.key_id != key.key_id:
        return False, "written for a different backup key", manifest
    size = ciphertext.stat().st_size
    if size != manifest.ciphertext_bytes:
        return False, "size does not match the manifest", manifest

    digest = hashlib.sha256()
    with ciphertext.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    if not hmac.compare_digest(digest.hexdigest(), manifest.ciphertext_sha256):
        return False, "SHA-256 does not match the manifest", manifest
    return True, "integrity and manifest authenticity confirmed", manifest


def scan_directory(
    directory: Path,
    key: BackupKey,
    *,
    deep: bool = False,
    pg_bin_dir: str | None = None,
) -> tuple[BackupEntry, ...]:
    """Classify every file in the backup directory, refusing unsafe candidates."""
    directory = assert_safe_backup_directory(directory)
    entries: list[BackupEntry] = []

    for child in sorted(directory.iterdir(), key=lambda p: p.name):
        if child.is_dir():
            continue
        if child.name.endswith(PARTIAL_SUFFIX):
            entries.append(
                BackupEntry(child, None, None, None, False, "in-progress or abandoned output")
            )
            continue
        if child.name.endswith(MANIFEST_SUFFIX):
            # Accounted for through its ciphertext; an orphan is caught below.
            partner = child.with_name(child.name[: -len(MANIFEST_SUFFIX)] + CIPHERTEXT_SUFFIX)
            if not partner.is_file():
                entries.append(
                    BackupEntry(child, None, None, None, False, "manifest with no backup file")
                )
            continue
        if not child.name.endswith(CIPHERTEXT_SUFFIX):
            entries.append(BackupEntry(child, None, None, None, False, "not a backup file"))
            continue

        try:
            ciphertext = assert_contained(child, directory)
        except ConfigurationError as exc:
            entries.append(BackupEntry(child, None, None, None, False, str(exc)))
            continue

        manifest_path = ciphertext.with_name(
            ciphertext.name[: -len(CIPHERTEXT_SUFFIX)] + MANIFEST_SUFFIX
        )
        if not manifest_path.is_file():
            entries.append(
                BackupEntry(ciphertext, None, None, None, False, "no manifest, so unverifiable")
            )
            continue

        ok, reason, manifest = _integrity_ok(ciphertext, manifest_path, key)
        created: datetime | None = None
        if manifest is not None:
            try:
                parsed = datetime.fromisoformat(manifest.created_at)
                created = parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
            except ValueError:
                ok, reason = False, "manifest timestamp is not a valid instant"

        if ok and deep:
            try:
                verify_backup(ciphertext, key=key, pg_bin_dir=pg_bin_dir)
                reason = "fully verified, including a dump table-of-contents read"
            except (VerificationError, ConfigurationError) as exc:
                ok, reason = False, f"deep verification failed: {exc}"

        entries.append(BackupEntry(ciphertext, manifest_path, created, manifest, ok, reason))

    return tuple(entries)


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def _period_keys(moment: datetime) -> tuple[str, str]:
    iso = moment.isocalendar()
    return f"{iso.year:04d}W{iso.week:02d}", f"{moment.year:04d}-{moment.month:02d}"


def plan_retention(
    entries: Sequence[BackupEntry],
    policy: RetentionPolicy,
    *,
    now: datetime | None = None,
    deep: bool = False,
) -> RetentionPlan:
    """Decide, without touching anything.

    Retention is the union of three independent claims — recent enough, newest
    in its week, newest in its month — and a backup survives if *any* of them
    holds. Intersection would be the obvious-looking mistake: it deletes a
    backup that two of the three policies wanted to keep.
    """
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    reasons: dict[str, str] = {}
    refusals: list[str] = []

    quarantine = tuple(sorted(e.name for e in entries if not e.ok))
    for entry in entries:
        if not entry.ok:
            reasons[entry.name] = entry.reason

    good = sorted(
        (e for e in entries if e.ok and e.created_at is not None),
        key=lambda e: (e.created_at or moment, e.name),
        reverse=True,
    )
    if not good:
        return RetentionPlan(
            quarantine=quarantine,
            reasons=reasons,
            deep=deep,
            refusals=("nothing was deleted: no backup passed its integrity check",),
        )

    keep: dict[str, str] = {}

    # The newest good backup, unconditionally. This is checked first so that it
    # is true even if every policy window is somehow zero-length.
    keep[good[0].name] = "newest backup that passed its integrity check"

    if len(good) == 1:
        refusals.append("nothing was deleted: only one backup passed its integrity check")

    daily_cutoff = moment - timedelta(days=policy.daily_days)
    weekly_cutoff = moment - timedelta(weeks=policy.weekly_weeks)
    monthly_cutoff = moment - timedelta(days=31 * policy.monthly_months)

    seen_weeks: set[str] = set()
    seen_months: set[str] = set()
    for entry in good:
        created = entry.created_at
        assert created is not None  # filtered above, but mypy cannot see that
        week, month = _period_keys(created)
        if created >= daily_cutoff:
            keep.setdefault(entry.name, f"within the {policy.daily_days}-day daily window")
        if created >= weekly_cutoff and week not in seen_weeks:
            seen_weeks.add(week)
            keep.setdefault(entry.name, f"newest in week {week}")
        if created >= monthly_cutoff and month not in seen_months:
            seen_months.add(month)
            keep.setdefault(entry.name, f"newest in month {month}")

    deletable: list[str] = []
    for entry in good:
        if entry.name in keep:
            reasons[entry.name] = keep[entry.name]
            continue
        if len(good) == 1:
            continue
        deletable.append(entry.name)
        reasons[entry.name] = "outside every retention window"

    return RetentionPlan(
        retained=tuple(sorted(keep)),
        deletable=tuple(sorted(deletable)),
        quarantine=quarantine,
        reasons=reasons,
        deep=deep,
        refusals=tuple(refusals),
    )


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------


def apply_plan(
    directory: Path,
    plan: RetentionPlan,
    entries: Iterable[BackupEntry],
    *,
    apply: bool = False,
) -> tuple[str, ...]:
    """Carry out a plan, or describe it. Returns the actions taken, in order.

    Nothing here recomputes the decision. The plan is the decision; this
    function's only job is to be unable to exceed it, which is why it matches
    candidates by name against the plan's own tuples and refuses anything else.
    """
    directory = assert_safe_backup_directory(directory)
    by_name = {e.name: e for e in entries}
    actions: list[str] = []

    for name in plan.quarantine:
        entry = by_name.get(name)
        if entry is None:
            continue
        if not apply:
            actions.append(f"would quarantine {name}")
            continue
        target = directory / QUARANTINE_DIRNAME
        target.mkdir(exist_ok=True)
        try:
            source = assert_contained(entry.ciphertext, directory)
        except ConfigurationError as exc:
            actions.append(f"refused to quarantine {name}: {exc}")
            continue
        shutil.move(str(source), str(target / name))
        actions.append(f"quarantined {name}")

    for name in plan.deletable:
        entry = by_name.get(name)
        if entry is None or not entry.ok:
            actions.append(f"refused to delete {name}: not an entry this plan verified")
            continue
        if not apply:
            actions.append(f"would delete {name} and its manifest")
            continue
        try:
            ciphertext = assert_contained(entry.ciphertext, directory)
            manifest = (
                assert_contained(entry.manifest, directory) if entry.manifest is not None else None
            )
        except ConfigurationError as exc:
            actions.append(f"refused to delete {name}: {exc}")
            continue
        try:
            ciphertext.unlink()
            if manifest is not None:
                manifest.unlink()
        except OSError as exc:
            raise StorageError(f"Could not delete {name}: {exc.strerror}.") from exc
        actions.append(f"deleted {name} and its manifest")

    return tuple(actions)


def state_directory_untouched(directory: Path) -> bool:
    """Whether the marker directory survived a prune. Used by the tests.

    The markers are what the readiness check reads. A retention run that
    removed them would silently turn "backups are healthy" into "no evidence",
    which looks identical to never having run — so nothing in this module ever
    enumerates or deletes inside it, and `scan_directory` skips directories
    entirely.
    """
    return (directory / STATE_DIRNAME).is_dir()


def default_policy(settings: object) -> RetentionPolicy:
    """The configured policy, or the proposed defaults."""
    backup = getattr(settings, "backup", None)
    if backup is None:
        return RetentionPolicy()
    return RetentionPolicy(
        daily_days=int(getattr(backup, "retention_daily_days", 14)),
        weekly_weeks=int(getattr(backup, "retention_weekly_weeks", 8)),
        monthly_months=int(getattr(backup, "retention_monthly_months", 12)),
    )
