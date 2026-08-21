"""Bulk rule application, pricing outcome on products, and 0024 validation.

Three additive changes plus one deferred cleanup:

* ``products`` gains the evidence of how its current price was reached --
  which rule and which *version*, the landed cost it was derived from, and
  the review reasons that held it back. On the product rather than derived on
  read, because a rule edited afterwards would otherwise make every past
  price unexplainable.
* ``rule_applications`` / ``rule_application_items`` record one confirmed
  bulk run and what happened to every item in it, including the ones nothing
  happened to -- "why is this product still at the old price" is the question
  merchants actually ask.
* Migration 0024 added ``ck_global_rule_versions_one_typed_reference`` as
  ``NOT VALID`` so the upgrade could not fail on a pre-existing row the
  backfill could not match. This validates it -- but only after proving every
  row satisfies it, and it re-runs the backfill first rather than assuming
  the table is empty in a real installation.

0023 and 0024 are not modified.

Revision ID: 0025
Revises: 0024
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

_MONEY = sa.Numeric(16, 4)

_APPLICATION_STATUS = ("pending", "running", "completed", "partial", "failed", "cancelled")
_ITEM_OUTCOME = ("applied", "skipped", "needs_review", "stale", "published", "failed")


def upgrade() -> None:
    bind = op.get_bind()

    # --- products: how the current price was reached -----------------------
    for name, column in (
        (
            "applied_pricing_rule_id",
            sa.Column("applied_pricing_rule_id", postgresql.UUID(as_uuid=True), nullable=True),
        ),
        (
            "applied_pricing_rule_version",
            sa.Column("applied_pricing_rule_version", sa.Integer(), nullable=True),
        ),
        (
            "applied_shipping_rule_id",
            sa.Column("applied_shipping_rule_id", postgresql.UUID(as_uuid=True), nullable=True),
        ),
        (
            "applied_shipping_rule_version",
            sa.Column("applied_shipping_rule_version", sa.Integer(), nullable=True),
        ),
        ("landed_cost", sa.Column("landed_cost", _MONEY, nullable=True)),
        ("landed_cost_fees", sa.Column("landed_cost_fees", _MONEY, nullable=True)),
        (
            "pricing_calculated_at",
            sa.Column("pricing_calculated_at", sa.DateTime(timezone=True), nullable=True),
        ),
    ):
        op.add_column("products", column)
        del name

    op.add_column(
        "products",
        sa.Column("needs_review", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "products",
        sa.Column(
            "pricing_review_reasons",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    # Drafts held for review are read as a filtered list; without this the
    # screen scans the whole catalogue.
    op.create_index(
        "ix_products_tenant_needs_review",
        "products",
        ["tenant_id", "needs_review"],
        postgresql_where=sa.text("needs_review"),
    )

    # --- bulk application --------------------------------------------------
    for values, name in (
        (_APPLICATION_STATUS, "rule_application_status"),
        (_ITEM_OUTCOME, "rule_application_item_outcome"),
    ):
        sa.Enum(*values, name=name).create(bind, checkfirst=True)

    status_enum = postgresql.ENUM(name="rule_application_status", create_type=False)
    outcome_enum = postgresql.ENUM(name="rule_application_item_outcome", create_type=False)

    op.create_table(
        "rule_applications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("status", status_enum, nullable=False, server_default="pending"),
        sa.Column("pricing_rule_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("pricing_rule_version", sa.Integer(), nullable=True),
        sa.Column("shipping_rule_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("shipping_rule_version", sa.Integer(), nullable=True),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "selection", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("applied_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("review_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_reason", sa.String(500), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"], ondelete="SET NULL"),
        # The idempotency guarantee: only a unique constraint can settle a
        # retry racing the original request.
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_rule_applications_tenant_idempotency"
        ),
    )
    op.create_index(
        "ix_rule_applications_tenant_created", "rule_applications", ["tenant_id", "created_at"]
    )
    op.create_index(
        "ix_rule_applications_tenant_status", "rule_applications", ["tenant_id", "status"]
    )

    op.create_table(
        "rule_application_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("application_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Nullable: a run must be able to report an id that no longer
        # resolves, and a NOT NULL foreign key cannot hold one.
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("variant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("outcome", outcome_enum, nullable=False),
        sa.Column("previous_price", _MONEY, nullable=True),
        sa.Column("new_price", _MONEY, nullable=True),
        sa.Column("landed_cost", _MONEY, nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("applied_rule_version", sa.Integer(), nullable=True),
        sa.Column(
            "review_reasons",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("message", sa.String(500), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["application_id"], ["rule_applications.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["variant_id"], ["product_variants.id"], ondelete="SET NULL"),
        # Makes a retry that re-enters the same run unable to double-write.
        sa.UniqueConstraint(
            "application_id", "product_id", "variant_id", name="uq_rule_application_items_target"
        ),
    )
    op.create_index(
        "ix_rule_application_items_tenant_app",
        "rule_application_items",
        ["tenant_id", "application_id"],
    )
    op.create_index(
        "ix_rule_application_items_tenant_product",
        "rule_application_items",
        ["tenant_id", "product_id"],
    )
    op.create_index(
        "ix_rule_application_items_application_id", "rule_application_items", ["application_id"]
    )
    op.create_index(
        "ix_rule_application_items_product_id", "rule_application_items", ["product_id"]
    )

    # --- validate the constraint 0024 deferred -----------------------------
    #
    # Re-run the backfill first: a real installation may have rows 0024 could
    # not match at the time, and validating against unmatched rows would fail
    # the upgrade. Anything still unmatched afterwards is repaired by
    # deriving the typed column from `rule_id` where a same-tenant rule
    # exists; rows referencing a rule that no longer exists in this tenant
    # are left alone and reported, not silently rewritten.
    op.execute(
        """
        UPDATE global_rule_versions v
           SET pricing_rule_id = r.id
          FROM pricing_rules r
         WHERE v.rule_kind = 'pricing'
           AND v.pricing_rule_id IS NULL
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
           AND v.shipping_rule_id IS NULL
           AND v.rule_id = r.id
           AND v.tenant_id = r.tenant_id
        """
    )

    unmatched = bind.execute(
        sa.text(
            """
            SELECT count(*) FROM global_rule_versions
             WHERE (rule_kind = 'pricing'  AND pricing_rule_id  IS DISTINCT FROM rule_id)
                OR (rule_kind = 'shipping' AND shipping_rule_id IS DISTINCT FROM rule_id)
            """
        )
    ).scalar_one()

    if unmatched == 0:
        op.execute(
            "ALTER TABLE global_rule_versions "
            "VALIDATE CONSTRAINT ck_global_rule_versions_one_typed_reference"
        )
    else:
        # Deliberately not fatal. Leaving the constraint NOT VALID keeps it
        # governing new rows while an operator investigates the orphans; a
        # migration that refused to run would take the whole deploy with it
        # over historical data that is already inert.
        op.execute(
            sa.text(
                "DO $$ BEGIN RAISE WARNING "
                "'[0025] % global_rule_versions row(s) reference a rule that no longer exists "
                "in their tenant; ck_global_rule_versions_one_typed_reference left NOT VALID.', "
                ":count; END $$"
            ).bindparams(count=unmatched)
        )


def downgrade() -> None:
    op.drop_table("rule_application_items")
    op.drop_table("rule_applications")

    bind = op.get_bind()
    for name in ("rule_application_item_outcome", "rule_application_status"):
        sa.Enum(name=name).drop(bind, checkfirst=True)

    op.drop_index("ix_products_tenant_needs_review", table_name="products")
    for column in (
        "pricing_review_reasons",
        "needs_review",
        "pricing_calculated_at",
        "landed_cost_fees",
        "landed_cost",
        "applied_shipping_rule_version",
        "applied_shipping_rule_id",
        "applied_pricing_rule_version",
        "applied_pricing_rule_id",
    ):
        op.drop_column("products", column)

    # The CHECK constraint stays validated. Un-validating it would mean
    # asserting the data might be bad when it has been proven good, and
    # PostgreSQL offers no "invalidate" anyway short of dropping and
    # recreating -- which 0024's own downgrade already handles.
