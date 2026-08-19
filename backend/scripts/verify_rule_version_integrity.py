"""Preflight for the deferred `global_rule_versions` typed-reference check.

Migration 0024 added ``ck_global_rule_versions_one_typed_reference`` as
``NOT VALID`` so that an upgrade could not fail on a pre-existing row the
backfill could not match. 0025 re-ran the backfill and validated the
constraint **only when nothing contradicted it** — otherwise it logged a
warning and left it unvalidated, deliberately, because a migration that
refused to run would take a whole deploy down over historical rows that are
already inert.

That decision moves the question to operations, and this is how it is
answered. It is a read-only report:

    python -m scripts.verify_rule_version_integrity

It never modifies data, and it is not run by the application. Startup does not
depend on it, because a startup check that can fail is a startup check that
takes a service down.

Output is deliberately shaped for a shared terminal or a CI log: it prints
*counts per tenant* and the ids of unmatched **version** rows, never a rule
name, a note or anything a merchant wrote. Two tenants' operators may read the
same output.

Exit codes:

* ``0`` — the constraint is validated and no unmatched rows exist. This is what
  a fresh M3A installation reports.
* ``1`` — unmatched rows exist. The remediation is below.
* ``2`` — the constraint is not validated but nothing contradicts it; a single
  ``VALIDATE CONSTRAINT`` will finish the job.

**Remediation.** An unmatched row is a version whose typed column
(``pricing_rule_id`` / ``shipping_rule_id``) does not agree with ``rule_id``,
which in practice means the rule it points at no longer exists in that tenant.
The history is still readable — that is why it is kept — so the safe repair is
to leave the rows alone and validate nothing, or, once an operator has
confirmed the rows are genuinely orphaned, to delete those specific version
rows and then run::

    ALTER TABLE global_rule_versions
      VALIDATE CONSTRAINT ck_global_rule_versions_one_typed_reference;

Validation takes a SHARE UPDATE EXCLUSIVE lock: it does not block reads or
writes, so it is safe online.
"""

from __future__ import annotations

import sys
from collections import Counter

import sqlalchemy as sa

from app.core.config import settings

_UNMATCHED = sa.text(
    """
    SELECT id, tenant_id
      FROM global_rule_versions
     WHERE (rule_kind = 'pricing'  AND pricing_rule_id  IS DISTINCT FROM rule_id)
        OR (rule_kind = 'shipping' AND shipping_rule_id IS DISTINCT FROM rule_id)
     ORDER BY created_at
    """
)

_VALIDATED = sa.text(
    """
    SELECT convalidated
      FROM pg_constraint
     WHERE conname = 'ck_global_rule_versions_one_typed_reference'
    """
)


def main() -> int:
    engine = sa.create_engine(settings.database.sync_dsn)
    try:
        with engine.connect() as connection:
            validated = connection.execute(_VALIDATED).scalar()
            # Short-circuit before touching the table. Pointed at a database
            # that has not run 0024 -- the wrong one, most likely -- the row
            # query would raise `UndefinedTable`, and a traceback is a poor way
            # to tell an operator they typed the wrong name.
            if validated is None:
                print("MISSING: ck_global_rule_versions_one_typed_reference does not exist.")
                print(f"         Migration 0024 has not been applied to {settings.database.db}.")
                return 1
            rows = connection.execute(_UNMATCHED).all()
    finally:
        engine.dispose()

    print(f"database:  {settings.database.db}")
    print(f"validated: {'yes' if validated else 'no'}")
    print(f"unmatched: {len(rows)}")

    if not rows:
        if validated:
            print("\nOK — the constraint is validated and every row satisfies it.")
            return 0
        print(
            "\nACTION — nothing contradicts the constraint, but it is still NOT VALID.\n"
            "  ALTER TABLE global_rule_versions\n"
            "    VALIDATE CONSTRAINT ck_global_rule_versions_one_typed_reference;"
        )
        return 2

    # Per-tenant counts, then ids. Nothing a merchant authored is printed:
    # this output is read by whoever operates the platform, not by the tenant
    # whose data it describes.
    per_tenant = Counter(str(tenant_id) for _, tenant_id in rows)
    print("\nunmatched rows by tenant:")
    for tenant_id, count in per_tenant.most_common():
        print(f"  {tenant_id}  {count}")

    print("\nunmatched global_rule_versions ids:")
    for version_id, _ in rows:
        print(f"  {version_id}")

    print(
        "\nACTION — these version rows reference a rule that no longer exists in\n"
        "their tenant. History is deliberately kept, so nothing is repaired\n"
        "automatically. Confirm the rows are genuinely orphaned, delete those\n"
        "specific ids, then validate the constraint. See this module's docstring."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
