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

Exit codes: 0 done, 1 could not resolve the subject, 2 refused or rolled back,
3 database erasure complete but cache cleanup pending — re-run to retry.
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
    execute_platform_user_erasure,
    execute_workspace_closure,
)


def _redis_note(scope: ErasureScope, have_email: bool) -> None:
    """The real key inventory. Deliberately prints no key and no address.

    A key is a hash of the value it came from, and printing hashes of
    low-entropy values is how somebody builds a lookup table for them.
    """
    print("\nRedis:")
    if scope is ErasureScope.PLATFORM_USER:
        if have_email:
            print("  login:email:{sha256(normalised_address)[:32]}  — deleted after commit")
        else:
            print("  login:email:{sha256(normalised_address)[:32]}  — not deleted: the")
            print("      subject was resolved by user id, so no address is available.")
            print("      It expires on its own; see the TTLs below.")
    else:
        print("  t:{tenant}:*  — workspace cache namespace, cleared after the commit")
        print("      via CacheClient.invalidate_tenant (SCAN, never KEYS, never FLUSHDB)")
    print("  login:ip:{sha256(client_ip)[:32]}  — NOT erased. An address is not a")
    print("      person: shared behind NAT, reassigned by ISPs, and a subject may have")
    print("      signed in from many. Expires after 300s, or 900s once the attempt")
    print("      limit is reached.")
    print("  ratelimit:ip:{client_ip}  — request throttling, 60s window. Same reason.")


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
            _redis_note(scope, email is not None)
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

        # --- the write: one transaction, then Redis strictly afterwards -----
        try:
            if scope is ErasureScope.PLATFORM_USER:
                assert subject is not None
                execution = await execute_platform_user_erasure(session, subject, email=email)
            else:
                execution = await execute_workspace_closure(session, tenant_id)
        except Exception as error:
            # Only the database work can raise here. The cache step runs after
            # the commit and reports rather than raises, so on this path Redis
            # was never touched and there is nothing to undo.
            await session.rollback()
            print(f"\nFAILED and rolled back. Nothing was erased. {type(error).__name__}: {error}")
            return 2

        print(f"\n{execution.describe()}")
        _redis_note(scope, email is not None)

        if execution.cache.pending:
            # The erasure is committed and correct. Calling the whole operation
            # failed would be false and would have someone re-open a finished
            # request; saying nothing would leave stale cache behind unnoticed.
            print(
                "\nThe database erasure is COMPLETE and committed. Only the cache "
                "cleanup did not finish."
                "\nRe-run this exact command to retry it: the operation is idempotent, "
                "the database counts will come back zero, and the cache step runs "
                "again regardless."
            )
            return 3

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
