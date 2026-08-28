"""Bounded deletion of persisted log files.

Request logs record client IP addresses, so "how long do you keep them" is a
statement in the privacy notice rather than an operational detail. This module
is what makes that statement enforceable instead of aspirational.

**It is deliberately small and deliberately suspicious.** A retention job is a
program whose whole purpose is deleting files on a production server, usually
unattended, usually as a privileged user. The failure mode is not "it kept logs
a day too long" — it is "it removed something that was not a log". Every rule
below exists to make that outcome impossible rather than unlikely:

* **No default directory.** `LOG_DIRECTORY` must be set explicitly. The
  application logs to stdout and opens no file of its own, so there is nothing
  to infer; a tool that guesses where logs live is a tool that deletes something
  else.
* **Never recursive.** One `glob`, never `rglob`. A log directory that happens
  to contain a checkout must not become a repository deletion.
* **Refuses dangerous roots.** A directory holding `.git`, `.env`, `alembic.ini`
  or `pyproject.toml` is somebody's project, not a log store.
* **Name allowlist, not a denylist.** Only `*.log` and rotated `*.log.<n>` are
  ever candidates. `.env.log` cannot exist under that rule, but `config.json`
  and `dump.sql` plainly cannot either.
* **Containment is re-checked after resolving.** A symlink pointing outside the
  directory resolves outside it, and is skipped.
* **The un-rotated current file is never deleted.** `app.log` is what a
  running process appends to; `app.log.1` and `app.log.2026-08-01` are closed by
  definition. Retention therefore applies to *rotated* files, which means a
  deployment must rotate for the published retention period to hold — a real
  limitation, written down in `docs/governance/LOG_RETENTION.md` rather than
  papered over.
* **Dry run is the default** everywhere it can be. Callers ask for deletion.

Nothing here touches the database, and nothing reads a log file's contents — the
whole point is to remove personal data, not to look at it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

__all__ = [
    "LOG_FILENAME_PATTERNS",
    "LogRetentionError",
    "LogRetentionPlan",
    "PrunedFile",
    "build_plan",
    "execute_plan",
]

#: Only these shapes are ever candidates. Rotated files are `app.log.1`,
#: `app.log.2026-08-28`, and similar.
LOG_FILENAME_PATTERNS: Final[tuple[str, ...]] = ("*.log", "*.log.*")

#: A directory containing any of these is a working tree, a configuration root
#: or a deployment — not a log store. Refuse rather than filter.
_FORBIDDEN_NEIGHBOURS: Final[frozenset[str]] = frozenset(
    {".git", ".env", "alembic.ini", "pyproject.toml", "package.json", ".venv", "node_modules"}
)


class LogRetentionError(RuntimeError):
    """Refusal to operate. Always a configuration problem, never a file problem."""


@dataclass(frozen=True, slots=True)
class PrunedFile:
    """One file the plan concerns, and why."""

    path: Path
    size_bytes: int
    modified_at: datetime


@dataclass(slots=True)
class LogRetentionPlan:
    """What would be removed, and what was examined and kept.

    Carries counts rather than the log lines themselves: an operator needs to
    know how much went, not what was in it.
    """

    directory: Path
    cutoff: datetime
    retention_days: int
    delete: list[PrunedFile] = field(default_factory=list)
    kept_recent: int = 0
    kept_active: int = 0
    skipped_unsafe: list[Path] = field(default_factory=list)

    @property
    def delete_bytes(self) -> int:
        return sum(item.size_bytes for item in self.delete)

    def describe(self) -> str:
        """One line an operator can paste into a change record."""
        return (
            f"{len(self.delete)} file(s), {self.delete_bytes} bytes older than "
            f"{self.retention_days}d (cutoff {self.cutoff.isoformat()}) in {self.directory}; "
            f"kept {self.kept_recent} recent, {self.kept_active} active, "
            f"skipped {len(self.skipped_unsafe)} unsafe"
        )


def _resolve_directory(directory: Path | None) -> Path:
    if directory is None:
        raise LogRetentionError(
            "LOG_DIRECTORY is not set. This deployment persists no application "
            "logs, so there is nothing to prune. Set it only to a directory that "
            "contains log files and nothing else."
        )

    resolved = Path(directory).expanduser().resolve()

    if not resolved.is_dir():
        raise LogRetentionError(f"LOG_DIRECTORY is not an existing directory: {resolved}")

    # A drive or filesystem root is never a log directory, and a mistake there is
    # unrecoverable.
    if resolved.parent == resolved:
        raise LogRetentionError(f"Refusing to operate on a filesystem root: {resolved}")

    for forbidden in sorted(_FORBIDDEN_NEIGHBOURS):
        if (resolved / forbidden).exists():
            raise LogRetentionError(
                f"Refusing to prune {resolved}: it contains {forbidden!r}, so it is a "
                "project or deployment directory rather than a log store."
            )

    return resolved


def _is_contained(candidate: Path, directory: Path) -> bool:
    """Whether ``candidate`` really sits directly inside ``directory``.

    Checked *after* resolving, so a symlink whose target lives elsewhere fails
    here even though its name appeared in the listing.
    """
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        return False
    return resolved.parent == directory and resolved.is_file()


def build_plan(
    *,
    directory: Path | None,
    retention_days: int,
    now: datetime | None = None,
) -> LogRetentionPlan:
    """Decide what to delete without deleting anything.

    ``now`` is injectable so the boundary behaviour can be tested exactly rather
    than approximately; production passes nothing and gets the current UTC time.
    """
    if retention_days < 1:
        raise LogRetentionError("retention_days must be at least 1.")

    resolved = _resolve_directory(directory)
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        raise LogRetentionError("`now` must be timezone-aware.")
    cutoff = moment - timedelta(days=retention_days)

    plan = LogRetentionPlan(directory=resolved, cutoff=cutoff, retention_days=retention_days)

    candidates: list[Path] = []
    seen: set[Path] = set()
    for pattern in LOG_FILENAME_PATTERNS:
        # `glob`, never `rglob`: one directory, no descent.
        for entry in resolved.glob(pattern):
            if entry in seen:
                continue
            seen.add(entry)
            candidates.append(entry)

    # `app.log` is the file a running process appends to; `app.log.1` is closed
    # by definition. Selecting on the name rather than on modification time means
    # a quiet service's live file is still never removed, and a stale rotated
    # file with no active sibling is still eligible — the previous
    # newest-per-stem rule protected exactly the wrong one of those.
    usable: list[tuple[Path, os.stat_result]] = []

    for entry in sorted(candidates):
        if entry.is_symlink() or not _is_contained(entry, resolved):
            plan.skipped_unsafe.append(entry)
            continue
        try:
            stat = entry.stat()
        except OSError:
            plan.skipped_unsafe.append(entry)
            continue
        usable.append((entry, stat))

    for entry, stat in usable:
        modified = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
        if entry.name.endswith(".log"):
            plan.kept_active += 1
            continue
        if modified >= cutoff:
            plan.kept_recent += 1
            continue
        plan.delete.append(PrunedFile(path=entry, size_bytes=stat.st_size, modified_at=modified))

    return plan


def execute_plan(plan: LogRetentionPlan) -> list[Path]:
    """Delete exactly what the plan lists. Returns what actually went.

    Re-validates containment immediately before each unlink: the plan may have
    been built seconds ago, and a file that became a symlink in between must not
    be followed.
    """
    removed: list[Path] = []
    for item in plan.delete:
        if not _is_contained(item.path, plan.directory):
            plan.skipped_unsafe.append(item.path)
            continue
        try:
            item.path.unlink()
        except OSError:
            plan.skipped_unsafe.append(item.path)
            continue
        removed.append(item.path)
    return removed
