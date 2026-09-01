#!/usr/bin/env python
"""Apply the backup retention policy, refusing anything it cannot justify.

    # Rehearse. This is the default, and the only mode a scheduler should use
    # until an operator has read a report and agreed with it.
    python scripts/prune_database_backups.py

    # Carry it out.
    python scripts/prune_database_backups.py --apply

The policy is grandfather-father-son, configured by `BACKUP_RETENTION_*`, and a
backup survives if **any** window wants it — recent enough, newest in its week,
or newest in its month. Intersection would be the obvious-looking mistake: it
deletes a file that two of the three policies were trying to keep.

Four refusals are unconditional and no flag relaxes them:

* the newest backup that passes its integrity check is never deleted;
* nothing is deleted while only one backup passes;
* a file that cannot be verified is quarantined, never removed — an unreadable
  backup is evidence about a failure, and deleting evidence is how the failure
  stays invisible;
* the directory must be an absolute, non-symlinked, dedicated path at least two
  levels below its root, outside any home directory, system directory or source
  checkout, and every candidate's resolved parent must be that directory.

The default check is cryptographic — manifest authenticity, recorded sizes and
SHA-256 — which proves a file is byte-identical to what was published. It does
**not** prove restorability; `--deep` decrypts every file and parses its table
of contents, and the report always says which check ran.

Exit codes: 0 done or rehearsed, 1 usage, 2 the directory or policy was
refused, 6 a deletion failed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings
from app.services.backup_retention import (
    RetentionPolicy,
    apply_plan,
    plan_retention,
    scan_directory,
)
from app.services.database_backup import (
    BackupError,
    ExitCode,
    load_key_from_settings,
    resolve_backup_directory,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prune_database_backups.py",
        description="Apply the backup retention policy. Rehearses unless --apply is given.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Delete and quarantine for real. Without this nothing is touched.",
    )
    parser.add_argument(
        "--deep",
        action="store_true",
        help="Decrypt and parse every backup, rather than checking its digest.",
    )
    parser.add_argument("--directory", default=None, help="Override BACKUP_DIRECTORY.")
    parser.add_argument("--daily-days", type=int, default=None)
    parser.add_argument("--weekly-weeks", type=int, default=None)
    parser.add_argument("--monthly-months", type=int, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    backup = settings.backup

    try:
        policy = RetentionPolicy(
            daily_days=args.daily_days or backup.retention_daily_days,
            weekly_weeks=args.weekly_weeks or backup.retention_weekly_weeks,
            monthly_months=args.monthly_months or backup.retention_monthly_months,
        )
        key = load_key_from_settings(settings)
        directory = resolve_backup_directory(args.directory or backup.directory, create=False)
        entries = scan_directory(directory, key, deep=args.deep, pg_bin_dir=backup.pg_bin_dir)
        plan = plan_retention(entries, policy, deep=args.deep)
        actions = apply_plan(directory, plan, entries, apply=args.apply)
    except BackupError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return int(exc.exit_code)

    check = "deep (decrypted and parsed)" if plan.deep else "digest and manifest authenticity"
    print(f"Directory : {directory}")
    print(
        f"Policy    : {policy.daily_days} days, {policy.weekly_weeks} weeks, "
        f"{policy.monthly_months} months"
    )
    print(f"Check     : {check}")
    print(f"Mode      : {'APPLY' if args.apply else 'rehearsal, nothing touched'}")
    print()

    for label, names in (
        ("RETAINED", plan.retained),
        ("DELETABLE", plan.deletable),
        ("QUARANTINE", plan.quarantine),
    ):
        print(f"{label} ({len(names)})")
        for name in names:
            print(f"  {name}  --  {plan.reasons.get(name, 'no reason recorded')}")
        print()

    for refusal in plan.refusals:
        print(f"REFUSAL   : {refusal}")
    for action in actions:
        print(f"ACTION    : {action}")

    if not args.apply and (plan.deletable or plan.quarantine):
        print()
        print("Nothing was changed. Re-run with --apply to carry this out.")
    return int(ExitCode.OK)


if __name__ == "__main__":
    raise SystemExit(main())
