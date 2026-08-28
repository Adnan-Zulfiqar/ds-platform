#!/usr/bin/env python
"""Carry out an erasure request, in one explicit scope, inside one transaction.

UK GDPR gives a person one month, and this product has no self-service control,
so the request arrives at `privacy@whiteto.com` and an operator runs this. Doing
it by hand in a database console at the end of that month is how the wrong
tenant loses its data.

Two scopes, deliberately separate:

    platform-user   one person. Their credentials, grants and notifications go;
                    every reference naming them is cleared. Colleagues, orders
                    and marketplace credentials are untouched.

    workspace       the whole workspace. Every member erased, and the workspace's
                    Shopify, AliExpress and eBay connections deleted with their
                    encrypted credentials.

**Marketplace buyers are not covered.** `orders` holds no buyer identifier, so a
buyer can only be matched on a name — which would erase a different customer who
shares it. Directing a shopper's request to the merchant is the correct answer;
see `docs/governance/DATA_SUBJECT_REQUESTS.md`.

Usage
-----

    # Rehearse. Reads and counts, writes nothing. This is the default.
    python scripts/erase_data_subject.py platform-user \\
        --tenant-id <uuid> --expect-database droppilot_staging

    # Perform it.
    python scripts/erase_data_subject.py platform-user \\
        --tenant-id <uuid> --expect-database droppilot_staging --apply

    # Close a workspace.
    python scripts/erase_data_subject.py workspace \\
        --tenant-id <uuid> --expect-database droppilot_staging --apply

The address is **prompted for**, never passed as an argument: command lines are
visible in shell history and in every other user's process list. It is not
written to the application log either. Use `--user-id` to skip the prompt
entirely when the request already identifies the account.

`--expect-database` is mandatory and checked with `SELECT current_database()`
against the server that would receive the writes. Configuration is not identity:
the URL is the same string that was already wrong.

Exit codes: 0 done, 1 could not resolve the subject, 2 refused.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.database_identity import DatabaseIdentityError, require_database
from app.database.session import session_factory
from app.services.data_subject_erasure import (
    ErasureScope,
    PlatformUserErasureService,
    SubjectResolutionError,
    WorkspaceClosureService,
    login_throttle_keys,
)


async def _redis_note(email: str | None) -> None:
    """What Redis holds, and what can be addressed exactly."""
    print("\nRedis:")
    if email:
        for key in login_throttle_keys(email):
            print(f"  exact key to delete: {key}")
        print("    (the login throttle hashes the address, so this is exact)")
    print("  ratelimit:ip:<address> — derived from the client IP, not from the")
    print("    subject; cannot be attributed safely. Expires within 60 seconds.")
    print("  t:<tenant>:* — workspace cache namespace; cleared by the existing")
    print("    tenant invalidation on workspace closure.")


async def run(args: argparse.Namespace) -> int:
    scope = ErasureScope(args.scope)
    tenant_id: uuid.UUID = args.tenant_id

    async with session_factory() as session:
        # --- guard 1: is this the database the operator meant? --------------
        try:
            database = await require_database(
                session,
                expected=args.expect_database,
                allow_production=args.i_understand_production,
            )
        except DatabaseIdentityError as error:
            print(f"Refusing to run: {error}")
            return 2

        users = PlatformUserErasureService(session)
        email: str | None = None

        # --- guard 2: exactly one subject, inside a named tenant ------------
        try:
            if scope is ErasureScope.PLATFORM_USER:
                if args.user_id:
                    subject = await users.resolve_by_id(tenant_id=tenant_id, user_id=args.user_id)
                else:
                    email = getpass.getpass("Subject's email address (not echoed): ").strip()
                    if not email:
                        print("No address given. Nothing was changed.")
                        return 1
                    subject = await users.resolve_by_email(tenant_id=tenant_id, email=email)
                plan = await users.plan(subject)
                target = str(subject)
            else:
                subject = None
                plan = await WorkspaceClosureService(session).plan(tenant_id)
                target = f"tenant={tenant_id}"
        except SubjectResolutionError as error:
            print(f"Could not resolve a subject: {error}")
            return 1

        print(f"\nScope    : {scope}")
        print(f"Database : {database}")
        print(f"Target   : {target}")
        print(f"\n{plan.describe()}")

        if not args.apply:
            await _redis_note(email)
            print("\nDry run. Nothing was changed. Re-run with --apply to erase.")
            return 0

        # --- guard 3: typed confirmation naming scope, tenant and database ---
        expected_phrase = f"{scope} {tenant_id} {database}"
        print(
            "\nThis permanently erases the rows above and cannot be undone."
            f"\nType exactly:  {expected_phrase}"
            "\n> ",
            end="",
        )
        try:
            typed = (await asyncio.to_thread(input)).strip()
        except EOFError:
            print("\nNo confirmation received. Nothing was changed.")
            return 2
        if typed != expected_phrase:
            print("Confirmation did not match. Nothing was changed.")
            return 2

        # --- the write, in one transaction ----------------------------------
        try:
            if scope is ErasureScope.PLATFORM_USER:
                assert subject is not None
                outcome = await users.erase(subject)
            else:
                outcome = await WorkspaceClosureService(session).erase(tenant_id)
            await session.commit()
        except Exception as error:
            await session.rollback()
            print(f"\nFAILED and rolled back. Nothing was erased. {type(error).__name__}: {error}")
            return 2

        print(f"\n{outcome.describe()}")
        await _redis_note(email)
        print(
            "\nRecord in the request log: scope, tenant id, user id, the dates, "
            "and the counts above. Do not record the address itself."
        )
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("scope", choices=[s.value for s in ErasureScope])
    parser.add_argument("--tenant-id", required=True, type=uuid.UUID)
    parser.add_argument(
        "--user-id",
        type=uuid.UUID,
        default=None,
        help="Resolve by id instead of prompting for an address.",
    )
    parser.add_argument(
        "--expect-database",
        required=True,
        help="The database you expect to be connected to; checked against the server.",
    )
    parser.add_argument("--apply", action="store_true", help="Erase. Otherwise dry run.")
    parser.add_argument(
        "--i-understand-production",
        action="store_true",
        help="Permit a production database name. Required in addition to --apply.",
    )
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
