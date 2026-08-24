"""eBay marketplace account deletion compliance ledger (EBAY-C0).

Additive. 0001-0028 are not modified, and nothing existing is altered — this
adds one table and two enum types and nothing else, so a rollback is a clean
drop with no data migration to reverse.

**The table deliberately holds no personal data.** eBay's notification carries
``username``, ``userId`` and ``eiasToken``; none of them has a column here. A
ledger row proves *that* a deletion instruction arrived and was carried out,
which is what a compliance auditor needs, without becoming a second place the
person's identifiers live — and therefore a second thing that would have to be
erased on the next request. ``payload_digest`` (SHA-256 of the exact received
bytes) supplies identity without content.

``notification_id`` is UNIQUE. That constraint is the idempotency mechanism, not
a tidiness measure: two concurrent deliveries of the same notification race to
insert, PostgreSQL refuses the loser, and the loser acknowledges rather than
repeating destructive processing. An application-level "check then insert"
would leave a window between the two statements where both see nothing.

Revision ID: 0029
Revises: 0028
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None

_VERIFICATION = postgresql.ENUM(
    "verified",
    "rejected",
    name="ebay_notification_verification",
    create_type=False,
)
_PROCESSING = postgresql.ENUM(
    "received",
    "completed",
    "failed",
    name="ebay_notification_processing",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    _VERIFICATION.create(bind, checkfirst=True)
    _PROCESSING.create(bind, checkfirst=True)

    op.create_table(
        "ebay_compliance_notifications",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
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
        sa.Column("notification_id", sa.String(length=255), nullable=False),
        sa.Column("topic", sa.String(length=120), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column("event_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("publish_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload_digest", sa.String(length=64), nullable=False),
        sa.Column("verification_status", _VERIFICATION, nullable=False),
        sa.Column("processing_status", _PROCESSING, nullable=False),
        sa.Column("first_received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("receipt_count", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("erased_record_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("outcome_code", sa.String(length=64), nullable=True),
        sa.Column("outcome_detail", sa.Text(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_ebay_compliance_notifications_notification_id",
        "ebay_compliance_notifications",
        ["notification_id"],
    )
    op.create_index(
        "ix_ebay_compliance_notifications_topic",
        "ebay_compliance_notifications",
        ["topic"],
    )
    op.create_index(
        "ix_ebay_compliance_notifications_status",
        "ebay_compliance_notifications",
        ["processing_status", "last_received_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ebay_compliance_notifications_status",
        table_name="ebay_compliance_notifications",
    )
    op.drop_index(
        "ix_ebay_compliance_notifications_topic",
        table_name="ebay_compliance_notifications",
    )
    op.drop_constraint(
        "uq_ebay_compliance_notifications_notification_id",
        "ebay_compliance_notifications",
        type_="unique",
    )
    op.drop_table("ebay_compliance_notifications")
    # Dropped after the table, and only then: an enum type still referenced by
    # a column cannot be dropped, and leaving orphan types behind would make a
    # re-upgrade fail on `CREATE TYPE`.
    bind = op.get_bind()
    _PROCESSING.drop(bind, checkfirst=True)
    _VERIFICATION.drop(bind, checkfirst=True)
