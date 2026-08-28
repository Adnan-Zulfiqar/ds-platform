#!/usr/bin/env python
"""Delete persisted log files older than the configured retention period.

The privacy notice states how long request logs — which contain client IP
addresses — are kept. This is the thing that makes that true.

**Dry run is the default.** Deleting requires `--apply`, typed deliberately.

    # See what would go. Safe, and the only mode that runs unattended by accident.
    python scripts/prune_logs.py

    # Actually remove them.
    python scripts/prune_logs.py --apply

    # Override the configured window for a one-off.
    python scripts/prune_logs.py --retention-days 7 --apply

Configuration (both read from the environment, never from arguments):

    LOG_DIRECTORY       absolute path of the directory to prune; unset = refuse
    LOG_RETENTION_DAYS  maximum age in days (default 30)

The current production deployment runs uvicorn in the foreground and persists
nothing to disk, so `LOG_DIRECTORY` is unset there and this script refuses. That
refusal is the correct outcome, not a failure: see
`docs/governance/LOG_RETENTION.md`.

Exit codes: 0 success (including "nothing to do"), 2 refusal (misconfiguration).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow `python scripts/prune_logs.py` from the backend root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings
from app.core.log_retention import (
    LogRetentionError,
    build_plan,
    execute_plan,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete. Without this the script only reports.",
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        default=None,
        help="Override LOG_RETENTION_DAYS for this run.",
    )
    args = parser.parse_args()

    settings = get_settings()
    retention_days = args.retention_days or settings.observability.retention_days

    try:
        plan = build_plan(
            directory=settings.observability.directory,
            retention_days=retention_days,
        )
    except LogRetentionError as error:
        print(f"Refusing to run: {error}")
        return 2

    print(plan.describe())

    for item in plan.delete:
        print(
            f"  {'delete' if args.apply else 'would delete'}  {item.path.name}  "
            f"{item.size_bytes} bytes  modified {item.modified_at.isoformat()}"
        )

    for path in plan.skipped_unsafe:
        print(f"  skipped (unsafe)  {path.name}")

    if not args.apply:
        if plan.delete:
            print("\nDry run. Re-run with --apply to delete these files.")
        return 0

    removed = execute_plan(plan)
    print(f"\nDeleted {len(removed)} file(s).")
    if plan.skipped_unsafe:
        print(f"Skipped {len(plan.skipped_unsafe)} unsafe path(s); see above.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
