#!/usr/bin/env python
"""Take one encrypted backup of a PostgreSQL database.

    python scripts/create_database_backup.py --expect-database droppilot_staging

The expected database name is mandatory and is confirmed with
`SELECT current_database()` against the server that will actually be read. The
connection string is not identity: it is the same string that was already
wrong, and a backup of the wrong database is worse than none because it is
indistinguishable from a good one until the day it is restored.

**Production is refused by default.** `droppilot` needs `--allow-production`,
which exists so that taking a production backup is a deliberate act with a flag
somebody had to type — not something a mistyped staging name does silently. The
protected-name check is case-insensitive policy, so `DROPPILOT` and `DropPilot`
are refused too; the name actually handed to `pg_dump` is always the operator's
exact string.

**Nothing is published until everything succeeded.** The dump streams through
authenticated encryption into a `.part` file; that file is renamed into place
only after `pg_dump` exits zero and the final chunk is sealed; the manifest is
written last, because every other tool reads the manifest's presence as "this
backup is complete". Any failure, including an interruption, leaves the
directory exactly as it was found — and leaves the last-success marker at its
previous value, so a run that failed can never make the readiness check say the
backups are current.

Exit codes: 0 published, 1 usage, 2 configuration or identity refused, 3 the
dump failed, 4 encryption failed, 6 the filesystem refused.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.backup_crypto import CHUNK_BYTES
from app.core.config import settings
from app.core.database_identity import DatabaseNameError, validate_database_name
from app.services.database_backup import (
    LAST_SUCCESS_MARKER,
    BackupError,
    ExitCode,
    PostgresTarget,
    create_backup,
    load_key_from_settings,
    resolve_backup_directory,
    write_marker,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="create_database_backup.py",
        description="Take one encrypted, verifiable backup of a PostgreSQL database.",
    )
    parser.add_argument(
        "--expect-database",
        required=True,
        help=(
            "The database name the server must report. The connection itself "
            "comes from POSTGRES_* configuration; this is the claim it is "
            "checked against."
        ),
    )
    parser.add_argument(
        "--directory",
        default=None,
        help="Override BACKUP_DIRECTORY. Must be an absolute path.",
    )
    parser.add_argument(
        "--allow-production",
        action="store_true",
        help="Permit a protected production database as the source.",
    )
    parser.add_argument(
        "--chunk-bytes",
        type=int,
        default=CHUNK_BYTES,
        help="Plaintext bytes per authenticated chunk. Rarely worth changing.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Print only the published file's name.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        # Before configuration is read, for the same reason as in the restore
        # tool: a malformed name is a typo, not a missing environment.
        validate_database_name(args.expect_database, field="--expect-database")
    except DatabaseNameError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return int(ExitCode.CONFIGURATION)

    try:
        key = load_key_from_settings(settings)
        directory = resolve_backup_directory(
            args.directory or settings.backup.directory, create=True
        )
        # Deliberately NOT `dbname=args.expect_database`. The connection comes
        # from configuration and the flag is the operator's claim about it; the
        # check is that the two agree. Dialling the expected name would make
        # `current_database()` agree by construction and prove nothing — and the
        # failure this guards is a `.env` pointing somewhere unintended.
        target = PostgresTarget.from_settings(settings)
        outcome = create_backup(
            target=target,
            expected_database=args.expect_database,
            key=key,
            directory=directory,
            allow_production=args.allow_production,
            pg_bin_dir=settings.backup.pg_bin_dir,
            chunk_bytes=args.chunk_bytes,
        )
    except BackupError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return int(exc.exit_code)
    except KeyboardInterrupt:
        print("REFUSED: interrupted; nothing was published.", file=sys.stderr)
        return int(ExitCode.STORAGE)

    manifest = outcome.manifest
    # The marker is written last and only here. A failed run returns above
    # without reaching it, which is what makes "last success" mean it.
    write_marker(
        directory,
        LAST_SUCCESS_MARKER,
        {
            "completed_at": datetime.now(UTC).isoformat(),
            "backup_id": manifest.backup_id,
            "database": manifest.database,
            "ciphertext_bytes": manifest.ciphertext_bytes,
            "key_id": manifest.key_id,
        },
        key,
    )

    if args.quiet:
        print(outcome.ciphertext_path.name)
        return int(ExitCode.OK)

    print(f"Published    : {outcome.ciphertext_path.name}")
    print(f"Manifest     : {outcome.manifest_path.name}")
    print(f"Source       : {manifest.database} at Alembic {manifest.alembic_revision}")
    print(f"Server       : {manifest.postgres_version}")
    print(f"Application  : {manifest.application_sha}")
    print(f"Encryption   : {manifest.encryption}, key {manifest.key_id}")
    print(f"Dump bytes   : {manifest.plaintext_bytes}")
    print(f"File bytes   : {manifest.ciphertext_bytes}")
    print(f"File SHA-256 : {manifest.ciphertext_sha256}")
    print()
    print("Not yet verified. Run verify_database_backup.py against this file;")
    print("a backup nobody has read back is a belief, not a backup.")
    return int(ExitCode.OK)


if __name__ == "__main__":
    raise SystemExit(main())
