#!/usr/bin/env python
"""Prove an encrypted backup is restorable, or say precisely why it is not.

    python scripts/verify_database_backup.py --file droppilot_staging-...-a1b2c3d4.dpbk
    python scripts/verify_database_backup.py --all

**Existence is not verification, and this is the tool that means it.** Every
check below is run in order, and the first failure stops the run:

1. the manifest parses against a closed schema — unknown fields are refused,
   not ignored;
2. the manifest's authentication code matches, under the configured backup key,
   so an edited manifest cannot describe a file it does not belong to;
3. the file's size matches the manifest, catching truncation and appending;
4. the file's SHA-256 matches the manifest;
5. the whole stream decrypts, with every chunk's authentication tag checked and
   the final chunk's flag required, so a file that stops early is refused as
   truncated rather than accepted as short;
6. the decrypted length and digest match the manifest;
7. `pg_restore --list` parses the dump and finds a non-empty table of contents.

The plaintext exists only inside a temporary directory restricted to this
account, and is overwritten and removed before this process exits.

Exit codes: 0 verified, 1 usage, 2 configuration, 5 verification failed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings
from app.services.database_backup import (
    CIPHERTEXT_SUFFIX,
    BackupError,
    ExitCode,
    VerificationError,
    load_key_from_settings,
    resolve_backup_directory,
    verify_backup,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verify_database_backup.py",
        description="Decrypt, authenticate and parse a backup to prove it is restorable.",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", help="One backup file, by path or by name.")
    source.add_argument(
        "--all",
        action="store_true",
        help="Every backup in the configured directory, oldest first.",
    )
    parser.add_argument(
        "--directory",
        default=None,
        help="Override BACKUP_DIRECTORY. Must be an absolute path.",
    )
    return parser


def _describe(path: Path, outcome: object) -> None:
    manifest = outcome.manifest  # type: ignore[attr-defined]
    print(f"VERIFIED     : {path.name}")
    print(f"  taken from : {manifest.database} at Alembic {manifest.alembic_revision}")
    print(f"  created    : {manifest.created_at}")
    print(f"  server     : {manifest.postgres_version}")
    print(f"  key        : {manifest.key_id}")
    print(f"  dump bytes : {manifest.plaintext_bytes}")
    print(f"  TOC entries: {outcome.toc_entries}")  # type: ignore[attr-defined]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        key = load_key_from_settings(settings)
        directory = resolve_backup_directory(
            args.directory or settings.backup.directory, create=False
        )
    except BackupError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return int(exc.exit_code)

    if args.all:
        candidates = sorted(directory.glob(f"*{CIPHERTEXT_SUFFIX}"))
        if not candidates:
            print("No backups found. That is not a pass; there is nothing to verify.")
            return int(ExitCode.VERIFICATION)
    else:
        given = Path(args.file)
        candidates = [given if given.is_absolute() else directory / given.name]

    failures = 0
    for path in candidates:
        try:
            outcome = verify_backup(path, key=key, pg_bin_dir=settings.backup.pg_bin_dir)
        except VerificationError as exc:
            failures += 1
            print(f"FAILED       : {path.name}", file=sys.stderr)
            print(f"  {exc}", file=sys.stderr)
        except BackupError as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return int(exc.exit_code)
        else:
            _describe(path, outcome)

    if failures:
        print(
            f"\n{failures} of {len(candidates)} backups did not verify. Treat them as absent.",
            file=sys.stderr,
        )
        return int(ExitCode.VERIFICATION)
    return int(ExitCode.OK)


if __name__ == "__main__":
    raise SystemExit(main())
