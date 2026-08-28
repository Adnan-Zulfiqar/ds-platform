"""Erasure must touch exactly one declared subject and nothing else.

Independent review of `29c7fdc` found the previous implementation conflated
three data subjects: erasing one person deleted the workspace's marketplace
credentials and blanked the buyer details on every order the company held. Most
of what follows exists to prove that cannot happen again, so the tests are
weighted towards what must *survive* rather than what must go.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database_identity import DatabaseIdentityError, current_database, require_database
from app.models.base import Base
from app.models.ebay import EbayConnection
from app.models.integration import AliExpressConnection
from app.models.order import Order
from app.models.refresh_token import RefreshToken
from app.models.shopify import ShopifyConnection
from app.models.store import Store
from app.models.user import User
from app.services.data_subject_erasure import (
    USER_REFERENCES,
    ErasureScope,
    PlatformUserErasureService,
    SubjectResolutionError,
    WorkspaceClosureService,
    login_throttle_keys,
)
from tests.integration.conftest import registration_payload

pytestmark = pytest.mark.integration


async def register(client: AsyncClient, email: str) -> tuple[uuid.UUID, uuid.UUID]:
    """Register a workspace. Returns (user_id, tenant_id)."""
    response = await client.post("/api/v1/auth/register", json=registration_payload(email=email))
    assert response.status_code == 201, response.text
    identity = response.json()["identity"]["user"]
    return uuid.UUID(identity["id"]), uuid.UUID(identity["tenantId"])


async def add_colleague(session: AsyncSession, tenant_id: uuid.UUID, email: str) -> uuid.UUID:
    """A second member of the same workspace, who must survive intact."""
    user = User(
        tenant_id=tenant_id,
        email=email,
        first_name="Colleague",
        last_name="Doe",
        password_hash="$argon2id$fake-hash-for-test",
        is_active=True,
        is_verified=True,
    )
    session.add(user)
    await session.flush()
    return user.id


async def add_ebay_connection(
    session: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> uuid.UUID:
    connection = EbayConnection(
        tenant_id=tenant_id,
        user_id=user_id,
        environment="production",
        ebay_user_id=f"ebay-{uuid.uuid4().hex[:12]}",
        ebay_username="seller",
        encrypted_access_token="ciphertext-access",
        encrypted_refresh_token="ciphertext-refresh",
    )
    session.add(connection)
    await session.flush()
    return connection.id


async def add_order(session: AsyncSession, tenant_id: uuid.UUID, store_id: uuid.UUID) -> uuid.UUID:
    order = Order(
        tenant_id=tenant_id,
        store_id=store_id,
        source="manual",
        external_id=f"ord-{uuid.uuid4().hex[:10]}",
        buyer_name="Jane Buyer",
        buyer_country="GB",
        recipient_name="Jane Buyer",
        recipient_phone="+441234567890",
        city="Ilford",
        province="Essex",
        postal_code="IG1 2UN",
        country_code="GB",
        currency="GBP",
        total_amount=25,
    )
    session.add(order)
    await session.flush()
    return order.id


async def add_store(session: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID) -> uuid.UUID:
    suffix = uuid.uuid4().hex[:10]
    store = Store(
        tenant_id=tenant_id,
        name=f"Test store {suffix}",
        slug=f"test-store-{suffix}",
        platform="shopify",
        connected_by_user_id=user_id,
    )
    session.add(store)
    await session.flush()
    return store.id


class TestDeclarationCompleteness:
    def test_the_declaration_covers_every_user_foreign_key_in_the_schema(self) -> None:
        """The guard against a future migration adding an eighteenth reference.

        The previous version cleared one of seventeen. This fails the moment a
        new `users.id` column appears without a decision about erasure.
        """
        declared = {(r.model.__tablename__, r.column.key) for r in USER_REFERENCES}
        actual = {
            (table.name, column.name)
            for table in Base.metadata.sorted_tables
            for column in table.columns
            for fk in column.foreign_keys
            if fk.column.table.name == "users"
        }
        assert actual - declared == set(), "user references with no erasure decision"
        assert declared - actual == set(), "declared references that are not real foreign keys"

    def test_every_reference_states_a_known_action(self) -> None:
        for reference in USER_REFERENCES:
            assert reference.action in {"delete", "clear"}
            assert reference.label


class TestResolutionSafety:
    async def test_the_same_address_in_two_tenants_resolves_per_tenant(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """The control for the review's arbitrary-user finding.

        A consultant with accounts at two clients is ordinary. The previous
        `.first()` over the whole table picked whichever row came back.
        """
        shared = f"shared-{uuid.uuid4().hex}@example.com"
        user_a, tenant_a = await register(client, shared)
        # Same address, second workspace.
        user_b = await add_colleague(
            db_session, (await register(client, f"o-{uuid.uuid4().hex}@example.com"))[1], shared
        )
        tenant_b = (
            await db_session.execute(select(User.tenant_id).where(User.id == user_b))
        ).scalar_one()

        service = PlatformUserErasureService(db_session)

        assert (await service.resolve_by_email(tenant_id=tenant_a, email=shared)).user_id == user_a
        assert (await service.resolve_by_email(tenant_id=tenant_b, email=shared)).user_id == user_b
        assert tenant_a != tenant_b

    async def test_an_address_in_another_tenant_is_not_found(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        email = f"elsewhere-{uuid.uuid4().hex}@example.com"
        await register(client, email)
        _, other_tenant = await register(client, f"other-{uuid.uuid4().hex}@example.com")

        service = PlatformUserErasureService(db_session)
        with pytest.raises(SubjectResolutionError):
            await service.resolve_by_email(tenant_id=other_tenant, email=email)

    async def test_an_unknown_address_fails_closed(self, db_session: AsyncSession) -> None:
        service = PlatformUserErasureService(db_session)
        with pytest.raises(SubjectResolutionError, match="No user with that address"):
            await service.resolve_by_email(tenant_id=uuid.uuid4(), email="nobody@example.com")

    async def test_a_user_id_from_another_tenant_is_refused(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        victim, _ = await register(client, f"v-{uuid.uuid4().hex}@example.com")
        _, other_tenant = await register(client, f"o-{uuid.uuid4().hex}@example.com")

        service = PlatformUserErasureService(db_session)
        with pytest.raises(SubjectResolutionError, match="No such user in that workspace"):
            await service.resolve_by_id(tenant_id=other_tenant, user_id=victim)


class TestPlatformUserScope:
    async def test_a_colleague_and_the_workspace_survive(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """The review's central finding, as a control.

        Erasing one employee must not delete the company's eBay credentials or
        blank its order book.
        """
        owner_email = f"owner-{uuid.uuid4().hex}@example.com"
        owner, tenant = await register(client, owner_email)
        colleague = await add_colleague(db_session, tenant, f"col-{uuid.uuid4().hex}@example.com")
        connection = await add_ebay_connection(db_session, tenant, owner)
        store = await add_store(db_session, tenant, owner)
        order = await add_order(db_session, tenant, store)

        service = PlatformUserErasureService(db_session)
        subject = await service.resolve_by_email(tenant_id=tenant, email=owner_email)
        await service.erase(subject)
        await db_session.flush()

        # The colleague is untouched.
        survivor = (await db_session.execute(select(User).where(User.id == colleague))).scalar_one()
        assert survivor.is_active is True
        assert survivor.password_hash is not None

        # The workspace keeps its eBay connection and its credentials; only the
        # link to the erased person is cleared.
        kept = (
            await db_session.execute(select(EbayConnection).where(EbayConnection.id == connection))
        ).scalar_one()
        assert kept.encrypted_access_token == "ciphertext-access"
        assert kept.user_id is None

        # The order book is untouched, country fields included.
        untouched = (await db_session.execute(select(Order).where(Order.id == order))).scalar_one()
        assert untouched.buyer_name == "Jane Buyer"
        assert untouched.buyer_country == "GB"
        assert untouched.country_code == "GB"
        assert untouched.recipient_phone == "+441234567890"
        assert untouched.postal_code == "IG1 2UN"

    async def test_it_clears_every_reference_naming_the_user(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        email = f"refs-{uuid.uuid4().hex}@example.com"
        user, tenant = await register(client, email)
        await add_ebay_connection(db_session, tenant, user)
        await add_store(db_session, tenant, user)

        service = PlatformUserErasureService(db_session)
        subject = await service.resolve_by_id(tenant_id=tenant, user_id=user)
        await service.erase(subject)
        await db_session.flush()

        # Not one of the seventeen may still name them.
        for reference in USER_REFERENCES:
            remaining = (
                (await db_session.execute(select(reference.model).where(reference.column == user)))
                .scalars()
                .all()
            )
            assert remaining == [], f"{reference.label} still references the erased user"

    async def test_it_anonymises_the_user_and_destroys_their_credentials(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        email = f"anon-{uuid.uuid4().hex}@example.com"
        user, tenant = await register(client, email)

        service = PlatformUserErasureService(db_session)
        await service.erase(await service.resolve_by_id(tenant_id=tenant, user_id=user))
        await db_session.flush()

        row = (await db_session.execute(select(User).where(User.id == user))).scalar_one()
        assert row.email.endswith("@erased.invalid")
        assert row.first_name is None and row.last_name is None
        assert row.password_hash is None
        assert row.is_active is False
        assert (
            await db_session.execute(select(RefreshToken).where(RefreshToken.user_id == user))
        ).scalars().all() == []

    async def test_a_second_run_changes_nothing_further(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        email = f"twice-{uuid.uuid4().hex}@example.com"
        user, tenant = await register(client, email)

        service = PlatformUserErasureService(db_session)
        subject = await service.resolve_by_id(tenant_id=tenant, user_id=user)
        await service.erase(subject)
        await db_session.flush()
        second = await service.erase(subject)
        await db_session.flush()

        assert second.counts["refresh_tokens"] == 0
        assert second.counts["user_roles"] == 0

    async def test_planning_writes_nothing(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        email = f"plan-{uuid.uuid4().hex}@example.com"
        user, tenant = await register(client, email)

        service = PlatformUserErasureService(db_session)
        plan = await service.plan(await service.resolve_by_id(tenant_id=tenant, user_id=user))

        assert plan.executed is False
        assert plan.scope is ErasureScope.PLATFORM_USER
        row = (await db_session.execute(select(User).where(User.id == user))).scalar_one()
        assert row.email == email
        assert row.is_active is True


class TestWorkspaceScope:
    async def test_it_deletes_every_marketplace_connection_including_ebay(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """`EbayConnection` was absent from the previous erasure path entirely."""
        owner, tenant = await register(client, f"ws-{uuid.uuid4().hex}@example.com")
        await add_ebay_connection(db_session, tenant, owner)
        store = await add_store(db_session, tenant, owner)
        db_session.add(
            ShopifyConnection(
                tenant_id=tenant,
                store_id=store,
                user_id=owner,
                shop_domain=f"s{uuid.uuid4().hex[:10]}.myshopify.com",
                encrypted_access_token="ciphertext",
            )
        )
        db_session.add(
            AliExpressConnection(
                tenant_id=tenant,
                user_id=owner,
                app_key="k",
                encrypted_app_secret="ciphertext",
                encrypted_access_token="ciphertext",
            )
        )
        await db_session.flush()

        outcome = await WorkspaceClosureService(db_session).erase(tenant)
        await db_session.flush()

        assert outcome.scope is ErasureScope.WORKSPACE
        for model, column in (
            (EbayConnection, EbayConnection.tenant_id),
            (ShopifyConnection, ShopifyConnection.tenant_id),
            (AliExpressConnection, AliExpressConnection.tenant_id),
        ):
            assert (
                await db_session.execute(select(model).where(column == tenant))
            ).scalars().all() == [], f"{model.__tablename__} survived workspace closure"

    async def test_it_never_reaches_another_tenant(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        owner_a, tenant_a = await register(client, f"a-{uuid.uuid4().hex}@example.com")
        owner_b, tenant_b = await register(client, f"b-{uuid.uuid4().hex}@example.com")
        await add_ebay_connection(db_session, tenant_a, owner_a)
        bystander = await add_ebay_connection(db_session, tenant_b, owner_b)

        await WorkspaceClosureService(db_session).erase(tenant_a)
        await db_session.flush()

        kept = (
            await db_session.execute(select(EbayConnection).where(EbayConnection.id == bystander))
        ).scalar_one()
        assert kept.encrypted_access_token == "ciphertext-access"
        survivor = (await db_session.execute(select(User).where(User.id == owner_b))).scalar_one()
        assert survivor.is_active is True

    async def test_it_erases_every_member(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        owner, tenant = await register(client, f"m-{uuid.uuid4().hex}@example.com")
        colleague = await add_colleague(db_session, tenant, f"c-{uuid.uuid4().hex}@example.com")

        await WorkspaceClosureService(db_session).erase(tenant)
        await db_session.flush()

        for user_id in (owner, colleague):
            row = (await db_session.execute(select(User).where(User.id == user_id))).scalar_one()
            assert row.is_active is False
            assert row.email.endswith("@erased.invalid")


class TestBuyerDataIsOutOfScope:
    async def test_neither_scope_touches_buyer_fields(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Buyer erasure is a blocker, not a silent side effect.

        The previous version blanked every order in the tenant to approximate
        it, destroying uninvolved customers' records. Both scopes must now leave
        order PII alone; a shopper's request goes to the merchant.
        """
        owner, tenant = await register(client, f"buy-{uuid.uuid4().hex}@example.com")
        store = await add_store(db_session, tenant, owner)
        order = await add_order(db_session, tenant, store)

        await WorkspaceClosureService(db_session).erase(tenant)
        await db_session.flush()

        row = (await db_session.execute(select(Order).where(Order.id == order))).scalar_one()
        assert row.buyer_name == "Jane Buyer"
        assert row.recipient_name == "Jane Buyer"
        assert row.recipient_phone == "+441234567890"
        assert row.buyer_country == "GB"
        assert row.country_code == "GB"
        assert row.city == "Ilford"
        assert row.postal_code == "IG1 2UN"

    def test_orders_still_carry_no_buyer_identifier(self) -> None:
        """Why exact buyer matching is impossible, asserted rather than claimed.

        If a buyer identifier is ever added, this fails and the blocker in
        `docs/governance/DATA_SUBJECT_REQUESTS.md` should be reconsidered.
        """
        columns = {column.name for column in Order.__table__.columns}
        for identifier in ("buyer_email", "buyer_id", "external_buyer_id", "customer_id"):
            assert identifier not in columns


