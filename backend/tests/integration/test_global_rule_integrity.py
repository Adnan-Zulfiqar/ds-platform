"""M3A-2 — schema integrity for global rules.

These assert the guarantees that live in the *database* rather than in
Python: the constraints added by migration 0024 are what settle races two
application processes cannot, so they are worth testing at the level they
are enforced.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import set_tenant_id
from app.models.pricing import (
    GlobalRuleKind,
    GlobalRuleVersion,
    PricingRule,
    PricingScope,
    PricingStrategy,
)

pytestmark = pytest.mark.integration


async def _tenant(db_session: AsyncSession) -> uuid.UUID:
    """Reuse an existing tenant; these tests care about rules, not signup."""
    tenant_id = (await db_session.execute(text("SELECT id FROM tenants LIMIT 1"))).scalar()
    if tenant_id is None:
        tenant_id = uuid.uuid4()
        await db_session.execute(
            text(
                "INSERT INTO tenants (id, name, slug, status, created_at, updated_at) "
                "VALUES (:id, 'Integrity', :slug, 'active', now(), now())"
            ),
            {"id": tenant_id, "slug": f"integrity-{uuid.uuid4().hex[:8]}"},
        )
        await db_session.flush()
    set_tenant_id(uuid.UUID(str(tenant_id)))
    return uuid.UUID(str(tenant_id))


def _rule(tenant_id: uuid.UUID, **overrides: object) -> PricingRule:
    values: dict[str, object] = {
        "tenant_id": tenant_id,
        "name": f"rule-{uuid.uuid4().hex[:8]}",
        "scope": PricingScope.GLOBAL,
        "strategy": PricingStrategy.PERCENTAGE_MARKUP,
        "markup_percent": Decimal("25"),
        "tiers": [],
        "is_active": True,
    }
    values.update(overrides)
    return PricingRule(**values)


class TestSchemaShape:
    async def test_migration_0024_created_its_objects(self, db_session: AsyncSession) -> None:
        """Built by running the migrations, not `create_all` -- so this
        asserts what production will actually have."""
        rows = (
            (
                await db_session.execute(
                    text(
                        "SELECT indexname FROM pg_indexes WHERE tablename IN "
                        "('pricing_rules','shipping_rules','global_rule_versions')"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert "uq_pricing_rules_one_active_global" in rows
        assert "uq_shipping_rules_one_active_global" in rows
        assert "ix_global_rule_versions_tenant_kind_created" in rows

    async def test_the_typed_reference_columns_exist(self, db_session: AsyncSession) -> None:
        columns = set(
            (
                await db_session.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'global_rule_versions'"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert {"pricing_rule_id", "shipping_rule_id", "snapshot"} <= columns

    async def test_the_check_constraint_exists(self, db_session: AsyncSession) -> None:
        names = (
            (
                await db_session.execute(
                    text(
                        "SELECT conname FROM pg_constraint "
                        "WHERE conrelid = 'global_rule_versions'::regclass"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert "ck_global_rule_versions_one_typed_reference" in names
        assert "fk_global_rule_versions_pricing_rule" in names


class TestActiveGlobalUniqueness:
    async def test_two_active_global_pricing_rules_are_refused(
        self, db_session: AsyncSession
    ) -> None:
        """Two concurrent activations would both read "no active global rule".
        Only the database can settle that, which is why this is an index and
        not an application check."""
        tenant_id = await _tenant(db_session)
        db_session.add(_rule(tenant_id))
        await db_session.flush()

        db_session.add(_rule(tenant_id))
        with pytest.raises(IntegrityError):
            await db_session.flush()
        await db_session.rollback()

    async def test_a_deactivated_global_rule_frees_the_slot(self, db_session: AsyncSession) -> None:
        tenant_id = await _tenant(db_session)
        first = _rule(tenant_id, is_active=False)
        db_session.add(first)
        await db_session.flush()
        db_session.add(_rule(tenant_id, is_active=True))
        await db_session.flush()  # no IntegrityError

    async def test_narrower_scopes_are_not_restricted(self, db_session: AsyncSession) -> None:
        """The index covers global rules only -- a tenant may have many
        active product overrides."""
        tenant_id = await _tenant(db_session)
        db_session.add(_rule(tenant_id, scope=PricingScope.STORE, store_id=None))
        db_session.add(_rule(tenant_id, scope=PricingScope.STORE, store_id=None))
        await db_session.flush()


class TestVersionIntegrity:
    async def test_a_version_cannot_reference_another_tenants_rule(
        self, db_session: AsyncSession
    ) -> None:
        """The composite foreign key. A single-column reference would let a
        version row in one tenant point at a rule in another, with only
        application code in the way."""
        tenant_id = await _tenant(db_session)
        rule = _rule(tenant_id)
        db_session.add(rule)
        await db_session.flush()

        foreign_tenant = uuid.uuid4()
        await db_session.execute(
            text(
                "INSERT INTO tenants (id, name, slug, status, created_at, updated_at) "
                "VALUES (:id, 'Other', :slug, 'active', now(), now())"
            ),
            {"id": foreign_tenant, "slug": f"other-{uuid.uuid4().hex[:8]}"},
        )
        await db_session.flush()

        with pytest.raises(IntegrityError):
            await db_session.execute(
                text(
                    "INSERT INTO global_rule_versions "
                    "(id, tenant_id, rule_kind, rule_id, pricing_rule_id, version, "
                    " changed_fields, previous_values, new_values, snapshot, is_active, "
                    " products_affected, created_at, updated_at) "
                    "VALUES (:id, :tenant, 'pricing', :rule, :rule, 1, "
                    " '[]'::jsonb, '{}'::jsonb, '{}'::jsonb, '{}'::jsonb, true, 0, now(), now())"
                ),
                {"id": uuid.uuid4(), "tenant": foreign_tenant, "rule": rule.id},
            )
        await db_session.rollback()

    async def test_duplicate_version_numbers_are_refused(self, db_session: AsyncSession) -> None:
        """What actually makes version numbers monotonic under concurrency:
        the read that picks the next number is advisory, this is binding."""
        tenant_id = await _tenant(db_session)
        rule = _rule(tenant_id)
        db_session.add(rule)
        await db_session.flush()

        for _ in range(2):
            db_session.add(
                GlobalRuleVersion(
                    tenant_id=tenant_id,
                    rule_kind=GlobalRuleKind.PRICING,
                    rule_id=rule.id,
                    pricing_rule_id=rule.id,
                    version=1,
                    changed_fields=[],
                    previous_values={},
                    new_values={},
                    snapshot={},
                    is_active=True,
                    products_affected=0,
                )
            )
        with pytest.raises(IntegrityError):
            await db_session.flush()
        await db_session.rollback()

    async def test_history_survives_a_soft_deleted_rule(self, db_session: AsyncSession) -> None:
        """Rules are soft-deleted, so the foreign key never blocks history
        from outliving one -- the row it points at is still there."""
        tenant_id = await _tenant(db_session)
        rule = _rule(tenant_id)
        db_session.add(rule)
        await db_session.flush()

        version = GlobalRuleVersion(
            tenant_id=tenant_id,
            rule_kind=GlobalRuleKind.PRICING,
            rule_id=rule.id,
            pricing_rule_id=rule.id,
            version=1,
            changed_fields=["name"],
            previous_values={},
            new_values={"name": rule.name},
            snapshot={"name": rule.name},
            is_active=True,
            products_affected=0,
        )
        db_session.add(version)
        await db_session.flush()

        from datetime import UTC, datetime

        rule.deleted_at = datetime.now(UTC)
        await db_session.flush()

        surviving = (
            (
                await db_session.execute(
                    select(GlobalRuleVersion).where(GlobalRuleVersion.rule_id == rule.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(surviving) == 1
        assert surviving[0].snapshot["name"] == rule.name


class TestBackwardCompatibility:
    async def test_a_pre_m3a_rule_still_prices_unchanged(self, db_session: AsyncSession) -> None:
        """Every column M3A added is nullable or server-defaulted to the
        pre-M3A implicit behaviour, so a rule created before this milestone
        must produce exactly the price it always did."""
        tenant_id = await _tenant(db_session)
        legacy_id = uuid.uuid4()
        await db_session.execute(
            text(
                "INSERT INTO pricing_rules "
                "(id, tenant_id, name, scope, strategy, priority, markup_percent, tiers, "
                " is_active, created_at, updated_at) "
                "VALUES (:id, :tenant, :name, 'store', 'percentage_markup', 100, 50, "
                " '[]'::jsonb, true, now(), now())"
            ),
            {"id": legacy_id, "tenant": tenant_id, "name": f"legacy-{uuid.uuid4().hex[:8]}"},
        )
        await db_session.flush()

        loaded = (
            await db_session.execute(select(PricingRule).where(PricingRule.id == legacy_id))
        ).scalar_one()
        assert loaded.rounding.value == "none"
        assert loaded.shipping_cost_handling.value == "include_in_price"
        assert loaded.version == 1
        assert loaded.margin_percent is None

        from app.services.pricing_engine import compute_sell_price

        assert compute_sell_price(cost=Decimal("10"), rule=loaded) == Decimal("15.0000")

    async def test_the_models_match_the_migrated_schema(self, db_session: AsyncSession) -> None:
        """Guards the failure mode of adding a model column and forgetting the
        migration: the suite builds its schema by running migrations, so a
        mismatch shows up here rather than in production."""

        def _columns(connection: object) -> set[str]:
            return {c["name"] for c in inspect(connection).get_columns("global_rule_versions")}

        actual = await db_session.run_sync(lambda sync: _columns(sync.connection()))
        expected = {c.name for c in GlobalRuleVersion.__table__.columns}
        assert expected <= actual
