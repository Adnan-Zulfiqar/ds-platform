"""Referential and uniqueness integrity for global rules (M3A-2).

Corrects a decision made in 0023 and adds the constraints the management API
needs. 0023 is already pushed, so nothing in it is rewritten -- this is
purely additive.

**The 0023 decision being corrected.** ``global_rule_versions.rule_id`` was
left without a foreign key on the reasoning that "history has to outlive the
rule it describes". That reasoning was sound but the premise was wrong:
pricing and shipping rules are **soft-deleted** (``PricingEngine.delete_rule``
calls ``soft_delete``; ``deleted_at`` is set and the row stays). A foreign key
therefore never destroys history here, and its absence bought nothing while
leaving a real hole -- a version row could reference a rule id belonging to
another tenant, with only application code preventing it.

The fix keeps the denormalised ``rule_id`` (every query reads it, and
``rule_kind`` selects which table it means) and adds two nullable typed
columns carrying **composite** foreign keys on ``(tenant_id, rule_id)``. The
composite is the point: a single-column FK would permit a version row in
tenant A referencing a rule in tenant B. A CHECK constraint keeps the typed
column and ``rule_id`` in agreement, so the denormalised copy cannot drift.

Also adds partial unique indexes so two concurrent requests cannot leave a
tenant with two active global defaults -- the database refuses the second
rather than the application hoping it noticed.

Revision ID: 0024
Revises: 0023
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- composite targets -------------------------------------------------
    # A composite FK needs a matching unique key on the referenced side. `id`
    # is already the primary key; this adds the (tenant_id, id) pair the FK
    # below points at. Redundant as a uniqueness statement, load-bearing as
    # an FK target.
    op.create_unique_constraint(
        "uq_pricing_rules_tenant_id_id", "pricing_rules", ["tenant_id", "id"]
    )
    op.create_unique_constraint(
        "uq_shipping_rules_tenant_id_id", "shipping_rules", ["tenant_id", "id"]
    )

    # --- typed, tenant-scoped references ----------------------------------
    op.add_column(
        "global_rule_versions",
        sa.Column("pricing_rule_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "global_rule_versions",
        sa.Column("shipping_rule_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    # Frozen copy of the whole rule at this version. The previous/new pairs
    # describe the delta; this answers "what were all the settings when this
    # price was calculated" without replaying the entire history.
    op.add_column(
        "global_rule_versions",
        sa.Column(
            "snapshot",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_foreign_key(
        "fk_global_rule_versions_pricing_rule",
        "global_rule_versions",
        "pricing_rules",
        ["tenant_id", "pricing_rule_id"],
        ["tenant_id", "id"],
        # RESTRICT, not CASCADE: rules are soft-deleted, so this never fires
        # in normal operation. If someone ever adds a hard delete, this makes
        # the audit trail refuse to be erased instead of silently vanishing.
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_global_rule_versions_shipping_rule",
        "global_rule_versions",
        "shipping_rules",
        ["tenant_id", "shipping_rule_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )

    # Backfill the typed column from the existing denormalised one, matching
    # only rules in the same tenant. Any row that cannot be matched is left
    # null rather than guessed at.
    op.execute(
        """
        UPDATE global_rule_versions v
           SET pricing_rule_id = r.id
        FROM pricing_rules r
        WHERE v.rule_kind = 'pricing'
          AND v.rule_id = r.id
          AND v.tenant_id = r.tenant_id
        """
    )
    op.execute(
        """
        UPDATE global_rule_versions v
           SET shipping_rule_id = r.id
        FROM shipping_rules r
        WHERE v.rule_kind = 'shipping'
          AND v.rule_id = r.id
          AND v.tenant_id = r.tenant_id
        """
    )

    # Exactly one typed reference, and it must agree with `rule_id`. NOT
    # VALID so the constraint governs new rows without failing the upgrade on
    # any pre-existing row the backfill could not match.
    op.execute(
        """
        ALTER TABLE global_rule_versions
        ADD CONSTRAINT ck_global_rule_versions_one_typed_reference
        CHECK (
            (rule_kind = 'pricing'  AND pricing_rule_id  = rule_id AND shipping_rule_id IS NULL)
         OR (rule_kind = 'shipping' AND shipping_rule_id = rule_id AND pricing_rule_id  IS NULL)
        ) NOT VALID
        """
    )

    # --- one active global default per tenant ------------------------------
    # Partial unique indexes rather than application checks: two concurrent
    # "activate" requests would both read "no active global rule" and both
    # write one. The database is the only place that race can be settled.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_pricing_rules_one_active_global
        ON pricing_rules (tenant_id)
        WHERE scope = 'global' AND is_active AND deleted_at IS NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_shipping_rules_one_active_global
        ON shipping_rules (tenant_id)
        WHERE scope = 'global' AND is_active AND deleted_at IS NULL
        """
    )

    # --- history read paths ------------------------------------------------
    op.create_index(
        "ix_global_rule_versions_tenant_kind_created",
        "global_rule_versions",
        ["tenant_id", "rule_kind", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_global_rule_versions_tenant_kind_created", table_name="global_rule_versions")
    op.execute("DROP INDEX IF EXISTS uq_shipping_rules_one_active_global")
    op.execute("DROP INDEX IF EXISTS uq_pricing_rules_one_active_global")
    op.execute(
        "ALTER TABLE global_rule_versions "
        "DROP CONSTRAINT IF EXISTS ck_global_rule_versions_one_typed_reference"
    )
    op.drop_constraint(
        "fk_global_rule_versions_shipping_rule", "global_rule_versions", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_global_rule_versions_pricing_rule", "global_rule_versions", type_="foreignkey"
    )
    op.drop_column("global_rule_versions", "snapshot")
    op.drop_column("global_rule_versions", "shipping_rule_id")
    op.drop_column("global_rule_versions", "pricing_rule_id")
    op.drop_constraint("uq_shipping_rules_tenant_id_id", "shipping_rules", type_="unique")
    op.drop_constraint("uq_pricing_rules_tenant_id_id", "pricing_rules", type_="unique")
