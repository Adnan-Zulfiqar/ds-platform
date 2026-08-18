"""Global pricing and shipping rules (M3A).

Extends ``pricing_rules`` with the strategies, guardrails and fee inputs the
global-rules screen configures, adds ``shipping_rules`` for supplier-shipping
selection, and adds ``global_rule_versions`` as an append-only audit trail.

Two decisions worth recording:

* Every added column is nullable or carries a server default, so the upgrade
  is safe on a populated table and existing rules keep behaving exactly as
  they did -- ``rounding`` defaults to ``none`` and
  ``shipping_cost_handling`` to ``include_in_price``, which is what the
  pre-M3A code did implicitly.
* ``global_rule_versions.rule_id`` is deliberately **not** a foreign key.
  History has to outlive the rule it describes; a cascade delete would erase
  the audit trail at exactly the moment someone wants to read it.

Revision ID: 0023
Revises: 0022
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

_MONEY = sa.Numeric(16, 4)
_PERCENT = sa.Numeric(8, 4)

_PRICE_ROUNDING = ("none", "ninety_nine", "ninety_five", "whole")
_SHIPPING_COST_HANDLING = ("include_in_price", "charge_separately", "absorb_from_profit")
_SHIPPING_SELECTION = ("cheapest", "cheapest_tracked", "fastest", "fastest_under_cost")
_SHIPPING_NO_MATCH = ("needs_review", "block_publish", "cheapest_available")
_RULE_KIND = ("pricing", "shipping")


def upgrade() -> None:
    bind = op.get_bind()

    # --- new enum types ----------------------------------------------------
    for values, name in (
        (_PRICE_ROUNDING, "price_rounding"),
        (_SHIPPING_COST_HANDLING, "shipping_cost_handling"),
        (_SHIPPING_SELECTION, "shipping_selection_strategy"),
        (_SHIPPING_NO_MATCH, "shipping_no_match_behaviour"),
        (_RULE_KIND, "global_rule_kind"),
    ):
        sa.Enum(*values, name=name).create(bind, checkfirst=True)

    # Reference the types created above rather than letting `add_column` /
    # `create_table` emit CREATE TYPE again -- SQLAlchemy does that implicitly
    # for a bare `sa.Enum`, which fails once the type already exists.
    price_rounding = postgresql.ENUM(name="price_rounding", create_type=False)
    shipping_cost_handling = postgresql.ENUM(name="shipping_cost_handling", create_type=False)
    shipping_selection = postgresql.ENUM(name="shipping_selection_strategy", create_type=False)
    shipping_no_match = postgresql.ENUM(name="shipping_no_match_behaviour", create_type=False)
    rule_kind = postgresql.ENUM(name="global_rule_kind", create_type=False)

    # --- extend the existing enums rather than replacing them --------------
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block on older
    # servers; `IF NOT EXISTS` keeps the migration re-runnable on a partially
    # upgraded database.
    op.execute("ALTER TYPE pricing_strategy ADD VALUE IF NOT EXISTS 'target_margin'")
    op.execute("ALTER TYPE pricing_strategy ADD VALUE IF NOT EXISTS 'hybrid'")
    op.execute("ALTER TYPE pricing_scope ADD VALUE IF NOT EXISTS 'variant'")

    # --- pricing_rules -----------------------------------------------------
    op.add_column("pricing_rules", sa.Column("margin_percent", _PERCENT, nullable=True))
    op.add_column("pricing_rules", sa.Column("min_profit_per_variant", _MONEY, nullable=True))
    op.add_column("pricing_rules", sa.Column("min_price", _MONEY, nullable=True))
    op.add_column("pricing_rules", sa.Column("duty_percent", _PERCENT, nullable=True))
    op.add_column("pricing_rules", sa.Column("fees_fixed", _MONEY, nullable=True))
    op.add_column("pricing_rules", sa.Column("compare_at_percent", _PERCENT, nullable=True))
    op.add_column(
        "pricing_rules",
        sa.Column("variant_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_pricing_rules_variant",
        "pricing_rules",
        "product_variants",
        ["variant_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_pricing_rules_variant_id", "pricing_rules", ["variant_id"])
    op.add_column(
        "pricing_rules",
        sa.Column("rounding", price_rounding, nullable=False, server_default="none"),
    )
    op.add_column(
        "pricing_rules",
        sa.Column(
            "shipping_cost_handling",
            shipping_cost_handling,
            nullable=False,
            server_default="include_in_price",
        ),
    )
    op.add_column(
        "pricing_rules",
        sa.Column(
            "applies_to_new_imports", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
    )
    op.add_column(
        "pricing_rules",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )

    # --- price_changes provenance -----------------------------------------
    op.add_column("price_changes", sa.Column("landed_cost", _MONEY, nullable=True))
    op.add_column("price_changes", sa.Column("rule_version", sa.Integer(), nullable=True))
    op.add_column(
        "price_changes",
        sa.Column("application_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_price_changes_application_id", "price_changes", ["application_id"])

    # --- shipping_rules ----------------------------------------------------
    op.create_table(
        "shipping_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column(
            "scope",
            postgresql.ENUM(name="pricing_scope", create_type=False),
            nullable=False,
            server_default="global",
        ),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("category_id", sa.String(64), nullable=True),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("variant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("destination_country", sa.String(2), nullable=True),
        sa.Column(
            "selection_strategy",
            shipping_selection,
            nullable=False,
            server_default="cheapest_tracked",
        ),
        sa.Column("max_delivery_days", sa.Integer(), nullable=True),
        sa.Column("max_shipping_cost", _MONEY, nullable=True),
        sa.Column(
            "tracking_required", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "preferred_carriers",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "blocked_carriers",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "no_match_behaviour", shipping_no_match, nullable=False, server_default="needs_review"
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["variant_id"], ["product_variants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_shipping_rules_tenant_name"),
    )
    op.create_index(
        "ix_shipping_rules_tenant_scope", "shipping_rules", ["tenant_id", "scope", "priority"]
    )
    op.create_index("ix_shipping_rules_store_id", "shipping_rules", ["store_id"])
    op.create_index("ix_shipping_rules_product_id", "shipping_rules", ["product_id"])
    op.create_index("ix_shipping_rules_variant_id", "shipping_rules", ["variant_id"])

    # --- global_rule_versions ---------------------------------------------
    op.create_table(
        "global_rule_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rule_kind", rule_kind, nullable=False),
        sa.Column("rule_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "changed_fields",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "previous_values",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "new_values", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("changed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("products_affected", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["changed_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "tenant_id", "rule_kind", "rule_id", "version", name="uq_global_rule_version"
        ),
    )
    op.create_index(
        "ix_global_rule_versions_tenant_rule",
        "global_rule_versions",
        ["tenant_id", "rule_kind", "rule_id"],
    )
    op.create_index(
        "ix_global_rule_versions_tenant_created",
        "global_rule_versions",
        ["tenant_id", "created_at"],
    )
    op.create_index("ix_global_rule_versions_rule_id", "global_rule_versions", ["rule_id"])


def downgrade() -> None:
    op.drop_table("global_rule_versions")
    op.drop_table("shipping_rules")

    op.drop_index("ix_price_changes_application_id", table_name="price_changes")
    op.drop_column("price_changes", "application_id")
    op.drop_column("price_changes", "rule_version")
    op.drop_column("price_changes", "landed_cost")

    op.drop_column("pricing_rules", "version")
    op.drop_column("pricing_rules", "applies_to_new_imports")
    op.drop_column("pricing_rules", "shipping_cost_handling")
    op.drop_column("pricing_rules", "rounding")
    op.drop_index("ix_pricing_rules_variant_id", table_name="pricing_rules")
    op.drop_constraint("fk_pricing_rules_variant", "pricing_rules", type_="foreignkey")
    op.drop_column("pricing_rules", "variant_id")
    op.drop_column("pricing_rules", "compare_at_percent")
    op.drop_column("pricing_rules", "fees_fixed")
    op.drop_column("pricing_rules", "duty_percent")
    op.drop_column("pricing_rules", "min_price")
    op.drop_column("pricing_rules", "min_profit_per_variant")
    op.drop_column("pricing_rules", "margin_percent")

    bind = op.get_bind()
    for name in (
        "global_rule_kind",
        "shipping_no_match_behaviour",
        "shipping_selection_strategy",
        "shipping_cost_handling",
        "price_rounding",
    ):
        sa.Enum(name=name).drop(bind, checkfirst=True)

    # `pricing_strategy` and `pricing_scope` keep their added members. Removing
    # an enum value in PostgreSQL means recreating the type and rewriting every
    # column that uses it, and a downgrade that rewrites live pricing data to
    # undo a *widening* is a worse risk than leaving two unused members behind.
