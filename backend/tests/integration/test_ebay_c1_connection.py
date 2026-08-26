"""EBAY-C1 — the seller connection, against real PostgreSQL, Redis and Fernet.

eBay's network is faked at the HTTP boundary and nothing else is. The database,
the migrations, the Redis state store, the row lock and the encryption authority
are all the production ones, because every claim worth making here is about what
is *persisted* and what is *enforced* — and a mocked database proves neither.

**No eBay credential is used and no live eBay request is made.**

The claims:

* state is single-use, tenant-bound, unguessable, and consumed *before* the
  authorization code is spent;
* tokens are encrypted at rest, and no token-shaped column escapes that;
* identity comes from eBay's immutable ``userId``, never the mutable username;
* one eBay seller cannot be attached to two workspaces, and finding that out
  reveals nothing about the workspace that holds it;
* refresh is serialised by a real row lock — proved against a control that
  removes the lock and shows the duplication it prevents;
* a revoked grant becomes "reconnect", never a retry loop;
* the EBAY-C0 deletion contract erases the connection, idempotently, without
  touching another tenant.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from app.core.config import EbayEnvironment, settings
from app.core.context import set_tenant_id
from app.core.encryption import decrypt
from app.core.redis import RedisPurpose, get_redis
from app.integrations.ebay.connection import (
    RECONNECT_MISSING_REFRESH,
    RECONNECT_REVOKED,
    EbayConnectionService,
)
from app.integrations.ebay.deletion import DeletionSubject, EbayAccountDeletionProcessor
from app.integrations.ebay.exceptions import (
    EbayOAuthStateError,
    EbaySellerAlreadyLinkedError,
    EbayTokenExchangeError,
    EbayTokenRevokedError,
)
from app.models.ebay import EbayConnection, EbayConnectionStatus
from tests.integration.ebay_c1_live import (
    ACCESS_TOKEN,
    REFRESH_TOKEN,
    REFRESHED_ACCESS_TOKEN,
    SELLER_USER_ID,
    SELLER_USERNAME,
    FakeEbay,
    SessionFactory,
    connect_seller,
    expire_access_token,
    install,
    live_tenants,
    load_connection,
    own_connection,
)
from tests.integration.live_locks import (
    HANG_GUARD_SECONDS,
    backend_pid,
    wait_until_blocked,
)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def fresh_redis() -> AsyncIterator[None]:
    """A Redis client bound to *this* test's event loop, and no stale state.

    The client cache is a module-level singleton, so a client built on one
    test's loop raises "Event loop is closed" on the next. The same fixture the
    EBAY-C0 suite uses, for the same reason.
    """
    from app.core.redis import close_redis_clients

    await close_redis_clients()
    client = get_redis(RedisPurpose.CACHE)
    stale = await client.keys("ebay:oauth:state:*")
    if stale:
        await client.delete(*stale)
    yield
    await close_redis_clients()


@pytest.fixture
def ebay(monkeypatch: pytest.MonkeyPatch) -> FakeEbay:
    fake = FakeEbay()
    install(monkeypatch, fake)
    return fake


async def count_connections(factory: SessionFactory, tenant_id: uuid.UUID) -> int:
    """Count straight from the table, with no soft-delete predicate.

    Deliberately raw SQL: the compliance requirement is physical erasure, and a
    count through the ORM would happily report zero for a row that is merely
    marked deleted.
    """
    async with factory() as session:
        return int(
            (
                await session.execute(
                    sa.text("SELECT count(*) FROM ebay_connections WHERE tenant_id = :t"),
                    {"t": str(tenant_id)},
                )
            ).scalar_one()
        )


# ---------------------------------------------------------------------------
# State: CSRF, replay, expiry, cross-tenant capture
# ---------------------------------------------------------------------------


class TestOAuthState:
    async def test_the_raw_state_token_is_never_a_redis_key(self, ebay: FakeEbay) -> None:
        """A Redis dump must not hand over usable state credentials.

        Only the SHA-256 of the token is the key, so what is stored is not
        something an attacker could present back to the callback.
        """
        async with live_tenants() as (tenants, factory):
            async with factory() as session:
                set_tenant_id(tenants[0])
                _, state = await EbayConnectionService(session).begin_connection(user_id=None)

            client = get_redis(RedisPurpose.CACHE)
            keys = [key async for key in client.scan_iter(match="ebay:oauth:state:*")]
            assert keys, "no state was stored"
            assert all(state not in str(key) for key in keys)
            assert await client.get(EbayConnectionService._state_key(state)) is not None

    async def test_state_is_single_use(self, ebay: FakeEbay) -> None:
        """The replay defence, and the reason the state is consumed first."""
        async with live_tenants() as (tenants, factory):
            tenant = tenants[0]
            async with factory() as session:
                set_tenant_id(tenant)
                service = EbayConnectionService(session)
                _, state = await service.begin_connection(user_id=None)
                await service.complete_connection(code="first", state_token=state)
                await session.commit()

            exchanges = len(ebay.grants("authorization_code"))

            async with factory() as session:
                set_tenant_id(tenant)
                with pytest.raises(EbayOAuthStateError):
                    await EbayConnectionService(session).complete_connection(
                        code="second", state_token=state
                    )

            assert len(ebay.grants("authorization_code")) == exchanges, (
                "a replayed callback reached eBay's token service"
            )

    async def test_an_unknown_state_never_reaches_ebay(self, ebay: FakeEbay) -> None:
        """A forged callback must not spend a code or contact the provider."""
        async with live_tenants() as (tenants, factory):
            async with factory() as session:
                set_tenant_id(tenants[0])
                with pytest.raises(EbayOAuthStateError):
                    await EbayConnectionService(session).complete_connection(
                        code="code", state_token="never-issued-by-this-server"
                    )
            assert ebay.token_requests == []

    async def test_an_expired_state_is_refused(self, ebay: FakeEbay) -> None:
        """Expiry is enforced by the Redis TTL; dropping the key is that state.

        The real TTL is asserted too, so a change that forgot to set one would
        fail here rather than leaving state valid forever.
        """
        async with live_tenants() as (tenants, factory):
            tenant = tenants[0]
            async with factory() as session:
                set_tenant_id(tenant)
                _, state = await EbayConnectionService(session).begin_connection(user_id=None)

            client = get_redis(RedisPurpose.CACHE)
            key = EbayConnectionService._state_key(state)
            ttl = await client.ttl(key)
            assert 0 < ttl <= settings.ebay.oauth_state_ttl_seconds

            await client.delete(key)
            async with factory() as session:
                set_tenant_id(tenant)
                with pytest.raises(EbayOAuthStateError):
                    await EbayConnectionService(session).complete_connection(
                        code="code", state_token=state
                    )

    async def test_the_callback_binds_to_the_state_record_not_the_caller(
        self, ebay: FakeEbay
    ) -> None:
        """Workspace B cannot steer workspace A's consent into its own account.

        The state record is the authority; whatever tenant context the callback
        happens to run under is overwritten from it. A tenant read from a URL is
        a tenant an attacker can type.
        """
        async with live_tenants(2) as (tenants, factory):
            owner, attacker = tenants
            async with factory() as session:
                set_tenant_id(owner)
                _, state = await EbayConnectionService(session).begin_connection(user_id=None)

            async with factory() as session:
                set_tenant_id(attacker)
                connection = await EbayConnectionService(session).complete_connection(
                    code="code", state_token=state
                )
                await session.commit()
                assert connection.tenant_id == owner

            assert await count_connections(factory, attacker) == 0
            assert await count_connections(factory, owner) == 1

    async def test_a_reconfigured_environment_invalidates_state_in_flight(
        self, ebay: FakeEbay, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A grant minted against one eBay estate is useless against the other.

        Sandbox and production have separate credentials and separate accounts,
        so completing here would store a token that could never work.
        """
        async with live_tenants() as (tenants, factory):
            tenant = tenants[0]
            async with factory() as session:
                set_tenant_id(tenant)
                _, state = await EbayConnectionService(session).begin_connection(user_id=None)

            monkeypatch.setattr(settings.ebay, "environment", EbayEnvironment.SANDBOX)
            async with factory() as session:
                set_tenant_id(tenant)
                with pytest.raises(EbayOAuthStateError):
                    await EbayConnectionService(session).complete_connection(
                        code="code", state_token=state
                    )
            assert ebay.grants("authorization_code") == []

    async def test_two_simultaneous_replays_admit_at_most_one(self, ebay: FakeEbay) -> None:
        """``GETDEL`` rather than get-then-delete.

        Two callbacks arriving together would both pass a non-atomic check, and
        only one of them should ever proceed.
        """
        async with live_tenants() as (tenants, factory):
            tenant = tenants[0]
            async with factory() as session:
                set_tenant_id(tenant)
                _, state = await EbayConnectionService(session).begin_connection(user_id=None)

            async def attempt() -> bool:
                async with factory() as session:
                    set_tenant_id(tenant)
                    try:
                        await EbayConnectionService(session).complete_connection(
                            code="code", state_token=state
                        )
                    except EbayOAuthStateError:
                        return False
                    await session.commit()
                    return True

            outcomes = await asyncio.gather(attempt(), attempt())
            assert sum(outcomes) == 1, f"{sum(outcomes)} callbacks consumed one state"


