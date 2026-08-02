"""Product optimisation: SEO/marketplace/AI columns on products, product_versions.

Revision ID: 0011
Revises: 0010
Created: 2026-08-02 19:59:00+00:00

Additive only. No existing column on ``products`` is renamed, retyped, or
dropped, and no existing row's data changes — every new column is nullable
or carries a default. ``product_versions`` is a new table, patterned on
``ai_prompts``/``prompt_executions`` from migration ``0010``: a partial
unique index (``WHERE active``) guarantees at most one active version per
product, the same mechanism ``uq_ai_prompts_name_active`` uses.

**Two columns need a database-side default, not just the Python-side one on
the model.** ``products.tags`` and ``products.ai_status`` are NOT NULL, and
this migration runs against a table that already holds imported product rows
— ``ALTER TABLE ... ADD COLUMN ... NOT NULL`` needs a `server_default` to
backfill them, or Postgres rejects the statement outright. Every other new
column here is nullable, so this only applies to those two.

Autogenerate also proposed rewriting unique constraints into unique indexes
on ``email_verification_tokens``, ``refresh_tokens``, ``roles``,
``shopify_connections``, and ``tenants``. Removed by hand, matching the
precedent set in migrations ``0004`` and ``0010``: this is drift between the
live schema and how earlier migrations declared those constraints, not a
change this migration needs.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "product_versions",
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column(
            "source",
            sa.Enum("original", "ai_generated", name="product_version_source"),
            nullable=False,
        ),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("ai_provider", sa.String(length=64), nullable=True),
        sa.Column("prompt_execution_id", sa.UUID(), nullable=True),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_product_versions_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_product_versions_product_id_products"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["prompt_execution_id"],
            ["prompt_executions.id"],
            name=op.f("fk_product_versions_prompt_execution_id_prompt_executions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_product_versions_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_product_versions")),
        sa.UniqueConstraint(
            "product_id", "version_number", name="uq_product_versions_product_number"
        ),
    )
    op.create_index(
        op.f("ix_product_versions_created_at"), "product_versions", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_product_versions_deleted_at"), "product_versions", ["deleted_at"], unique=False
    )
    op.create_index(
        op.f("ix_product_versions_product_id"), "product_versions", ["product_id"], unique=False
    )
    op.create_index(
        op.f("ix_product_versions_tenant_id"), "product_versions", ["tenant_id"], unique=False
    )
    op.create_index(
        "ix_product_versions_tenant_product",
        "product_versions",
        ["tenant_id", "product_id"],
        unique=False,
    )
    op.create_index(
        "uq_product_versions_product_active",
        "product_versions",
        ["product_id"],
        unique=True,
        postgresql_where=sa.text("active"),
    )

    op.add_column("products", sa.Column("seo_title", sa.String(length=512), nullable=True))
    op.add_column("products", sa.Column("seo_description", sa.String(length=512), nullable=True))
    op.add_column("products", sa.Column("meta_keywords", sa.Text(), nullable=True))
    op.add_column("products", sa.Column("slug", sa.String(length=255), nullable=True))
    op.add_column("products", sa.Column("vendor", sa.String(length=255), nullable=True))
    op.add_column(
        "products",
        sa.Column(
            "tags",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    # Unlike `product_version_source` above — used inline in `create_table`,
    # where SQLAlchemy emits `CREATE TYPE` automatically as part of creating
    # the table — `ADD COLUMN` on an existing table has no table-creation
    # event to hang that on, so the enum type needs creating explicitly first.
    product_ai_status = sa.Enum("not_optimized", "optimized", "failed", name="product_ai_status")
    product_ai_status.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "products",
        sa.Column(
            "ai_status",
            product_ai_status,
            nullable=False,
            server_default=sa.text("'not_optimized'"),
        ),
    )
    op.add_column(
        "products", sa.Column("ai_last_generated_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("products", sa.Column("ai_provider", sa.String(length=64), nullable=True))
    op.add_column("products", sa.Column("ai_version", sa.Integer(), nullable=True))
    op.add_column("products", sa.Column("optimized_title", sa.String(length=512), nullable=True))
    op.add_column("products", sa.Column("optimized_description", sa.Text(), nullable=True))
    op.create_unique_constraint("uq_products_tenant_slug", "products", ["tenant_id", "slug"])


def downgrade() -> None:
    op.drop_constraint("uq_products_tenant_slug", "products", type_="unique")
    op.drop_column("products", "optimized_description")
    op.drop_column("products", "optimized_title")
    op.drop_column("products", "ai_version")
    op.drop_column("products", "ai_provider")
    op.drop_column("products", "ai_last_generated_at")
    op.drop_column("products", "ai_status")
    op.drop_column("products", "tags")
    op.drop_column("products", "vendor")
    op.drop_column("products", "slug")
    op.drop_column("products", "meta_keywords")
    op.drop_column("products", "seo_description")
    op.drop_column("products", "seo_title")

    op.drop_index(
        "uq_product_versions_product_active",
        table_name="product_versions",
        postgresql_where=sa.text("active"),
    )
    op.drop_index("ix_product_versions_tenant_product", table_name="product_versions")
    op.drop_index(op.f("ix_product_versions_tenant_id"), table_name="product_versions")
    op.drop_index(op.f("ix_product_versions_product_id"), table_name="product_versions")
    op.drop_index(op.f("ix_product_versions_deleted_at"), table_name="product_versions")
    op.drop_index(op.f("ix_product_versions_created_at"), table_name="product_versions")
    op.drop_table("product_versions")

    # Postgres enum types outlive the tables/columns that use them. Without
    # these drops the downgrade "succeeds" and the next upgrade fails with
    # "type ... already exists" — see migrations 0004 and 0010 for the same
    # fix applied to their own enum types.
    sa.Enum(name="product_version_source").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="product_ai_status").drop(op.get_bind(), checkfirst=True)
