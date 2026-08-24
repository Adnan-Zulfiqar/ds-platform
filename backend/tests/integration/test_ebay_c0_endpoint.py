"""EBAY-C0 — the public compliance endpoint, end to end.

Real routes, real middleware, real PostgreSQL, real signature verification
against eBay's official vector. **The only thing mocked is eBay's network**:
the OAuth token grant and ``getPublicKey`` are served by ``httpx.MockTransport``
with the payloads eBay's own SDK vector records. No live eBay request is made
anywhere in this suite.

Every credential here is synthetic and says so in its own value.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.integrations.ebay import compliance as compliance_module
from app.integrations.ebay.public_key import EbayPublicKeyClient
from app.models.ebay import EbayComplianceNotification, NotificationProcessing
from tests.unit.test_ebay_c0_signature import (
    OFFICIAL_KEY_ALGORITHM,
    OFFICIAL_KEY_DIGEST,
    OFFICIAL_PUBLIC_KEY,
    OFFICIAL_SIGNATURE_HEADER,
    official_body,
)

pytestmark = pytest.mark.integration

PATH = "/api/v1/integrations/ebay/marketplace-account-deletion"
SYNTHETIC_TOKEN = "EBAY-C0-SYNTHETIC-VERIFICATION-TOKEN-0001"
ENDPOINT = "https://api.whiteto.com/api/v1/integrations/ebay/marketplace-account-deletion"


# --------------------------------------------------------------- eBay's wire
class FakeEbay:
    """eBay's two endpoints, counted. Nothing leaves the process."""

    def __init__(self) -> None:
        self.token_calls = 0
        self.key_calls = 0
        self.key_paths: list[str] = []
        self.token_status = 200
        self.key_status = 200
        self.key_payload: dict[str, Any] = {
            "key": OFFICIAL_PUBLIC_KEY,
            "algorithm": OFFICIAL_KEY_ALGORITHM,
            "digest": OFFICIAL_KEY_DIGEST,
        }
        self.fail_with: Exception | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.fail_with is not None:
            raise self.fail_with
        if request.url.path.endswith("/identity/v1/oauth2/token"):
            self.token_calls += 1
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={})
            return httpx.Response(
                200, json={"access_token": "synthetic-application-token", "expires_in": 7200}
            )
        if "/commerce/notification/v1/public_key/" in request.url.path:
            self.key_calls += 1
            self.key_paths.append(request.url.path)
            if self.key_status != 200:
                return httpx.Response(self.key_status, json={})
            return httpx.Response(200, json=self.key_payload)
        raise AssertionError(f"unexpected eBay call: {request.url}")

    def client(self) -> EbayPublicKeyClient:
        return EbayPublicKeyClient(
            transport=httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
        )


@pytest.fixture
def ebay(monkeypatch: pytest.MonkeyPatch) -> FakeEbay:
    fake = FakeEbay()
    original = compliance_module.EbayComplianceService.__init__

    def patched(self: Any, session: AsyncSession, *, key_client: Any = None) -> None:
        original(self, session, key_client=key_client or fake.client())

    monkeypatch.setattr(compliance_module.EbayComplianceService, "__init__", patched)
    return fake


@pytest.fixture(autouse=True)
async def clean_key_cache() -> AsyncIterator[None]:
    """Start every test with an empty public-key cache and a live client.

    Two reasons, both real rather than cosmetic. The cache is process-wide and
    deliberately long-lived (eBay asks for an hour), so without this the second
    test in a file would silently exercise a cache hit and never call eBay —
    which is how "the key is fetched from the fixed host" would pass while
    asserting nothing. And the pooled Redis client binds to the event loop that
    created it, so a client left over from a previous test's loop raises
    "Event loop is closed" on first use.
    """
    from app.core.redis import RedisPurpose, close_redis_clients, get_redis

    await close_redis_clients()
    redis = get_redis(RedisPurpose.CACHE)
    keys = await redis.keys("ebay:notification:public_key:*")
    if keys:
        await redis.delete(*keys)
    yield
    await close_redis_clients()