# ---------------------------------------------------------------------------
# The exchange itself: what goes on the wire, and what lands in the column
# ---------------------------------------------------------------------------


class TestTokenExchange:
    async def test_the_authorization_code_is_encoded_exactly_once(self, ebay: FakeEbay) -> None:
        """The bug this integration is most likely to have.

        FastAPI decodes the query parameter and httpx encodes the form body, so
        the value must travel through the service untouched. A second encoding
        pass turns ``v^1.1#i^1`` into ``v%5E1.1%23i%5E1`` on the wire and eBay
        answers ``invalid_grant`` — which reads like an expired code rather than
        a mangled one, and costs a day.
        """
        raw_code = "v^1.1#i^1#f^0#code+with/reserved=chars&more"
        async with live_tenants() as (tenants, factory):
            await connect_seller(factory, tenants[0], code=raw_code)

            sent = ebay.grants("authorization_code")[0]
            assert sent["code"] == raw_code
            assert sent["grant_type"] == "authorization_code"
            assert sent["redirect_uri"] == "DropPilot-TestOnly-RuName"
            assert not sent["redirect_uri"].startswith("http"), "a URL was sent, not the RuName"

    async def test_the_client_secret_is_not_in_the_request_body(self, ebay: FakeEbay) -> None:
        """eBay's documented scheme is HTTP Basic, which is also what keeps the
        secret out of anything that logs a form body."""
        async with live_tenants() as (tenants, factory):
            await connect_seller(factory, tenants[0])
            sent = ebay.grants("authorization_code")[0]
            assert "client_secret" not in sent
            assert "client_id" not in sent

    async def test_tokens_are_encrypted_at_rest(self, ebay: FakeEbay) -> None:
        """Read straight from the columns, not through the model.

        The proof has to be that what sits in the database is not the plaintext,
        and that the production encryption authority is what turns one into the
        other.
        """
        async with live_tenants() as (tenants, factory):
            tenant = tenants[0]
            await connect_seller(factory, tenant)

            async with factory() as session:
                stored_access, stored_refresh = (
                    await session.execute(
                        sa.select(
                            EbayConnection.encrypted_access_token,
                            EbayConnection.encrypted_refresh_token,
                        ).where(EbayConnection.tenant_id == tenant)
                    )
                ).one()

            assert stored_access and stored_refresh
            assert ACCESS_TOKEN not in stored_access, "the access token is stored in clear text"
            assert REFRESH_TOKEN not in stored_refresh, "the refresh token is in clear text"
            assert decrypt(stored_access) == ACCESS_TOKEN
            assert decrypt(stored_refresh) == REFRESH_TOKEN

    async def test_every_token_column_in_the_table_is_an_encrypted_one(
        self, ebay: FakeEbay
    ) -> None:
        """Structural, against the migrated schema rather than the models.

        A column that cannot hold a plaintext token cannot leak one, and this
        fails on the *migration* that adds one — the change that would otherwise
        reach production unnoticed.
        """
        async with live_tenants() as (_, factory):
            async with factory() as session:
                columns = {
                    row[0]
                    for row in (
                        await session.execute(
                            sa.text(
                                "SELECT column_name FROM information_schema.columns "
                                "WHERE table_name = 'ebay_connections'"
                            )
                        )
                    ).all()
                }

        assert "encrypted_access_token" in columns, "the schema under test is not C1's"
        suspicious = {
            name
            for name in columns
            if "token" in name and "expires" not in name and not name.startswith("encrypted_")
        }
        assert not suspicious, f"unencrypted token-shaped columns: {sorted(suspicious)}"

    async def test_a_connection_without_a_refresh_token_asks_to_reconnect(
        self, ebay: FakeEbay
    ) -> None:
        """Rather than failing every future call with a generic error.

        eBay always issues one for this grant; if a response ever arrives
        without one, the honest state is "reconnect", not "broken".
        """
        ebay.issue_refresh_token = False
        async with live_tenants() as (tenants, factory):
            tenant = tenants[0]
            await connect_seller(factory, tenant)
            await expire_access_token(factory, tenant)

            async with own_connection(tenant) as session:
                service = EbayConnectionService(session)
                connection = await service.get_connection()
                assert connection is not None
                with pytest.raises(EbayTokenRevokedError):
                    await service.access_token_for(connection)
                await session.commit()

            stored = await load_connection(factory, tenant)
            assert stored.status is EbayConnectionStatus.RECONNECT_REQUIRED
            assert stored.reconnect_reason == RECONNECT_MISSING_REFRESH


