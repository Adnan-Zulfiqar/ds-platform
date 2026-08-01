"""AliExpress app secret is platform-owned — stop requiring it on tenant rows.

Revision ID: 0009
Revises: 0008

Merchants authorize DropPilot's AliExpress application. The app secret lives in
environment configuration, not in ``aliexpress_connections``. Tenant rows keep
only encrypted seller access/refresh tokens.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "aliexpress_connections",
        "encrypted_app_secret",
        existing_type=sa.String(length=1024),
        nullable=True,
    )
    # Drop any previously stored platform/tenant app secrets from connection rows.
    op.execute(sa.text("UPDATE aliexpress_connections SET encrypted_app_secret = NULL"))


def downgrade() -> None:
    # Downgrade cannot reconstruct secrets. Fill a non-empty placeholder so the
    # NOT NULL constraint can be restored; reconnect is required after rollback.
    op.execute(
        sa.text(
            "UPDATE aliexpress_connections "
            "SET encrypted_app_secret = 'downgrade-placeholder' "
            "WHERE encrypted_app_secret IS NULL"
        )
    )
    op.alter_column(
        "aliexpress_connections",
        "encrypted_app_secret",
        existing_type=sa.String(length=1024),
        nullable=False,
    )
