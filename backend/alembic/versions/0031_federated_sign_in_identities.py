"""Federated sign-in identities (AUTH-G1).

Additive. 0001-0030 are untouched: this adds one table and alters nothing that
exists, so `downgrade()` is a clean drop with no data migration to reverse.

**Provider-neutral rather than a `google_sub` column on `users`.** The column
would work today and would need migrating away the first time Apple or Microsoft
sign-in appears. A table costs one migration now and none later.

`provider` is a plain `VARCHAR` with a Python-side enum rather than a PostgreSQL
enum type. Adding a provider should be a code change, not a migration that
rewrites a live type — the existing enum columns in this schema all pass
`values_callable` precisely because that shape is awkward, and there is no
reason to take it on for a two-value list that will grow.

Two unique constraints, answering different questions:

* `uq_user_identities_provider_subject` — **global**. One Google account cannot
  sign in as two DropPilot users. Enforced here rather than by a lookup because
  check-then-insert races, and a cross-tenant existence check would be an oracle
  telling an attacker whether an address is registered in another workspace.
* `uq_user_identities_user_provider` — one identity per provider per user.

`ON DELETE CASCADE` on `user_id`: an identity without its user is a row that can
authenticate nobody, and leaving it behind would keep a Google subject linked to
a deleted account. Platform-user erasure deletes these rows explicitly as well,
so the cascade is a backstop rather than the mechanism.

Revision ID: 0031
Revises: 0030
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_identities",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        # The address the provider asserted at link time. Display only; nothing
        # matches on it, because an email is mutable and a subject is not.
        sa.Column("provider_email", sa.String(length=320), nullable=True),
        sa.Column("last_authenticated_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_user_identities_user_id_users",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("provider", "subject", name="uq_user_identities_provider_subject"),
        sa.UniqueConstraint("user_id", "provider", name="uq_user_identities_user_provider"),
    )
    op.create_index("ix_user_identities_user_id", "user_identities", ["user_id"])
    op.create_index(
        "ix_user_identities_provider_subject", "user_identities", ["provider", "subject"]
    )


def downgrade() -> None:
    op.drop_index("ix_user_identities_provider_subject", table_name="user_identities")
    op.drop_index("ix_user_identities_user_id", table_name="user_identities")
    op.drop_table("user_identities")