# ---------------------------------------------------------------------------
# Identity, and one seller per workspace
# ---------------------------------------------------------------------------


class TestSellerIdentity:
    async def test_the_immutable_user_id_is_what_is_stored(self, ebay: FakeEbay) -> None:
        async with live_tenants() as (tenants, factory):
            tenant = tenants[0]
            await connect_seller(factory, tenant)

            stored = await load_connection(factory, tenant)
            assert stored.ebay_user_id == SELLER_USER_ID
            assert stored.ebay_username == SELLER_USERNAME
            assert stored.marketplace_id == "EBAY_GB"
            assert stored.account_type == "BUSINESS"
            assert stored.status is EbayConnectionStatus.CONNECTED
            assert stored.connected_at is not None
            assert stored.is_usable

    async def test_a_renamed_seller_reconnects_into_the_same_row(self, ebay: FakeEbay) -> None:
        """Why the identity is the id and not the name.

        A seller who renames on eBay is the same seller. Keyed on the username,
        a reconnect would create a second connection and orphan the first — and
        the deletion contract would then no longer find what it must erase.
        """
        async with live_tenants() as (tenants, factory):
            tenant = tenants[0]
            first = await connect_seller(factory, tenant)

            ebay.username = "renamed-on-ebay"
            second = await connect_seller(factory, tenant)

            assert first == second, "a rename created a second connection"
            assert await count_connections(factory, tenant) == 1
            stored = await load_connection(factory, tenant)
            assert stored.ebay_user_id == SELLER_USER_ID
            assert stored.ebay_username == "renamed-on-ebay"

    async def test_one_seller_cannot_be_linked_to_two_workspaces(self, ebay: FakeEbay) -> None:
        """Settled by the database, not by a lookup.

        A check-then-insert races; the global unique constraint does not, and it
        means no ordinary code path ever has to read another tenant's row.
        """
        async with live_tenants(2) as (tenants, factory):
            holder, other = tenants
            await connect_seller(factory, holder)

            with pytest.raises(EbaySellerAlreadyLinkedError):
                await connect_seller(factory, other)

            assert await count_connections(factory, other) == 0
            assert await count_connections(factory, holder) == 1

    async def test_the_conflict_discloses_nothing_about_the_other_workspace(
        self, ebay: FakeEbay
    ) -> None:
        """No existence oracle.

        A message naming the holder would let anyone probe which eBay sellers
        use this platform — the same disclosure a 404-not-403 policy exists to
        prevent everywhere else.
        """
        async with live_tenants(2) as (tenants, factory):
            holder, prober = tenants
            await connect_seller(factory, holder)

            with pytest.raises(EbaySellerAlreadyLinkedError) as caught:
                await connect_seller(factory, prober)

            rendered = f"{caught.value.message} {caught.value.details}".lower()
            assert str(holder) not in rendered
            assert "ebay-c1-" not in rendered
            assert SELLER_USER_ID.lower() not in rendered

    async def test_a_token_for_a_different_account_is_refused_on_verify(
        self, ebay: FakeEbay
    ) -> None:
        """Silently rewriting the identity would move a connection between eBay
        accounts with nobody having consented to it."""
        async with live_tenants() as (tenants, factory):
            tenant = tenants[0]
            await connect_seller(factory, tenant)

            ebay.seller_user_id = "a-completely-different-seller"
            async with own_connection(tenant) as session:
                with pytest.raises(EbayTokenRevokedError):
                    await EbayConnectionService(session).verify()

            stored = await load_connection(factory, tenant)
            assert stored.ebay_user_id == SELLER_USER_ID, "the identity was overwritten"

    async def test_verify_refreshes_the_display_metadata(self, ebay: FakeEbay) -> None:
        """The stored status is only ever as fresh as the last call that used it."""
        async with live_tenants() as (tenants, factory):
            tenant = tenants[0]
            await connect_seller(factory, tenant)

            ebay.username = "trading-name-changed"
            ebay.marketplace_id = "EBAY_US"
            async with own_connection(tenant) as session:
                verified = await EbayConnectionService(session).verify()
                assert verified is not None
                await session.commit()

            stored = await load_connection(factory, tenant)
            assert stored.ebay_username == "trading-name-changed"
            assert stored.marketplace_id == "EBAY_US"
            assert stored.last_verified_at is not None