@pytest.fixture(autouse=True)
def ebay_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Synthetic platform credentials — they authenticate to nothing."""
    monkeypatch.setattr(settings.ebay, "marketplace_deletion_endpoint", ENDPOINT)
    monkeypatch.setattr(
        settings.ebay, "marketplace_deletion_verification_token", SecretStr(SYNTHETIC_TOKEN)
    )
    monkeypatch.setattr(settings.ebay, "client_id", "synthetic-client-id")
    monkeypatch.setattr(settings.ebay, "client_secret", SecretStr("synthetic-client-secret"))


@pytest.fixture
async def isolated_session() -> AsyncIterator[AsyncSession]:
    """A committed session on its own engine.

    The ledger's uniqueness and the duplicate path are database facts, so they
    are exercised against real committed rows rather than inside a transaction
    that is rolled back.
    """
    engine = create_async_engine(settings.database.async_dsn, poolclass=None)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    try:
        async with factory() as session:
            yield session
    finally:
        async with factory() as cleanup:
            await cleanup.execute(sa.delete(EbayComplianceNotification))
            await cleanup.commit()
        await engine.dispose()


async def http(session: AsyncSession) -> AsyncClient:
    from app.api.deps import get_db_session
    from app.main import create_application

    app = create_application()

    async def _override() -> Any:
        yield session

    app.dependency_overrides[get_db_session] = _override
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


async def post(
    session: AsyncSession,
    *,
    body: bytes | None = None,
    signature: str | None = OFFICIAL_SIGNATURE_HEADER,
    content_type: str = "application/json",
) -> httpx.Response:
    headers = {"content-type": content_type}
    if signature is not None:
        headers["x-ebay-signature"] = signature
    client = await http(session)
    async with client:
        response = await client.post(
            PATH, content=official_body() if body is None else body, headers=headers
        )
    if response.status_code < 400:
        await session.commit()
    else:
        await session.rollback()
    return response


async def ledger_rows(session: AsyncSession) -> list[EbayComplianceNotification]:
    result = await session.execute(
        sa.select(EbayComplianceNotification).execution_options(populate_existing=True)
    )
    return list(result.scalars().all())


# --------------------------------------------------------------- GET challenge
class TestChallengeEndpoint:
    async def test_it_answers_with_the_documented_body_and_content_type(
        self, isolated_session: AsyncSession
    ) -> None:
        import hashlib

        client = await http(isolated_session)
        async with client:
            response = await client.get(PATH, params={"challenge_code": "abc123"})

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        assert response.json() == {
            "challengeResponse": hashlib.sha256(
                b"abc123" + SYNTHETIC_TOKEN.encode() + ENDPOINT.encode()
            ).hexdigest()
        }

    async def test_the_body_is_real_json_with_no_byte_order_mark(
        self, isolated_session: AsyncSession
    ) -> None:
        """eBay: *"a BOM is considered invalid JSON"* and the subscription fails."""
        client = await http(isolated_session)
        async with client:
            response = await client.get(PATH, params={"challenge_code": "abc123"})
        assert not response.content.startswith(b"\xef\xbb\xbf")
        assert json.loads(response.content)

    async def test_the_query_alias_is_exactly_challenge_code(
        self, isolated_session: AsyncSession
    ) -> None:
        """``challengeCode`` is not what eBay sends, and must not work."""
        client = await http(isolated_session)
        async with client:
            wrong = await client.get(PATH, params={"challengeCode": "abc123"})
            right = await client.get(PATH, params={"challenge_code": "abc123"})
        assert wrong.status_code == 422
        assert right.status_code == 200

    @pytest.mark.parametrize("value", ["", "   "])
    async def test_a_blank_challenge_is_refused(
        self, isolated_session: AsyncSession, value: str
    ) -> None:
        client = await http(isolated_session)
        async with client:
            response = await client.get(PATH, params={"challenge_code": value})
        assert response.status_code == 422

    async def test_an_oversized_challenge_is_refused(self, isolated_session: AsyncSession) -> None:
        client = await http(isolated_session)
        async with client:
            response = await client.get(PATH, params={"challenge_code": "x" * 5000})
        assert response.status_code == 422

    async def test_the_route_needs_no_authentication(self, isolated_session: AsyncSession) -> None:
        """Public by protocol design — eBay cannot present a JWT."""
        client = await http(isolated_session)
        async with client:
            response = await client.get(PATH, params={"challenge_code": "abc123"})
        assert response.status_code == 200

    async def test_no_secret_appears_in_the_response(self, isolated_session: AsyncSession) -> None:
        client = await http(isolated_session)
        async with client:
            response = await client.get(PATH, params={"challenge_code": "abc123"})
        assert SYNTHETIC_TOKEN not in response.text
        assert "synthetic-client-secret" not in response.text

    async def test_an_unconfigured_server_refuses_rather_than_hashing_nothing(
        self, isolated_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings.ebay, "marketplace_deletion_verification_token", None)
        client = await http(isolated_session)
        async with client:
            response = await client.get(PATH, params={"challenge_code": "abc123"})
        assert response.status_code == 422
        assert response.json()["code"] == "ebay_not_configured"

    async def test_the_registered_url_is_served_directly_with_no_redirect(
        self, isolated_session: AsyncSession
    ) -> None:
        """eBay's URL has no trailing slash and must answer on the first hop.

        This is the requirement that matters: the endpoint string is part of the
        challenge hash, so the URL eBay reaches has to be the URL it asked for.
        A redirect here would mean eBay followed us somewhere else before being
        answered.

        Starlette still offers its usual inbound tolerance for the *slash*
        spelling — ``…-deletion/`` 307s to ``…-deletion`` — which is framework
        behaviour this phase does not add and does not remove. eBay never sends
        it: the portal is configured with the no-slash URL. Asserted below so
        the direction is on record rather than assumed.
        """
        client = await http(isolated_session)
        async with client:
            direct = await client.get(
                PATH, params={"challenge_code": "abc123"}, follow_redirects=False
            )
            with_slash = await client.get(
                f"{PATH}/", params={"challenge_code": "abc123"}, follow_redirects=False
            )

        assert direct.status_code == 200, "the registered URL must answer on the first hop"
        # The tolerated redirect points *at* the registered URL, never away from
        # it, so no caller can be steered somewhere this endpoint does not own.
        if with_slash.status_code in (301, 302, 307, 308):
            from urllib.parse import urlsplit

            assert urlsplit(with_slash.headers["location"]).path == PATH


# ------------------------------------------------------------ POST notification
class TestNotificationEndpoint:
    async def test_a_valid_signed_notification_is_acknowledged_with_204(
        self, isolated_session: AsyncSession, ebay: FakeEbay
    ) -> None:
        response = await post(isolated_session)

        assert response.status_code == 204, response.text
        assert response.content == b""
        rows = await ledger_rows(isolated_session)
        assert len(rows) == 1
        assert rows[0].processing_status is NotificationProcessing.COMPLETED
        assert rows[0].topic == "MARKETPLACE_ACCOUNT_DELETION"

    async def test_the_public_key_is_fetched_from_ebays_fixed_host_only(
        self, isolated_session: AsyncSession, ebay: FakeEbay
    ) -> None:
        await post(isolated_session)
        assert ebay.key_calls == 1
        assert ebay.key_paths == [
            "/commerce/notification/v1/public_key/9936261a-7d7b-4621-a0f1-96ccb428af49"
        ]
        assert ebay.token_calls == 1

    async def test_an_altered_body_is_rejected_with_412(
        self, isolated_session: AsyncSession, ebay: FakeEbay
    ) -> None:
        tampered = official_body().replace(b"test_user", b"evil_user")
        response = await post(isolated_session, body=tampered)

        assert response.status_code == 412
        assert response.json()["code"] == "ebay_signature_invalid"
        assert await ledger_rows(isolated_session) == []

    async def test_a_missing_signature_is_rejected_with_412(
        self, isolated_session: AsyncSession, ebay: FakeEbay
    ) -> None:
        response = await post(isolated_session, signature=None)
        assert response.status_code == 412
        assert ebay.key_calls == 0, "an unsigned body must not reach eBay's key service"

    async def test_an_unsupported_content_type_is_rejected(
        self, isolated_session: AsyncSession, ebay: FakeEbay
    ) -> None:
        response = await post(isolated_session, content_type="text/plain")
        assert response.status_code == 412
        assert ebay.key_calls == 0

    async def test_an_oversized_body_is_rejected_before_verification(
        self, isolated_session: AsyncSession, ebay: FakeEbay
    ) -> None:
        response = await post(isolated_session, body=b"x" * (64 * 1024 + 1))
        assert response.status_code == 413
        assert ebay.key_calls == 0, "an oversized body must not cost an eBay call"

    async def test_malformed_json_behind_a_valid_signature_is_rejected(
        self, isolated_session: AsyncSession, ebay: FakeEbay
    ) -> None:
        """Signature first, schema second — and a valid signature over rubbish
        is still rubbish."""
        body = b"{not json"
        # The signature will not match, so this proves ordering rather than
        # schema handling; the schema path is covered by the next test.
        response = await post(isolated_session, body=body)
        assert response.status_code == 412

    async def test_an_unknown_topic_fails_closed(
        self, isolated_session: AsyncSession, ebay: FakeEbay, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verification is bypassed here to isolate schema handling.

        The signature path has its own dedicated coverage; what this asserts is
        that a *verified* notification for a topic this endpoint was never
        designed to act on is refused rather than acknowledged.
        """
        monkeypatch.setattr(
            compliance_module.EbayComplianceService,
            "verify",
            lambda self, **_: asyncio.sleep(0),
        )
        payload = json.loads(official_body())
        payload["metadata"]["topic"] = "PRIORITY_LISTING_REVISION"
        response = await post(
            isolated_session, body=json.dumps(payload, separators=(",", ":")).encode()
        )

        assert response.status_code == 412
        assert response.json()["code"] == "ebay_notification_rejected"
        assert await ledger_rows(isolated_session) == []

    async def test_an_unknown_key_id_returns_a_retryable_failure(
        self, isolated_session: AsyncSession, ebay: FakeEbay
    ) -> None:
        """404 from eBay's key service must not be acknowledged as success."""
        ebay.key_status = 404
        response = await post(isolated_session)
        assert response.status_code >= 500
        assert await ledger_rows(isolated_session) == []

    @pytest.mark.parametrize("status", [429, 500, 503])
    async def test_a_transient_key_service_failure_is_retryable_not_acknowledged(
        self, isolated_session: AsyncSession, ebay: FakeEbay, status: int
    ) -> None:
        """eBay resends what it is not told was received.

        Acknowledging here would silently discard a deletion request because
        eBay's own key service was briefly unavailable.
        """
        ebay.key_status = status
        response = await post(isolated_session)
        assert response.status_code >= 500
        assert response.json()["code"] == "ebay_key_unavailable"
        assert await ledger_rows(isolated_session) == []

    async def test_a_key_service_timeout_is_retryable(
        self, isolated_session: AsyncSession, ebay: FakeEbay
    ) -> None:
        ebay.fail_with = httpx.ReadTimeout(
            "ebay went quiet", request=httpx.Request("GET", "https://x")
        )
        response = await post(isolated_session)
        assert response.status_code >= 500
        assert await ledger_rows(isolated_session) == []

    async def test_an_oauth_failure_is_retryable(
        self, isolated_session: AsyncSession, ebay: FakeEbay
    ) -> None:
        ebay.token_status = 401
        response = await post(isolated_session)
        assert response.status_code >= 500
        assert ebay.key_calls == 0
        assert await ledger_rows(isolated_session) == []

    async def test_an_unsupported_key_algorithm_is_rejected(
        self, isolated_session: AsyncSession, ebay: FakeEbay
    ) -> None:
        ebay.key_payload = {**ebay.key_payload, "algorithm": "RSA"}
        response = await post(isolated_session)
        assert response.status_code == 412

    async def test_an_unsupported_key_digest_is_rejected(
        self, isolated_session: AsyncSession, ebay: FakeEbay
    ) -> None:
        ebay.key_payload = {**ebay.key_payload, "digest": "MD5"}
        response = await post(isolated_session)
        assert response.status_code == 412


