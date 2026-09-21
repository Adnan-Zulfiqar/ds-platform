"""Durable pipeline bulk runs and per-item outcomes (Phase 9 Stage 9).

Adds the unique (tenant_id, id) pairs on products and product_versions that
composite FKs require, then the run and item tables. Downgrade reverses
everything this revision adds, including those unique pairs.

Revision ID: 0034
Revises: 0033
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None

_RUN_STATUS = ("pending", "running", "completed", "partial", "failed", "cancelled")
_ITEM_STATE = ("pending", "succeeded", "failed", "skipped", "missing")


def upgrade() -> None:
    bind = op.get_bind()

    op.create_unique_constraint("uq_products_tenant_id_id", "products", ["tenant_id", "id"])
    op.create_unique_constraint(
        "uq_product_versions_tenant_id_id", "product_versions", ["tenant_id", "id"]
    )

    for values, name in (
        (_RUN_STATUS, "pipeline_bulk_run_status"),
        (_ITEM_STATE, "pipeline_bulk_item_state"),
    ):
        sa.Enum(*values, name=name).create(bind, checkfirst=True)

    status_enum = postgresql.ENUM(name="pipeline_bulk_run_status", create_type=False)
    state_enum = postgresql.ENUM(name="pipeline_bulk_item_state", create_type=False)

    op.create_table(
        "pipeline_bulk_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("status", status_enum, nullable=False, server_default="pending"),
        sa.Column("tone", sa.String(64), nullable=False),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "selection", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("claimed_by_task_id", sa.String(64), nullable=True),
        sa.Column("lease_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recovery_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enqueued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("succeeded_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("missing_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_reason", sa.String(500), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["store_id"], ["stores.id"], ondelete="RESTRICT", name="fk_pipeline_bulk_runs_store"
        ),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_pipeline_bulk_runs_tenant_idempotency"
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_pipeline_bulk_runs_tenant_id_id"),
    )
    op.create_index(
        "ix_pipeline_bulk_runs_tenant_created", "pipeline_bulk_runs", ["tenant_id", "created_at"]
    )
    op.create_index(
        "ix_pipeline_bulk_runs_tenant_status", "pipeline_bulk_runs", ["tenant_id", "status"]
    )
    op.create_index(
        "ix_pipeline_bulk_runs_pending_created",
        "pipeline_bulk_runs",
        ["created_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_pipeline_bulk_runs_running_heartbeat",
        "pipeline_bulk_runs",
        ["heartbeat_at"],
        postgresql_where=sa.text("status = 'running'"),
    )
    op.create_index(
        "uq_pipeline_bulk_runs_tenant_active",
        "pipeline_bulk_runs",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )

    op.create_table(
        "pipeline_bulk_run_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("submitted_product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("candidate_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("state", state_enum, nullable=False, server_default="pending"),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.String(500), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["pipeline_bulk_runs.tenant_id", "pipeline_bulk_runs.id"],
            ondelete="CASCADE",
            name="fk_pipeline_bulk_run_items_run_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "product_id"],
            ["products.tenant_id", "products.id"],
            ondelete="RESTRICT",
            name="fk_pipeline_bulk_run_items_product_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "candidate_version_id"],
            ["product_versions.tenant_id", "product_versions.id"],
            ondelete="RESTRICT",
            name="fk_pipeline_bulk_run_items_version_tenant",
        ),
        sa.UniqueConstraint(
            "run_id", "submitted_product_id", name="uq_pipeline_bulk_run_items_run_product"
        ),
        sa.CheckConstraint(
            "(state = 'succeeded' AND candidate_version_id IS NOT NULL) "
            "OR (state <> 'succeeded' AND candidate_version_id IS NULL)",
            name="succeeded_version",
        ),
    )
    op.create_index(
        "ix_pipeline_bulk_run_items_tenant_run",
        "pipeline_bulk_run_items",
        ["tenant_id", "run_id"],
    )
    op.create_index(
        "ix_pipeline_bulk_run_items_run_state",
        "pipeline_bulk_run_items",
        ["run_id", "state"],
    )


def downgrade() -> None:
    op.drop_index("ix_pipeline_bulk_run_items_run_state", table_name="pipeline_bulk_run_items")
    op.drop_index("ix_pipeline_bulk_run_items_tenant_run", table_name="pipeline_bulk_run_items")
    op.drop_table("pipeline_bulk_run_items")

    op.drop_index("uq_pipeline_bulk_runs_tenant_active", table_name="pipeline_bulk_runs")
    op.drop_index("ix_pipeline_bulk_runs_running_heartbeat", table_name="pipeline_bulk_runs")
    op.drop_index("ix_pipeline_bulk_runs_pending_created", table_name="pipeline_bulk_runs")
    op.drop_index("ix_pipeline_bulk_runs_tenant_status", table_name="pipeline_bulk_runs")
    op.drop_index("ix_pipeline_bulk_runs_tenant_created", table_name="pipeline_bulk_runs")
    op.drop_table("pipeline_bulk_runs")

    bind = op.get_bind()
    postgresql.ENUM(name="pipeline_bulk_item_state").drop(bind, checkfirst=True)
    postgresql.ENUM(name="pipeline_bulk_run_status").drop(bind, checkfirst=True)

    op.drop_constraint("uq_product_versions_tenant_id_id", "product_versions", type_="unique")
    op.drop_constraint("uq_products_tenant_id_id", "products", type_="unique")
