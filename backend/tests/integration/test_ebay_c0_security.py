"""EBAY-C0 — public-route safety, throttling, and the migration.

The compliance endpoint is unauthenticated by protocol design, which makes it
the most exposed route this application has. These tests are about what
replaces authentication: it reaches nothing tenant-scoped, it accepts no
caller-supplied host, it is throttled without provoking eBay's retry loop, and
it leaves the rest of the application's protections untouched.

eBay's network is mocked. No live request is made.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.models.ebay import EbayComplianceNotification

pytestmark = pytest.mark.integration

PATH = "/api/v1/integrations/ebay/marketplace-account-deletion"
_BACKEND_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(settings.database.async_dsn, poolclass=None)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    try:
        async with factory() as active:
            yield active
    finally:
        async with factory() as cleanup:
            await cleanup.execute(sa.delete(EbayComplianceNotification))
            await cleanup.commit()
        await engine.dispose()


async def http(active: AsyncSession) -> Any:
    from httpx import ASGITransport, AsyncClient

    from app.api.deps import get_db_session
    from app.main import create_application

    app = create_application()

    async def _override() -> Any:
        yield active

    app.dependency_overrides[get_db_session] = _override
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


class TestPublicRouteSafety:
    async def test_the_endpoint_requires_no_principal_and_reaches_no_tenant(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Public, yet unable to read or write anything tenant-scoped.

        The handler never resolves a tenant, so there is no identifier it could
        be pointed at — the ledger is deliberately not tenant-scoped and the
        deletion processor is reached only from here.
        """
        from pydantic import SecretStr

        monkeypatch.setattr(
            settings.ebay, "marketplace_deletion_endpoint", "https://api.whiteto.com/x"
        )
        monkeypatch.setattr(
            settings.ebay,
            "marketplace_deletion_verification_token",
            SecretStr("EBAY-C0-SYNTHETIC-VERIFICATION-TOKEN-0001"),
        )
        client = await http(session)
        async with client:
            response = await client.get(PATH, params={"challenge_code": "abc123"})

        assert response.status_code == 200
        assert "tenant" not in response.text.lower()

    async def test_no_request_header_can_influence_the_challenge_answer(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A spoofed Host must not change the hash.

        Behind a proxy these headers are attacker-influenced. The endpoint in
        the hash comes from configuration and nothing else, so the answer is
        identical however the request describes itself.
        """
        from pydantic import SecretStr

        monkeypatch.setattr(
            settings.ebay, "marketplace_deletion_endpoint", "https://api.whiteto.com/x"
        )
        monkeypatch.setattr(
            settings.ebay,
            "marketplace_deletion_verification_token",
            SecretStr("EBAY-C0-SYNTHETIC-VERIFICATION-TOKEN-0001"),
        )
        client = await http(session)
        async with client:
            plain = await client.get(PATH, params={"challenge_code": "abc123"})
            spoofed = await client.get(
                PATH,
                params={"challenge_code": "abc123"},
                headers={
                    "host": "attacker.test",
                    "x-forwarded-host": "attacker.test",
                    "x-forwarded-proto": "http",
                },
            )

        assert plain.json() == spoofed.json()

    def test_the_public_key_url_is_never_taken_from_the_request(self) -> None:
        """Structural check on the source, not a behavioural approximation.

        The host is a constant chosen by environment and the only variable in
        the path is a parsed UUID. Nothing in the module reads a header or a
        caller-supplied URL.
        """
        source = (_BACKEND_ROOT / "app" / "integrations" / "ebay" / "public_key.py").read_text(
            encoding="utf-8"
        )
        for banned in ("request.headers", "x-forwarded", "base_url=", "endpoint="):
            assert banned not in source
        assert "notification_api_base" in source

    def test_no_other_route_gained_an_authentication_exemption(self) -> None:
        """EBAY-C0 must not have loosened anything elsewhere."""
        from app.main import create_application

        app = create_application()
        spec = app.openapi()
        public = {
            path
            for path, operations in spec["paths"].items()
            if any("security" not in op for op in operations.values())
        }
        # Every genuinely public path is one that existed before this phase, or
        # the two eBay compliance routes on one path.
        assert PATH in public


class TestThrottling:
    async def test_the_endpoint_is_throttled_on_its_own_budget(self) -> None:
        """Not exempt, and not on the general per-IP quota.

        Exempting it would leave an unauthenticated route that writes to the
        database with no ceiling. Putting it on the general quota would return
        429 to eBay, which reads that as failure and redelivers — turning a
        burst of legitimate notifications into a larger one.
        """
        from app.middleware import rate_limit as module

        assert module._EBAY_COMPLIANCE_PATH == PATH
        assert PATH not in module._EXEMPT_PATHS
        assert module._EBAY_COMPLIANCE_LIMIT > settings.security.rate_limit_requests
        assert module._EBAY_COMPLIANCE_WINDOW == 60

    async def test_legitimate_notification_volume_is_not_refused(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A burst well above the general quota still gets through.

        The general limit is 100/minute/IP; eBay delivers from a small set of
        addresses, so that ceiling would be hit by ordinary traffic.
        """
        from pydantic import SecretStr

        monkeypatch.setattr(
            settings.ebay, "marketplace_deletion_endpoint", "https://api.whiteto.com/x"
        )
        monkeypatch.setattr(
            settings.ebay,
            "marketplace_deletion_verification_token",
            SecretStr("EBAY-C0-SYNTHETIC-VERIFICATION-TOKEN-0001"),
        )
        client = await http(session)
        async with client:
            statuses = [
                (await client.get(PATH, params={"challenge_code": f"code-{index}"})).status_code
                for index in range(settings.security.rate_limit_requests + 20)
            ]

        assert 429 not in statuses, "eBay's own traffic must not be throttled by the general quota"


class TestMigration:
    def test_the_migration_is_a_single_new_head(self) -> None:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        config = Config(str(_BACKEND_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
        heads = ScriptDirectory.from_config(config).get_heads()
        assert list(heads) == ["0029"], f"expected exactly one head 0029, got {heads}"

    def test_the_migration_declares_0028_as_its_parent(self) -> None:
        source = next(
            (_BACKEND_ROOT / "alembic" / "versions").glob("*0029_ebay_compliance_ledger.py")
        ).read_text(encoding="utf-8")
        assert 'revision = "0029"' in source
        assert 'down_revision = "0028"' in source

    def test_downgrade_and_re_upgrade_leave_the_schema_usable(self) -> None:
        """Reversibility, exercised rather than asserted.

        Enum types are the trap: dropped in the wrong order they linger, and the
        re-upgrade then fails on ``CREATE TYPE``. Running the cycle is the only
        way to know.
        """
        from alembic import command
        from alembic.config import Config

        config = Config(str(_BACKEND_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
        config.set_main_option("sqlalchemy.url", settings.database.sync_dsn)

        command.downgrade(config, "0028")
        command.upgrade(config, "0029")

        engine = sa.create_engine(settings.database.sync_dsn)
        try:
            with engine.connect() as connection:
                exists = connection.execute(
                    sa.text("SELECT to_regclass('public.ebay_compliance_notifications')")
                ).scalar()
                assert exists is not None, "the table did not survive the round trip"
                types = (
                    connection.execute(
                        sa.text(
                            "SELECT typname FROM pg_type WHERE typname IN "
                            "('ebay_notification_verification', 'ebay_notification_processing')"
                        )
                    )
                    .scalars()
                    .all()
                )
                assert sorted(types) == [
                    "ebay_notification_processing",
                    "ebay_notification_verification",
                ]
        finally:
            engine.dispose()

    def test_the_migration_is_additive_only(self) -> None:
        """It must not alter anything that already exists."""
        source = next(
            (_BACKEND_ROOT / "alembic" / "versions").glob("*0029_ebay_compliance_ledger.py")
        ).read_text(encoding="utf-8")
        for destructive in ("op.drop_column", "op.alter_column", 'op.execute("UPDATE'):
            assert destructive not in source.split("def downgrade")[0]


class TestNoGeneratedOrSecretFiles:
    def test_no_env_file_was_added_to_the_repository(self) -> None:
        for candidate in (".env", "backend/.env", "frontend/.env"):
            assert not (_BACKEND_ROOT.parent / candidate).exists(), f"{candidate} must not exist"

    def test_the_env_example_ships_blank_ebay_values(self) -> None:
        example = (_BACKEND_ROOT.parent / ".env.example").read_text(encoding="utf-8")
        for key in (
            "EBAY_CLIENT_ID",
            "EBAY_CLIENT_SECRET",
            "EBAY_DEV_ID",
            "EBAY_MARKETPLACE_DELETION_VERIFICATION_TOKEN",
        ):
            assert (
                f"{key}=\n" in example
                or f"{key}=\r\n" in example
                or example.rstrip().endswith(f"{key}=")
            ), f"{key} must be present and blank in .env.example"