# ---------------------------------------------------------------------------
# Refresh, and the lock that serialises it
# ---------------------------------------------------------------------------


class TestRefresh:
    async def test_a_valid_token_is_reused_without_calling_ebay(self, ebay: FakeEbay) -> None:
        async with live_tenants() as (tenants, factory):
            tenant = tenants[0]
            await connect_seller(factory, tenant)

            async with own_connection(tenant) as session:
                service = EbayConnectionService(session)
                connection = await service.get_connection()
                assert connection is not None
                token = await service.access_token_for(connection)

            assert token == ACCESS_TOKEN
            assert ebay.refresh_count == 0, "refreshed a token that was still valid"

    async def test_a_token_inside_the_margin_is_refreshed(self, ebay: FakeEbay) -> None:
        """Refreshed early on purpose: a token that expires mid-request fails a
        call that had no reason to fail."""
        async with live_tenants() as (tenants, factory):
            tenant = tenants[0]
            await connect_seller(factory, tenant)
            await expire_access_token(
                factory, tenant, seconds=settings.ebay.token_refresh_margin_seconds - 30
            )

            async with own_connection(tenant) as session:
                service = EbayConnectionService(session)
                connection = await service.get_connection()
                assert connection is not None
                token = await service.access_token_for(connection)
                await session.commit()

            assert token == REFRESHED_ACCESS_TOKEN
            assert ebay.refresh_count == 1
            sent = ebay.grants("refresh_token")[0]
            assert sent["refresh_token"] == REFRESH_TOKEN
            assert "sell.inventory" in sent["scope"], "the granted scope was not replayed"

            stored = await load_connection(factory, tenant)
            assert stored.last_refreshed_at is not None
            assert decrypt(stored.encrypted_access_token or "") == REFRESHED_ACCESS_TOKEN
            assert stored.access_token_expires_at is not None
            assert stored.access_token_expires_at > datetime.now(UTC) + timedelta(hours=1)

    async def test_concurrent_refreshes_are_serialised_by_the_row_lock(
        self, ebay: FakeEbay
    ) -> None:
        """Two connections, real contention, exactly one refresh.

        The first caller is held inside eBay's token call *after* it has taken
        the row lock, and the test then waits for PostgreSQL itself to report
        the second caller blocked. That turns a race into a rendezvous: this
        cannot pass merely because the two callers failed to overlap.
        """
        async with live_tenants() as (tenants, factory):
            tenant = tenants[0]
            await connect_seller(factory, tenant)
            await expire_access_token(factory, tenant)

            release = asyncio.Event()
            reached = asyncio.Event()
            ebay.hold_next_refresh = release
            ebay.refresh_reached = reached

            second_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()

            async def first() -> str:
                async with own_connection(tenant) as session:
                    service = EbayConnectionService(session)
                    connection = await service.get_connection()
                    assert connection is not None
                    token = await service.access_token_for(connection)
                    await session.commit()
                    return token

            async def second() -> str:
                async with own_connection(tenant) as session:
                    # Held until the first caller owns the lock, so this one is
                    # guaranteed to arrive at the lock second rather than first.
                    await reached.wait()
                    second_pid.set_result(await backend_pid(session))
                    service = EbayConnectionService(session)
                    connection = await service.get_connection()
                    assert connection is not None
                    token = await service.access_token_for(connection)
                    await session.commit()
                    return token

            async def referee() -> None:
                await wait_until_blocked(factory, await second_pid)
                release.set()

            first_token, second_token, _ = await asyncio.gather(first(), second(), referee())

            assert ebay.refresh_count == 1, (
                f"{ebay.refresh_count} refreshes ran; the row lock did not serialise"
            )
            assert first_token == second_token == REFRESHED_ACCESS_TOKEN

    async def test_without_the_lock_the_same_race_refreshes_twice(
        self, ebay: FakeEbay, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The control for the test above.

        Without it, a green serialisation test could just mean the two callers
        never actually contended. Here the lock is removed and the duplication
        it prevents is demonstrated — so the previous test is evidence about the
        lock rather than about the scheduler.
        """
        from app.repositories.ebay import EbayConnectionRepository

        async def unlocked(
            self: EbayConnectionRepository, connection_id: uuid.UUID
        ) -> EbayConnection | None:
            result = await self.session.execute(
                self._base_query()
                .where(EbayConnection.id == connection_id)
                .execution_options(populate_existing=True)
            )
            return result.scalar_one_or_none()

        monkeypatch.setattr(EbayConnectionRepository, "lock_for_update", unlocked)

        async with live_tenants() as (tenants, factory):
            tenant = tenants[0]
            await connect_seller(factory, tenant)
            await expire_access_token(factory, tenant)

            release = asyncio.Event()
            second_seen = asyncio.Event()
            ebay.hold_next_refresh = release
            ebay.refresh_reached = asyncio.Event()
            ebay.second_refresh_seen = second_seen

            async def caller() -> None:
                async with own_connection(tenant) as session:
                    service = EbayConnectionService(session)
                    connection = await service.get_connection()
                    assert connection is not None
                    await service.access_token_for(connection)
                    await session.commit()

            async def referee() -> None:
                # With no lock to block on, the second caller reaches eBay
                # unimpeded; release the first once it has, so neither hangs.
                await asyncio.wait_for(second_seen.wait(), timeout=HANG_GUARD_SECONDS)
                release.set()

            await asyncio.gather(caller(), caller(), referee())

            assert ebay.refresh_count == 2, (
                "the control did not reproduce the duplicate refresh, so the "
                "serialisation test above proves nothing"
            )

    async def test_a_revoked_refresh_token_asks_to_reconnect_and_drops_the_ciphertext(
        self, ebay: FakeEbay
    ) -> None:
        """eBay revokes on password and login-name changes. Never retryable.

        The dead ciphertext is dropped rather than kept: a revoked token cannot
        be used again, so retaining it is retention with no purpose.
        """
        async with live_tenants() as (tenants, factory):
            tenant = tenants[0]
            await connect_seller(factory, tenant)
            await expire_access_token(factory, tenant)

            ebay.token_status = 400
            ebay.token_error = "invalid_grant"

            async with own_connection(tenant) as session:
                service = EbayConnectionService(session)
                connection = await service.get_connection()
                assert connection is not None
                with pytest.raises(EbayTokenRevokedError):
                    await service.access_token_for(connection)
                await session.commit()

            stored = await load_connection(factory, tenant)
            assert stored.status is EbayConnectionStatus.RECONNECT_REQUIRED
            assert stored.reconnect_reason == RECONNECT_REVOKED
            assert stored.encrypted_access_token is None
            assert stored.encrypted_refresh_token is None
            assert stored.needs_reconnect
            assert not stored.is_usable

    async def test_a_connection_awaiting_reconnect_does_not_retry(self, ebay: FakeEbay) -> None:
        """Hammering a revoked grant is how an integration gets rate-limited."""
        async with live_tenants() as (tenants, factory):
            tenant = tenants[0]
            await connect_seller(factory, tenant)
            await expire_access_token(factory, tenant)

            ebay.token_status = 400
            async with own_connection(tenant) as session:
                service = EbayConnectionService(session)
                connection = await service.get_connection()
                assert connection is not None
                with pytest.raises(EbayTokenRevokedError):
                    await service.access_token_for(connection)
                await session.commit()

            attempts = ebay.refresh_count
            async with own_connection(tenant) as session:
                service = EbayConnectionService(session)
                connection = await service.get_connection()
                assert connection is not None
                with pytest.raises(EbayTokenRevokedError):
                    await service.access_token_for(connection)

            assert ebay.refresh_count == attempts, "a revoked grant was retried"

    async def test_a_temporary_failure_is_not_treated_as_revocation(self, ebay: FakeEbay) -> None:
        """A 5xx from eBay is an outage, not consent withdrawn.

        The fake returns the harder case on purpose: a 503 whose body still
        carries ``invalid_grant``. Classifying on the error code alone would
        read that as revocation and ask every merchant to re-authorise because
        eBay had a bad afternoon, so the verdict requires a 4xx as well.
        """
        async with live_tenants() as (tenants, factory):
            tenant = tenants[0]
            await connect_seller(factory, tenant)
            await expire_access_token(factory, tenant)

            ebay.token_status = 503
            async with own_connection(tenant) as session:
                service = EbayConnectionService(session)
                connection = await service.get_connection()
                assert connection is not None
                with pytest.raises(EbayTokenExchangeError):
                    await service.access_token_for(connection)
                await session.commit()

            stored = await load_connection(factory, tenant)
            assert stored.status is EbayConnectionStatus.CONNECTED
            assert stored.encrypted_refresh_token is not None

    async def test_reconnecting_after_revocation_restores_the_connection(
        self, ebay: FakeEbay
    ) -> None:
        async with live_tenants() as (tenants, factory):
            tenant = tenants[0]
            await connect_seller(factory, tenant)
            async with factory() as session:
                await session.execute(
                    sa.update(EbayConnection)
                    .where(EbayConnection.tenant_id == tenant)
                    .values(
                        status=EbayConnectionStatus.RECONNECT_REQUIRED,
                        reconnect_reason=RECONNECT_REVOKED,
                        encrypted_access_token=None,
                        encrypted_refresh_token=None,
                        access_token_expires_at=None,
                    )
                )
                await session.commit()

            await connect_seller(factory, tenant)

            stored = await load_connection(factory, tenant)
            assert stored.status is EbayConnectionStatus.CONNECTED
            assert stored.reconnect_reason is None
            assert stored.encrypted_refresh_token is not None
            assert await count_connections(factory, tenant) == 1


# ---------------------------------------------------------------------------
# Disconnect
# ---------------------------------------------------------------------------


class TestDisconnect:
    async def test_disconnect_removes_the_row_and_its_ciphertext(self, ebay: FakeEbay) -> None:
        """A hard delete, not ``deleted_at``.

        A soft-deleted row is retained credential material for an account the
        merchant has told the platform to forget.
        """
        async with live_tenants() as (tenants, factory):
            tenant = tenants[0]
            await connect_seller(factory, tenant)

            async with own_connection(tenant) as session:
                assert await EbayConnectionService(session).disconnect() is True
                await session.commit()

            assert await count_connections(factory, tenant) == 0

    async def test_disconnect_is_idempotent(self, ebay: FakeEbay) -> None:
        """A double click must not produce a confusing failure."""
        async with live_tenants() as (tenants, factory):
            tenant = tenants[0]
            await connect_seller(factory, tenant)

            async with own_connection(tenant) as session:
                assert await EbayConnectionService(session).disconnect() is True
                await session.commit()
            async with own_connection(tenant) as session:
                assert await EbayConnectionService(session).disconnect() is False

    async def test_disconnect_does_not_touch_another_workspace(self, ebay: FakeEbay) -> None:
        async with live_tenants(2) as (tenants, factory):
            keeper, leaver = tenants
            await connect_seller(factory, keeper)
            ebay.seller_user_id = "second-immutable-seller-id"
            await connect_seller(factory, leaver)

            async with own_connection(leaver) as session:
                await EbayConnectionService(session).disconnect()
                await session.commit()

            assert await count_connections(factory, keeper) == 1
            assert await count_connections(factory, leaver) == 0

    async def test_disconnecting_frees_the_seller_for_another_workspace(
        self, ebay: FakeEbay
    ) -> None:
        """The uniqueness constraint must not strand an account permanently.

        A merchant who connects the wrong workspace, disconnects and reconnects
        elsewhere is an ordinary mistake, not a support ticket.
        """
        async with live_tenants(2) as (tenants, factory):
            wrong, right = tenants
            await connect_seller(factory, wrong)

            async with own_connection(wrong) as session:
                await EbayConnectionService(session).disconnect()
                await session.commit()

            await connect_seller(factory, right)
            assert await count_connections(factory, right) == 1


# ---------------------------------------------------------------------------
# The EBAY-C0 deletion contract, now that there is something to erase
# ---------------------------------------------------------------------------


class TestDeletionGovernance:
    async def test_a_deletion_notification_erases_the_matching_connection(
        self, ebay: FakeEbay
    ) -> None:
        """The whole reason C1 had to register an owner.

        Before this milestone the correct behaviour was a verified zero-match
        deletion. It is now a real erasure, and this is what shows the C0 guard
        was a mechanism rather than a note.
        """
        async with live_tenants() as (tenants, factory):
            tenant = tenants[0]
            await connect_seller(factory, tenant)

            async with factory() as session:
                outcome = await EbayAccountDeletionProcessor(session).erase(
                    DeletionSubject(user_id=SELLER_USER_ID, username="anything", eias_token="tok")
                )
                await session.commit()

            assert outcome.erased == 1
            assert "ebay_connection" in outcome.owners_run
            assert await count_connections(factory, tenant) == 0

    async def test_deletion_is_idempotent(self, ebay: FakeEbay) -> None:
        """eBay redelivers. A second run must find nothing and not fail."""
        async with live_tenants() as (tenants, factory):
            await connect_seller(factory, tenants[0])
            subject = DeletionSubject(user_id=SELLER_USER_ID, username=None, eias_token=None)

            async with factory() as session:
                first = await EbayAccountDeletionProcessor(session).erase(subject)
                await session.commit()
            async with factory() as session:
                second = await EbayAccountDeletionProcessor(session).erase(subject)
                await session.commit()

            assert first.erased == 1
            assert second.erased == 0

    async def test_deletion_leaves_another_tenants_connection_alone(self, ebay: FakeEbay) -> None:
        """Unscoped by necessity, narrow by construction: one equality predicate
        on a globally unique column."""
        async with live_tenants(2) as (tenants, factory):
            target, bystander = tenants
            await connect_seller(factory, target)
            ebay.seller_user_id = "bystander-immutable-seller-id"
            await connect_seller(factory, bystander)

            async with factory() as session:
                outcome = await EbayAccountDeletionProcessor(session).erase(
                    DeletionSubject(user_id=SELLER_USER_ID, username=None, eias_token=None)
                )
                await session.commit()

            assert outcome.erased == 1
            assert await count_connections(factory, target) == 0
            assert await count_connections(factory, bystander) == 1

    async def test_a_notification_without_the_immutable_id_erases_nothing(
        self, ebay: FakeEbay
    ) -> None:
        """Matching on the mutable username could erase the wrong workspace.

        A seller who renamed may free their old name for someone else, so a
        username match is not evidence of identity — and erasing on it is
        unrecoverable.
        """
        async with live_tenants() as (tenants, factory):
            tenant = tenants[0]
            await connect_seller(factory, tenant)

            async with factory() as session:
                outcome = await EbayAccountDeletionProcessor(session).erase(
                    DeletionSubject(user_id=None, username=SELLER_USERNAME, eias_token="eias-token")
                )
                await session.commit()

            assert outcome.erased == 0
            assert await count_connections(factory, tenant) == 1
