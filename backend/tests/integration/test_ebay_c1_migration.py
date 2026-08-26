"""EBAY-C1 — migration 0030, exercised rather than asserted.

The schema claims in this milestone are only worth as much as the migration
that produces them, and a migration is the one artefact that cannot be fixed
forward once it has run somewhere. So the cycle is *run*: upgrade, downgrade,
re-upgrade, against a real PostgreSQL, with the constraints and the enum
inspected from the catalogue afterwards.

Enum types are the specific trap. Dropped in the wrong order they linger after a
downgrade, and the re-upgrade then fails on ``CREATE TYPE`` — a failure that
only ever appears on the second deploy attempt, which is the worst moment to
discover it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings

pytestmark = pytest.mark.integration

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION = "*0030_ebay_seller_connection.py"


def _config() -> Config:
    config = Config(str(_BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.database.sync_dsn)
    return config


def _source() -> str:
    return next((_BACKEND_ROOT / "alembic" / "versions").glob(_MIGRATION)).read_text(
        encoding="utf-8"
    )


class TestRevisionGraph:
    def test_the_history_has_one_head_and_0030_is_it(self) -> None:
        """A second head is a merge nobody resolved, and stops ``upgrade head``."""
        from alembic.script import ScriptDirectory

        heads = ScriptDirectory.from_config(_config()).get_heads()
        assert list(heads) == ["0030"], f"expected the single head 0030, got {heads}"

    def test_it_declares_0029_as_its_parent(self) -> None:
        source = _source()
        assert 'revision = "0030"' in source
        assert 'down_revision = "0029"' in source

    def test_it_is_additive_only(self) -> None:
        """It must not alter or drop anything that already exists.

        A new table and a new enum are safe to deploy while the previous release
        is still serving traffic. An ``ALTER`` on a live table is not, and would
        need a different rollout than the one this milestone documents.
        """
        upgrade = _source().split("def downgrade")[0]
        for destructive in ("op.drop_column", "op.drop_table", "op.alter_column"):
            assert destructive not in upgrade, f"{destructive} in the upgrade path"

    def test_the_downgrade_is_real(self) -> None:
        """A ``pass`` body is a migration that cannot be rolled back.

        Discovering that during an incident is the wrong time.
        """
        downgrade = _source().split("def downgrade")[1]
        assert "op.drop_table" in downgrade
        assert "pass" not in downgrade.split("\n")[1:3]


class TestTheCycle:
    def test_downgrade_and_re_upgrade_leave_the_schema_usable(self) -> None:
        """The whole point: run it, do not reason about it.

        Ends back at head so the rest of the session runs against the schema it
        expects, whatever order pytest chose.
        """
        from alembic import command

        config = _config()
        command.downgrade(config, "0029")

        engine = sa.create_engine(settings.database.sync_dsn)
        try:
            with engine.connect() as connection:
                assert (
                    connection.execute(
                        sa.text("SELECT to_regclass('public.ebay_connections')")
                    ).scalar()
                    is None
                ), "the table survived its own downgrade"
                assert (
                    connection.execute(
                        sa.text(
                            "SELECT count(*) FROM pg_type WHERE typname = 'ebay_connection_status'"
                        )
                    ).scalar()
                    == 0
                ), "the enum type lingered; the next upgrade would fail on CREATE TYPE"

            command.upgrade(config, "0030")

            with engine.connect() as connection:
                assert (
                    connection.execute(
                        sa.text("SELECT to_regclass('public.ebay_connections')")
                    ).scalar()
                    is not None
                ), "the table did not come back"
        finally:
            engine.dispose()
            command.upgrade(config, "head")


class TestTheShapeItProduces:
    """Read from the catalogue, so these describe the deployed schema.

    Asserting against the models instead would test the models against
    themselves and prove nothing about what a migration actually creates.
    """

    async def test_the_tenant_constraint_allows_one_connection_per_workspace(
        self, db_session: AsyncSession
    ) -> None:
        constraints = await _unique_constraints(db_session)
        assert "uq_ebay_connections_tenant_id" in constraints
        assert constraints["uq_ebay_connections_tenant_id"] == ["tenant_id"]

    async def test_the_seller_constraint_is_global_not_per_tenant(
        self, db_session: AsyncSession
    ) -> None:
        """One eBay account belongs to one workspace, enforced by the database.

        Scoping this to ``(tenant_id, ebay_user_id)`` would let the same seller
        be attached twice, and the second workspace would then compete for the
        same listings and orders. Global also means the conflict is settled
        without any query that reads across tenants.
        """
        constraints = await _unique_constraints(db_session)
        assert constraints["uq_ebay_connections_ebay_user_id"] == ["ebay_user_id"]

    async def test_the_status_enum_holds_exactly_the_four_states(
        self, db_session: AsyncSession
    ) -> None:
        """Persisted by value, not by member name.

        SQLAlchemy writes member *names* unless ``values_callable`` is passed,
        and the mismatch fails every insert at runtime rather than at import.
        """
        labels = (
            (
                await db_session.execute(
                    sa.text(
                        "SELECT enumlabel FROM pg_enum e "
                        "JOIN pg_type t ON t.oid = e.enumtypid "
                        "WHERE t.typname = 'ebay_connection_status' ORDER BY enumlabel"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert list(labels) == ["connected", "error", "pending", "reconnect_required"]

    async def test_deleting_a_workspace_takes_its_credentials_with_it(
        self, db_session: AsyncSession
    ) -> None:
        """Cascade, not ``SET NULL`` and not ``RESTRICT``.

        ``SET NULL`` would leave an encrypted eBay token belonging to nobody —
        credential material with no owner and no route to erasure. ``RESTRICT``
        would make closing an account fail on a foreign key.

        Cast to text because ``confdeltype`` is PostgreSQL's internal ``"char"``
        type, which the driver hands back as bytes.
        """
        rule = (
            await db_session.execute(
                sa.text(
                    "SELECT confdeltype::text FROM pg_constraint "
                    "WHERE conrelid = 'ebay_connections'::regclass AND contype = 'f' "
                    "AND conname LIKE '%tenant_id%'"
                )
            )
        ).scalar_one()
        assert rule == "c", "tenant deletion would orphan an encrypted eBay token"

    async def test_the_refresh_sweep_has_an_index_to_use(self, db_session: AsyncSession) -> None:
        """Leading with ``status``, then the expiry.

        The query a future background refresh runs is "connections that are
        connected and expiring soon". Without this it is a sequential scan over
        every workspace on the platform, every time it runs.
        """
        indexes = (
            (
                await db_session.execute(
                    sa.text("SELECT indexdef FROM pg_indexes WHERE tablename = 'ebay_connections'")
                )
            )
            .scalars()
            .all()
        )
        assert any(
            "status" in definition and "access_token_expires_at" in definition
            for definition in indexes
        ), f"no index covers the refresh sweep: {list(indexes)}"


async def _unique_constraints(session: AsyncSession) -> dict[str, list[str]]:
    rows = (
        await session.execute(
            sa.text(
                "SELECT c.conname, a.attname "
                "FROM pg_constraint c "
                "JOIN unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord) ON true "
                "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum "
                "WHERE c.conrelid = 'ebay_connections'::regclass AND c.contype = 'u' "
                "ORDER BY c.conname, k.ord"
            )
        )
    ).all()
    grouped: dict[str, list[str]] = {}
    for name, column in rows:
        grouped.setdefault(name, []).append(column)
    return grouped
