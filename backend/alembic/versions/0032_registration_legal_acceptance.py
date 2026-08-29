"""Record legal acceptance at registration (AUTH-G1-R1).

Additive, and deliberately a **new** migration rather than an edit to `0031`:
that one has been reviewed and may already be applied somewhere, and rewriting
an applied migration is how two environments end up with different schemas under
the same revision id.

All four columns are nullable. Existing users accepted nothing recorded, and
back-filling a timestamp would be inventing evidence that somebody agreed to
something — the honest representation of "we did not capture this" is `NULL`.

Revision ID: 0032
Revises: 0031
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("users", sa.Column("terms_version", sa.String(length=64), nullable=True))
    op.add_column(
        "users", sa.Column("privacy_accepted_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("users", sa.Column("privacy_version", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "privacy_version")
    op.drop_column("users", "privacy_accepted_at")
    op.drop_column("users", "terms_version")
    op.drop_column("users", "terms_accepted_at")
