#!/usr/bin/env python
"""Carry out a data-subject erasure request for one platform user.

UK GDPR gives a person one month to have their data erased, and this product has
no self-service control, so the request arrives at `privacy@whiteto.com` and an
operator runs this. Doing it by hand in a database console at the end of that
month is how the wrong tenant loses its data.

**Dry run is the default.** Erasing requires `--apply`, typed deliberately, and
then a second confirmation naming the address.

    # 1. Rehearse. Reads and counts; writes nothing.
    python scripts/erase_data_subject.py --email person@example.com

    # 2. Perform it.
    python scripts/erase_data_subject.py --email person@example.com --apply

The address is a command-line argument rather than an environment variable
because it is not a secret — it is the subject of a request already sitting in
an inbox. It is **not** written to the application log: erasing someone while
recording their address in a log that outlives the erasure is not erasure.

Idempotent: a second run reports zeroes rather than failing, so a retry after an
interrupted run is safe.

Exit codes: 0 done, 1 no such user, 2 refused (confirmation declined).

See `docs/governance/DATA_SUBJECT_REQUESTS.md` for the surrounding process —
identity verification, the response letter, and what must be recorded.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database.session import session_factory
from app.services.data_subject_erasure import DataSubjectErasureService


async def run(email: str, apply: bool, assume_yes: bool) -> int:
    async with session_factory() as session:
        service = DataSubjectErasureService(session)

        subject = await service.resolve(email)
        if subject is None:
            print("No user matches that address. Nothing to do.")
            return 1

        print(f"Subject: {subject}")
        print("\nDeclared categories:")
        for category in service.categories():
            print(f"  {category.name:<28} {category.action:<10} {category.note}")

        plan = await service.plan(subject)
        print(f"\n{plan.describe()}")

        if not apply:
            print("\nDry run. Re-run with --apply to erase.")
            return 0

        if not assume_yes:
            print(
                "\nThis permanently erases the rows above and cannot be undone."
                "\nType the subject's address to confirm: ",
                end="",
            )
            try:
                # Off the event loop: a blocking read here would stall the open
                # database session for as long as the operator takes to type.
                typed = (await asyncio.to_thread(input)).strip()
            except EOFError:
                print("\nNo confirmation received. Nothing was erased.")
                return 2
            if typed.lower() != email.strip().lower():
                print("Confirmation did not match. Nothing was erased.")
                return 2

        outcome = await service.erase(subject)
        await session.commit()
        print(f"\n{outcome.describe()}")
        print(
            "\nRecord this in the request log: subject id, date received, date "
            "completed, and the counts above. Do not paste the address into the "
            "application log."
        )
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True, help="Address from the verified request.")
    parser.add_argument(
        "--apply", action="store_true", help="Erase. Without this the script only reports."
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the typed confirmation. For rehearsed, logged runbook execution only.",
    )
    args = parser.parse_args()
    return asyncio.run(run(args.email, args.apply, args.yes))


if __name__ == "__main__":
    raise SystemExit(main())