class TestDatabaseGuard:
    async def test_it_reports_the_connected_database(self, db_session: AsyncSession) -> None:
        assert await current_database(db_session) != ""

    async def test_a_mismatched_expectation_is_refused(self, db_session: AsyncSession) -> None:
        with pytest.raises(DatabaseIdentityError, match="was expected"):
            await require_database(db_session, expected="definitely-not-this-database")

    async def test_the_matching_expectation_is_accepted(self, db_session: AsyncSession) -> None:
        actual = await current_database(db_session)
        assert await require_database(db_session, expected=actual) == actual

    async def test_a_production_name_is_refused_without_the_explicit_flag(
        self, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Pretend the connected database is the production one, without going
        # anywhere near it.
        actual = await current_database(db_session)
        monkeypatch.setattr(
            "app.core.database_identity.PRODUCTION_DATABASE_NAMES", frozenset({actual})
        )
        with pytest.raises(DatabaseIdentityError, match="production database"):
            await require_database(db_session, expected=actual)

        assert await require_database(db_session, expected=actual, allow_production=True) == actual


class TestTransactionSafety:
    async def test_a_failure_part_way_through_leaves_nothing_behind(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Rollback must restore every earlier mutation, not merely the last one.

        The erase touches seventeen tables in sequence. A failure at table four
        with tables one to three already written would leave a half-erased user
        who can no longer sign in but whose data is still present — the worst of
        both outcomes. The operator script wraps the whole thing in one
        transaction; this proves that actually restores everything.

        Uses a SAVEPOINT rather than a real commit because the fixture already
        holds an outer transaction it rolls back at the end.
        """
        email = f"rollback-{uuid.uuid4().hex}@example.com"
        user, tenant = await register(client, email)
        connection = await add_ebay_connection(db_session, tenant, user)
        store = await add_store(db_session, tenant, user)
        await db_session.flush()

        service = PlatformUserErasureService(db_session)
        subject = await service.resolve_by_id(tenant_id=tenant, user_id=user)

        original = service._affected
        calls = {"n": 0}

        async def failing(statement: object) -> int:
            calls["n"] += 1
            if calls["n"] > 3:
                raise RuntimeError("injected failure part way through erasure")
            return await original(statement)  # type: ignore[arg-type]

        service._affected = failing  # type: ignore[method-assign]

        savepoint = await db_session.begin_nested()
        with pytest.raises(RuntimeError, match="injected failure"):
            await service.erase(subject)
        await savepoint.rollback()

        # More than three statements ran before the failure, so this is a real
        # partial-write rollback rather than a no-op.
        assert calls["n"] > 3

        # Everything is exactly as it was.
        row = (await db_session.execute(select(User).where(User.id == user))).scalar_one()
        assert row.email == email
        assert row.is_active is True
        assert row.password_hash is not None

        kept = (
            await db_session.execute(select(EbayConnection).where(EbayConnection.id == connection))
        ).scalar_one()
        assert kept.user_id == user
        assert kept.encrypted_access_token == "ciphertext-access"

        kept_store = (await db_session.execute(select(Store).where(Store.id == store))).scalar_one()
        assert kept_store.connected_by_user_id == user

        assert (
            await db_session.execute(select(RefreshToken).where(RefreshToken.user_id == user))
        ).scalars().all() != []

    async def test_a_successful_erase_within_a_savepoint_can_still_be_undone(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A rehearsal must be able to run the real thing and discard it."""
        email = f"rehearse-{uuid.uuid4().hex}@example.com"
        user, tenant = await register(client, email)
        await db_session.flush()

        service = PlatformUserErasureService(db_session)
        subject = await service.resolve_by_id(tenant_id=tenant, user_id=user)

        savepoint = await db_session.begin_nested()
        await service.erase(subject)
        await savepoint.rollback()

        row = (await db_session.execute(select(User).where(User.id == user))).scalar_one()
        assert row.email == email
        assert row.is_active is True


class TestRedisKeys:
    def test_the_login_throttle_key_is_exact_and_carries_no_address(self) -> None:
        keys = login_throttle_keys("Person@Example.COM ")

        assert len(keys) == 1
        assert keys[0].startswith("login:email:")
        # Hashed, so the key space holds no addresses even in MONITOR output.
        assert "person@example.com" not in keys[0].lower()
        assert "@" not in keys[0]

    def test_it_normalises_case_and_padding_like_the_throttle_does(self) -> None:
        assert login_throttle_keys("  A@b.com ") == login_throttle_keys("a@b.com")

    def test_no_key_is_a_pattern(self) -> None:
        # A wildcard here would be a cross-tenant deletion waiting to happen.
        for key in login_throttle_keys("a@b.com"):
            assert "*" not in key and "?" not in key


class TestLogging:
    async def test_no_address_or_identifier_reaches_the_log_line(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        email = f"quiet-{uuid.uuid4().hex}@example.com"
        user, tenant = await register(client, email)

        service = PlatformUserErasureService(db_session)
        subject = await service.resolve_by_id(tenant_id=tenant, user_id=user)

        rendered = str(subject)
        assert email not in rendered
        assert "@" not in rendered
        assert str(user) in rendered and str(tenant) in rendered