class TestNoSecretsOrPiiLeak:
    async def test_no_identifier_or_secret_appears_in_any_response(
        self, isolated_session: AsyncSession, ebay: FakeEbay
    ) -> None:
        response = await post(isolated_session)
        body = response.text
        for forbidden in (
            "test_user",
            "ma8vp1jySJC",
            "nY+sHZ2PrBmdj6wV",
            SYNTHETIC_TOKEN,
            "synthetic-client-secret",
            "synthetic-application-token",
        ):
            assert forbidden not in body

    async def test_no_identifier_or_secret_is_written_to_the_ledger(
        self, isolated_session: AsyncSession, ebay: FakeEbay
    ) -> None:
        """The strongest form of PII safety: the columns do not exist.

        Asserted against the whole row rather than a list of fields, so a future
        column that could hold an identifier fails this immediately.
        """
        await post(isolated_session)
        row = (await ledger_rows(isolated_session))[0]
        rendered = json.dumps({c.name: str(getattr(row, c.name)) for c in row.__table__.columns})
        for forbidden in ("test_user", "ma8vp1jySJC", "nY+sHZ2PrBmdj6wV", SYNTHETIC_TOKEN):
            assert forbidden not in rendered

    async def test_no_secret_or_identifier_reaches_the_logs(
        self, isolated_session: AsyncSession, ebay: FakeEbay, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        with caplog.at_level(logging.DEBUG):
            await post(isolated_session)
        text = caplog.text
        for forbidden in (
            "test_user",
            "ma8vp1jySJC",
            SYNTHETIC_TOKEN,
            "synthetic-client-secret",
            "synthetic-application-token",
            OFFICIAL_SIGNATURE_HEADER,
        ):
            assert forbidden not in text

    async def test_the_openapi_schema_exposes_no_credential_field(
        self, isolated_session: AsyncSession
    ) -> None:
        client = await http(isolated_session)
        async with client:
            spec = (await client.get("/openapi.json")).json()
        rendered = json.dumps(spec)
        for forbidden in ("verificationToken", "clientSecret", "verification_token", "eiasToken"):
            assert forbidden not in rendered
        assert PATH in spec["paths"]
        assert set(spec["paths"][PATH]) == {"get", "post"}
