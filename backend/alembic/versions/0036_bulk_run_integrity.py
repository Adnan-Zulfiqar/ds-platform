"""Bulk-run integrity and cancellation requests (review findings B-2, D-1, H-2).

* B-2: ``pipeline_bulk_runs.store_id`` was a single-column FK, so only the
  service stopped a run naming another tenant's store. It becomes a composite
  ``(tenant_id, store_id)`` FK, which needs ``uq_stores_tenant_id_id``. Still
  ``RESTRICT``; a hard tenant delete cascading through runs and stores is
  covered by tests and works with it.
* D-1: 0034 created ``created_at``/``updated_at`` nullable and without the
  ``created_at``/``deleted_at`` indexes the models declare. Brought into line
  here; 0034 itself is not edited. No NULLs can exist (server default), so
  ``SET NOT NULL`` cannot fail on real data.
* H-2: ``pipeline_bulk_run_cancel_requests`` records a cancel asked for while
  a worker holds the run row, so the HTTP request never waits on that lock.

Revision ID: 0036
Revises: 0035
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None

_BULK_TABLES = ("pipeline_bulk_runs", "pipeline_bulk_run_items")


def upgrade() -> None:
    # --- B-2 -----------------------------------------------------------------
    op.create_unique_constraint("uq_stores_tenant_id_id", "stores", ["tenant_id", "id"])
    op.drop_constraint("fk_pipeline_bulk_runs_store", "pipeline_bulk_runs", type_="foreignkey")
    op.create_foreign_key(
        "fk_pipeline_bulk_runs_store_tenant",
        "pipeline_bulk_runs",
        "stores",
        ["tenant_id", "store_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )

    # --- D-1 -----------------------------------------------------------------
    for table in _BULK_TABLES:
        for column in ("created_at", "updated_at"):
            op.alter_column(
                table,
                column,
                existing_type=sa.DateTime(timezone=True),
                nullable=False,
                existing_server_default=sa.text("now()"),
            )
        op.create_index(f"ix_{table}_created_at", table, ["created_at"])
        op.create_index(f"ix_{table}_deleted_at", table, ["deleted_at"])

    # --- H-2 -----------------------------------------------------------------
    op.create_table(
        "pipeline_bulk_run_cancel_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
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
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
            name="fk_pipeline_bulk_run_cancel_requests_tenant_id_tenants",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_pipeline_bulk_run_cancel_requests_requested_by_user_id_users",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["pipeline_bulk_runs.tenant_id", "pipeline_bulk_runs.id"],
            ondelete="CASCADE",
            name="fk_pipeline_bulk_run_cancel_requests_run_tenant",
        ),
        sa.UniqueConstraint(
            "tenant_id", "run_id", name="uq_pipeline_bulk_run_cancel_requests_tenant_run"
        ),
    )
    for column in ("tenant_id", "created_at", "deleted_at"):
        op.create_index(
            f"ix_pipeline_bulk_run_cancel_requests_{column}",
            "pipeline_bulk_run_cancel_requests",
            [column],
        )


def downgrade() -> None:
    op.drop_table("pipeline_bulk_run_cancel_requests")

    for table in reversed(_BULK_TABLES):
        op.drop_index(f"ix_{table}_deleted_at", table_name=table)
        op.drop_index(f"ix_{table}_created_at", table_name=table)
        for column in ("updated_at", "created_at"):
            op.alter_column(
                table,
                column,
                existing_type=sa.DateTime(timezone=True),
                nullable=True,
                existing_server_default=sa.text("now()"),
            )

    op.drop_constraint(
        "fk_pipeline_bulk_runs_store_tenant", "pipeline_bulk_runs", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_pipeline_bulk_runs_store",
        "pipeline_bulk_runs",
        "stores",
        ["store_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint("uq_stores_tenant_id_id", "stores", type_="unique")
