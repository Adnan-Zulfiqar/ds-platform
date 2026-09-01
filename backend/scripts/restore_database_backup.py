#!/usr/bin/env python
"""Restore an encrypted backup into an isolated database.

    # Rehearse. Verifies the backup, checks both identities, writes nothing.
    python scripts/restore_database_backup.py --file <name> \\
        --expect-source droppilot_staging --target droppilot_restore_check

    # Perform it.
    python scripts/restore_database_backup.py --file <name> \\
        --expect-source droppilot_staging --target droppilot_restore_check --apply

**The default is a rehearsal, and that is the safety property.** The dangerous
form is the one an operator has to opt into, at three in the morning, having
read what the rehearsal said would happen.

Three identities are checked, because at least one of them is wrong in every
real incident:

* the backup's own recorded source must equal `--expect-source`, so yesterday's
  staging dump cannot be poured into a production-shaped database;
* the connected database must equal `--target`, confirmed by asking the server;
* the target must be empty, unless `--allow-non-empty` says overwriting this
  particular database is the intention.

**Restoring over production.** `droppilot` is refused outright unless
`--production-restore` is given *and* the operator types the database name when
prompted. That flag has never been exercised: the milestone that built this
tooling was not authorised to touch production, and the first use must be a
rehearsed, supervised operation with the service stopped. Nothing here makes
that decision for you.

The restore runs inside a single transaction with `--exit-on-error`, so a
failure part-way leaves the target as it was rather than half-populated. No
restored row is ever printed: the report is counts and digests.

Exit codes: 0 done or rehearsed, 1 usage, 2 configuration or identity refused,
5 the backup did not verify, 7 the restore or a check after it failed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings
from app.core.database_identity import (
    DatabaseNameError,
    is_protected_database,
    safe_label,
    validate_database_name,
)
from app.services.database_backup import (
    DEFAULT_INTEGRITY_QUERIES,
    BackupError,
    ExitCode,
    PostgresTarget,
    RestoreOutcome,
    load_key_from_settings,
    resolve_backup_directory,
    restore_backup,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="restore_database_backup.py",
        description="Restore an encrypted backup into an isolated database.",
    )
    parser.add_argument("--file", required=True, help="The backup file, by path or by name.")
    parser.add_argument(
        "--expect-source",
        required=True,
        help="The database this backup must have been taken from.",
    )
    parser.add_argument(
        "--target",
        required=True,
        help=(
            "The database to restore into. Must match the configured "
            "POSTGRES_DB; the two are compared rather than one overriding the "
            "other."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually restore. Without this the run is a rehearsal.",
    )
    parser.add_argument(
        "--allow-non-empty",
        action="store_true",
        help="Permit a target that already contains tables. Destructive.",
    )
    parser.add_argument(
        "--production-restore",
        action="store_true",
        help="Permit a protected production database as the target. Also prompts.",
    )
    parser.add_argument(
        "--integrity-queries",
        default=None,
        help="JSON file of name-to-SQL checks, each returning one scalar.",
    )
    parser.add_argument(
        "--directory",
        default=None,
        help="Override BACKUP_DIRECTORY. Must be an absolute path.",
    )
    return parser


def _confirm_production(observed: str) -> bool:
    """Require the database name to be typed. Never auto-answered.

    A y/n prompt is answered reflexively; a name has to be read off the screen
    and copied, which is the smallest amount of deliberation that is worth
    anything. If stdin is not a terminal — a scheduled task, a pipeline — this
    refuses rather than reading a line from somewhere unattended.

    `observed` is the name the **server** reported, not the one typed on the
    command line. The two agree by the time this runs, because the identity
    check has already refused any disagreement — and that is the point: the
    operator confirms what the database says it is, rather than re-typing their
    own argument back at themselves.

    The comparison here is **exact**, deliberately. The protected-name *policy*
    is case-insensitive so that `DropPilot` cannot slip past it; the
    confirmation is not, because typing a different capitalisation than the
    server reported means the operator is not looking at what they think they
    are looking at.
    """
    if not sys.stdin.isatty():
        print(
            "REFUSED: a production restore needs a typed confirmation and this "
            "session has no terminal.",
            file=sys.stderr,
        )
        return False
    print()
    print(f"About to restore over the PRODUCTION database {safe_label(observed)!r}.")
    print("Every row currently in it will be replaced by the backup's contents.")
    try:
        typed = input("Type the database name to continue: ").strip()
    except (EOFError, KeyboardInterrupt):
        # `isatty` said there was a terminal and there was not, or the operator
        # thought better of it. Either way this is a refusal, not a crash: a
        # traceback here would exit 1, which means "bad flag", and a monitor
        # reading exit codes would file it as the operator's typo.
        print()
        print("REFUSED: no confirmation was given. Nothing was changed.", file=sys.stderr)
        return False
    if typed != observed:
        print("REFUSED: the name did not match. Nothing was changed.", file=sys.stderr)
        return False
    return True


def _load_queries(path: str | None) -> dict[str, str] | None:
    if path is None:
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Could not read the integrity queries: {exc}") from exc
    if not isinstance(data, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in data.items()
    ):
        raise SystemExit("The integrity queries file must map names to SQL strings.")
    return data


def _report(outcome: RestoreOutcome) -> None:
    heading = "REHEARSAL" if outcome.dry_run else "RESTORED"
    print(f"{heading}")
    print(f"  backup        : {outcome.manifest.backup_id}")
    print(f"  taken from    : {outcome.source_database} ({outcome.manifest.created_at})")
    print(f"  restored into : {outcome.target_database}")
    print(f"  dump entries  : {outcome.toc_entries}")
    print(f"  Alembic       : {outcome.alembic_revision}")
    print(f"  public tables : {outcome.public_tables}")
    for note in outcome.notes:
        print(f"  note          : {note}")
    if outcome.integrity:
        print("  integrity checks (aggregates and digests only):")
        for name, value in sorted(outcome.integrity.items()):
            print(f"    {name:<24} {value}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    queries = _load_queries(args.integrity_queries)

    try:
        # Names first, before configuration is even read. A malformed flag is
        # the operator's typo, and it should be reported as that rather than as
        # whatever unrelated thing happens to be missing from the environment.
        validate_database_name(args.target, field="--target")
        validate_database_name(args.expect_source, field="--expect-source")

        # Refused before anything is connected to, on the stated name alone.
        # `is_protected_database` canonicalises, so every capitalisation and
        # whitespace variant of a protected name lands here.
        if is_protected_database(args.target) and not args.production_restore:
            print(
                f"REFUSED: {safe_label(args.target)!r} is a protected production "
                "database name. Restoring over it needs --production-restore and "
                "a typed confirmation.",
                file=sys.stderr,
            )
            return int(ExitCode.CONFIGURATION)
    except DatabaseNameError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return int(ExitCode.CONFIGURATION)

    try:
        key = load_key_from_settings(settings)
        directory = resolve_backup_directory(
            args.directory or settings.backup.directory, create=False
        )
        given = Path(args.file)
        path = given if given.is_absolute() else directory / given.name
        outcome = restore_backup(
            path,
            # From configuration, not from `--target`: see the note in
            # create_database_backup.py. `--target` is the expectation the
            # connected database is checked against.
            target=PostgresTarget.from_settings(settings),
            expected_source=args.expect_source,
            expected_target=args.target,
            key=key,
            apply=args.apply,
            allow_production_target=args.production_restore,
            allow_non_empty=args.allow_non_empty,
            pg_bin_dir=settings.backup.pg_bin_dir,
            integrity_queries=queries or DEFAULT_INTEGRITY_QUERIES,
            # Passed unconditionally: the service decides whether a confirmation
            # is needed, from the identity the server reported. A CLI that
            # decided for itself would be deciding from the command line, which
            # is the thing under suspicion.
            confirm_production=_confirm_production,
        )
    except BackupError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return int(exc.exit_code)

    _report(outcome)
    if outcome.dry_run:
        print()
        print("Nothing was written. Re-run with --apply to perform the restore.")
    return int(ExitCode.OK)


if __name__ == "__main__":
    raise SystemExit(main())
