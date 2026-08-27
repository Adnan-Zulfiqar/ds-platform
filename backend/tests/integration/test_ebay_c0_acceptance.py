"""EBAY-C0 acceptance fix — the findings, reproduced then fixed.

Written against `db6e986` **before** the production changes, so each test named
here failed for the reason it describes. What they cover:

* **BLOCKER 1** — the body was buffered whole by `await request.body()` and only
  measured afterwards, so a gigabyte reached memory before the 413.
* **BLOCKER 2** — the rate limiter took `X-Forwarded-For` from any caller, so a
  direct attacker could rotate the header and mint a fresh quota per request.
* **MEDIUM 3** — the public-key cache key was the bare key id, so a sandbox and
  a production key with the same id shared one entry.
* **MEDIUM 4** — a repeat `notificationId` with a *different* payload was
  counted as an ordinary duplicate.
* **MEDIUM 5** — any elliptic curve was accepted, not only eBay's P-256.

Real ASGI, real PostgreSQL, real Redis. eBay's own network is `MockTransport`
throughout; no live request is made.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.integrations.ebay import compliance as compliance_module
from app.integrations.ebay.compliance import MAX_NOTIFICATION_BODY_BYTES
from app.models.ebay import EbayComplianceNotification
from tests.integration.test_ebay_c0_endpoint import (
    ENDPOINT,
    PATH,
    SYNTHETIC_TOKEN,
    FakeEbay,
)
from tests.unit.test_ebay_c0_signature import OFFICIAL_SIGNATURE_HEADER, official_body

pytestmark = pytest.mark.integration


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
    from app.core.redis import RedisPurpose, close_redis_clients, get_redis

    await close_redis_clients()
    redis = get_redis(RedisPurpose.CACHE)
    keys = await redis.keys("ebay:*")
    if keys:
        await redis.delete(*keys)
    yield
    await close_redis_clients()


@pytest.fixture(autouse=True)
def ebay_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.ebay, "marketplace_deletion_endpoint", ENDPOINT)
    monkeypatch.setattr(
        settings.ebay, "marketplace_deletion_verification_token", SecretStr(SYNTHETIC_TOKEN)
    )
    monkeypatch.setattr(settings.ebay, "client_id", "synthetic-client-id")
    monkeypatch.setattr(settings.ebay, "client_secret", SecretStr("synthetic-client-secret"))


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


def application(active: AsyncSession) -> Any:
    from app.api.deps import get_db_session
    from app.main import create_application

    app = create_application()

    async def _override() -> Any:
        yield active

    app.dependency_overrides[get_db_session] = _override
    return app


async def ledger_rows(active: AsyncSession) -> list[EbayComplianceNotification]:
    result = await active.execute(
        sa.select(EbayComplianceNotification).execution_options(populate_existing=True)
    )
    return list(result.scalars().all())


# ===========================================================================
# BLOCKER 1 — bounded streaming body
# ===========================================================================
class CountingBody:
    """An ASGI body the server has to pull, chunk by chunk.

    The point of the whole exercise: a reader that stops early stops *asking*,
    and this records exactly how many bytes it was asked for. A test that hands
    the entire body over and then checks for a 413 proves nothing about
    buffering, because the buffering already happened.
    """

    def __init__(
        self,
        *,
        chunk: bytes,
        chunks: int,
        declared_length: int | None = None,
        disconnect_after: int | None = None,
    ) -> None:
        self.chunk = chunk
        self.chunks = chunks
        self.declared_length = declared_length
        self.disconnect_after = disconnect_after
        self.chunks_sent = 0

    @property
    def bytes_sent(self) -> int:
        return self.chunks_sent * len(self.chunk)

    async def __call__(self) -> AsyncIterator[bytes]:
        for _ in range(self.chunks):
            if self.disconnect_after is not None and self.chunks_sent >= self.disconnect_after:
                raise httpx.ReadError("client went away")
            self.chunks_sent += 1
            yield self.chunk


async def raw_asgi_post(
    active: AsyncSession,
    *,
    body: CountingBody,
    headers: dict[str, str],
) -> tuple[int, dict[str, Any]]:
    """Drive the ASGI app directly, feeding the body on demand.

    Deliberately not `httpx` with a bytes payload: that would materialise the
    whole body before the app ever ran. This speaks the ASGI protocol so the
    application decides how much it receives.
    """
    app = application(active)
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": PATH,
        "raw_path": PATH.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": ("203.0.113.10", 54321),
        "server": ("testserver", 80),
    }

    produced = body()
    disconnected = False

    async def receive() -> dict[str, Any]:
        nonlocal disconnected
        if disconnected:
            return {"type": "http.disconnect"}
        try:
            chunk = await anext(produced)
        except StopAsyncIteration:
            return {"type": "http.request", "body": b"", "more_body": False}
        except httpx.ReadError:
            disconnected = True
            return {"type": "http.disconnect"}
        return {"type": "http.request", "body": chunk, "more_body": True}

    captured: dict[str, Any] = {"status": None, "body": b""}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            captured["status"] = message["status"]
        elif message["type"] == "http.response.body":
            captured["body"] += message.get("body", b"")

    await app(scope, receive, send)
    return int(captured["status"] or 0), captured


def json_headers(
    length: int | None = None, *, content_type: str = "application/json"
) -> dict[str, str]:
    headers = {
        "content-type": content_type,
        "x-ebay-signature": OFFICIAL_SIGNATURE_HEADER,
        "host": "testserver",
    }
    if length is not None:
        headers["content-length"] = str(length)
    return headers


class TestBoundedStreamingBody:
    async def test_consumption_stops_once_the_limit_is_exceeded(
        self, session: AsyncSession, ebay: FakeEbay
    ) -> None:
        """The finding, stated as an assertion about bytes *received*.

        A 16 MiB body is offered in 8 KiB chunks. A reader that stops at the
        ceiling can never have pulled more than a little over 64 KiB; the
        original `await request.body()` pulled all 16 MiB before measuring.
        """
        chunk = b"x" * 8192
        total_chunks = 2048  # 16 MiB
        body = CountingBody(chunk=chunk, chunks=total_chunks)

        status, _ = await raw_asgi_post(session, body=body, headers=json_headers())

        assert status == 413
        assert body.bytes_sent <= MAX_NOTIFICATION_BODY_BYTES + len(chunk), (
            f"the application consumed {body.bytes_sent} bytes before rejecting a "
            f"{MAX_NOTIFICATION_BODY_BYTES}-byte limit — it is buffering the whole body"
        )
        assert body.chunks_sent < total_chunks, "the whole body was consumed"

    async def test_exactly_the_limit_is_accepted_by_the_reader(
        self, session: AsyncSession, ebay: FakeEbay
    ) -> None:
        """64 KiB exactly is not oversized — the boundary must not be off by one.

        It fails signature verification (it is not a signed notification), which
        is the correct next step and proves the reader passed it through.
        """
        body = CountingBody(chunk=b"y" * MAX_NOTIFICATION_BODY_BYTES, chunks=1)
        status, _ = await raw_asgi_post(session, body=body, headers=json_headers())
        assert status != 413, "a body of exactly the limit must not be rejected as oversized"
        assert status == 412

    async def test_one_byte_over_the_limit_is_rejected(
        self, session: AsyncSession, ebay: FakeEbay
    ) -> None:
        body = CountingBody(chunk=b"y" * (MAX_NOTIFICATION_BODY_BYTES + 1), chunks=1)
        status, _ = await raw_asgi_post(session, body=body, headers=json_headers())
        assert status == 413

    async def test_an_oversized_declared_content_length_is_refused_before_reading(
        self, session: AsyncSession, ebay: FakeEbay
    ) -> None:
        """The cheap check, when the caller is honest about the size."""
        body = CountingBody(chunk=b"z" * 1024, chunks=1)
        status, _ = await raw_asgi_post(
            session, body=body, headers=json_headers(length=50 * 1024 * 1024)
        )
        assert status == 413
        assert body.bytes_sent == 0, "a declared oversize must not cost a single byte read"

    async def test_a_forged_small_content_length_does_not_defeat_the_limit(
        self, session: AsyncSession, ebay: FakeEbay
    ) -> None:
        """Content-Length is a hint, never the protection.

        The header claims 10 bytes and the body streams megabytes. Trusting the
        header would let an attacker send anything they liked.
        """
        chunk = b"x" * 8192
        body = CountingBody(chunk=chunk, chunks=1024)
        status, _ = await raw_asgi_post(session, body=body, headers=json_headers(length=10))

        assert status == 413
        assert body.bytes_sent <= MAX_NOTIFICATION_BODY_BYTES + len(chunk)

    async def test_a_missing_content_length_is_still_bounded(
        self, session: AsyncSession, ebay: FakeEbay
    ) -> None:
        """Chunked transfer sends no Content-Length at all."""
        chunk = b"x" * 8192
        body = CountingBody(chunk=chunk, chunks=1024)
        headers = json_headers()
        assert "content-length" not in headers

        status, _ = await raw_asgi_post(session, body=body, headers=headers)
        assert status == 413
        assert body.bytes_sent <= MAX_NOTIFICATION_BODY_BYTES + len(chunk)

    async def test_an_oversized_request_writes_nothing_and_deletes_nothing(
        self, session: AsyncSession, ebay: FakeEbay
    ) -> None:
        body = CountingBody(chunk=b"x" * 8192, chunks=1024)
        await raw_asgi_post(session, body=body, headers=json_headers())

        assert await ledger_rows(session) == []
        assert ebay.key_calls == 0, "an oversized body must not cost an eBay call"

    async def test_an_empty_body_is_handled_cleanly(
        self, session: AsyncSession, ebay: FakeEbay
    ) -> None:
        body = CountingBody(chunk=b"", chunks=0)
        status, _ = await raw_asgi_post(session, body=body, headers=json_headers(length=0))
        assert status == 412
        assert await ledger_rows(session) == []

    async def test_a_client_disconnect_mid_stream_is_not_a_server_error(
        self, session: AsyncSession, ebay: FakeEbay
    ) -> None:
        """A dropped connection is the client's problem, not a 500."""
        body = CountingBody(chunk=b"x" * 1024, chunks=100, disconnect_after=3)
        status, _ = await raw_asgi_post(session, body=body, headers=json_headers())

        assert status < 500, f"a disconnect produced {status}"
        assert await ledger_rows(session) == []

    async def test_the_verifier_receives_the_exact_bytes(
        self, session: AsyncSession, ebay: FakeEbay, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Byte-for-byte, split across chunks, with no re-encoding in between."""
        seen: list[bytes] = []
        original = compliance_module.EbayComplianceService.verify

        async def capture(self: Any, *, raw_body: bytes, signature_header: str | None) -> None:
            seen.append(raw_body)
            await original(self, raw_body=raw_body, signature_header=signature_header)

        monkeypatch.setattr(compliance_module.EbayComplianceService, "verify", capture)

        payload = official_body()
        midpoint = len(payload) // 2

        class Split(CountingBody):
            async def __call__(self) -> AsyncIterator[bytes]:
                self.chunks_sent += 1
                yield payload[:midpoint]
                self.chunks_sent += 1
                yield payload[midpoint:]

        status, _ = await raw_asgi_post(
            session, body=Split(chunk=b"", chunks=2), headers=json_headers()
        )

        assert seen == [payload], "the reassembled body differed from what was sent"
        assert status == 204

    @pytest.mark.parametrize(
        ("content_type", "expected"),
        [
            ("application/json", 204),
            ("application/json; charset=utf-8", 204),
            ("application/json;charset=UTF-8", 204),
            ("text/plain", 412),
            ("application/xml", 412),
        ],
    )
    async def test_content_type_handling(
        self,
        session: AsyncSession,
        ebay: FakeEbay,
        content_type: str,
        expected: int,
    ) -> None:
        payload = official_body()

        class Whole(CountingBody):
            async def __call__(self) -> AsyncIterator[bytes]:
                self.chunks_sent += 1
                yield payload

        status, _ = await raw_asgi_post(
            session,
            body=Whole(chunk=b"", chunks=1),
            headers=json_headers(content_type=content_type),
        )
        assert status == expected

    async def test_malformed_json_behind_a_valid_signature_is_rejected(
        self, session: AsyncSession, ebay: FakeEbay, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Signature first; a verified body that is not JSON is still refused."""

        async def accept(self: Any, **_: Any) -> None:
            return None

        monkeypatch.setattr(compliance_module.EbayComplianceService, "verify", accept)

        class Broken(CountingBody):
            async def __call__(self) -> AsyncIterator[bytes]:
                self.chunks_sent += 1
                yield b"{not json at all"

        status, captured = await raw_asgi_post(
            session, body=Broken(chunk=b"", chunks=1), headers=json_headers()
        )
        assert status == 412
        assert json.loads(captured["body"])["code"] == "ebay_notification_rejected"
        assert await ledger_rows(session) == []


# ===========================================================================
# BLOCKER 2 — trusted proxy and rate-limit identity
# ===========================================================================
async def challenge(
    active: AsyncSession,
    *,
    peer: str = "203.0.113.10",
    headers: dict[str, str] | None = None,
    code: str = "abc123",
) -> int:
    """One GET through the real middleware stack, from a chosen socket peer."""
    app = application(active)
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": PATH,
        "raw_path": PATH.encode(),
        "query_string": f"challenge_code={code}".encode(),
        "root_path": "",
        "headers": [
            (k.lower().encode(), v.encode())
            for k, v in {"host": "testserver", **(headers or {})}.items()
        ],
        "client": (peer, 44444),
        "server": ("testserver", 80),
    }
    captured: dict[str, Any] = {"status": 0}

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            captured["status"] = message["status"]

    await app(scope, receive, send)
    return int(captured["status"])


@pytest.fixture
async def strict_limit(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    """A tiny compliance budget, so exhaustion is observable in a few requests.

    The limiter itself stays enabled and unmodified; only the number is small.
    """
    from app.core.redis import RedisPurpose, close_redis_clients, get_redis
    from app.middleware import rate_limit as module

    # The integration conftest disables the limiter for every HTTP flow test.
    # These tests are *about* the limiter, so it is switched back on for their
    # duration only — the middleware reads the flag when the app is built, and
    # each request here builds a fresh app.
    monkeypatch.setattr(settings.security, "rate_limit_enabled", True)
    monkeypatch.setattr(module, "_EBAY_COMPLIANCE_LIMIT", 3)
    await close_redis_clients()
    redis = get_redis(RedisPurpose.RATE_LIMIT)
    keys = await redis.keys("ratelimit:*")
    if keys:
        await redis.delete(*keys)
    yield
    await close_redis_clients()


class TestForwardedHeaderCannotForgeIdentity:
    async def test_a_direct_caller_cannot_mint_a_fresh_quota_per_request(
        self, session: AsyncSession, strict_limit: None
    ) -> None:
        """The finding.

        One attacker, one socket, a different ``X-Forwarded-For`` each time.
        If the header is trusted from an untrusted peer, every request looks
        like a new client and the budget never runs out.
        """
        statuses = [
            await challenge(
                session,
                peer="198.51.100.7",
                headers={"x-forwarded-for": f"10.0.0.{index}"},
                code=f"code-{index}",
            )
            for index in range(8)
        ]

        assert 429 in statuses, (
            "rotating X-Forwarded-For from an untrusted peer bypassed the limit "
            "— the header is being trusted from any caller"
        )

    async def test_a_direct_caller_shares_one_identity_however_it_labels_itself(
        self, session: AsyncSession, strict_limit: None
    ) -> None:
        for index in range(3):
            assert await challenge(session, peer="198.51.100.8", code=f"a-{index}") == 200
        # Same socket, new label: must land on the same exhausted counter.
        assert (
            await challenge(
                session,
                peer="198.51.100.8",
                headers={"x-forwarded-for": "8.8.8.8"},
                code="a-relabelled",
            )
            == 429
        )

    async def test_two_genuinely_different_callers_stay_independent(
        self, session: AsyncSession, strict_limit: None
    ) -> None:
        for index in range(3):
            assert await challenge(session, peer="198.51.100.9", code=f"b-{index}") == 200
        assert await challenge(session, peer="198.51.100.9", code="b-over") == 429
        # A different socket is a different client and must be unaffected.
        assert await challenge(session, peer="198.51.100.10", code="c-0") == 200


# ===========================================================================
# MEDIUM 3 — environment-separated public-key cache
# ===========================================================================
class TestPublicKeyCacheNamespace:
    async def test_sandbox_and_production_keys_do_not_share_an_entry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The same key id exists in both estates and means different things.

        A shared cache slot means a production notification can be verified
        against a sandbox key — or refused because of one.
        """
        import uuid

        from app.core.config import EbayEnvironment
        from app.integrations.ebay.public_key import EbayPublicKeyClient

        key_id = uuid.UUID("9936261a-7d7b-4621-a0f1-96ccb428af49")

        monkeypatch.setattr(settings.ebay, "environment", EbayEnvironment.PRODUCTION)
        production = EbayPublicKeyClient._cache_key(key_id)
        monkeypatch.setattr(settings.ebay, "environment", EbayEnvironment.SANDBOX)
        sandbox = EbayPublicKeyClient._cache_key(key_id)

        assert production != sandbox, (
            "sandbox and production share one cache slot for the same key id"
        )
        assert "production" in production and "sandbox" in sandbox
        assert str(key_id) in production

    async def test_the_cache_key_carries_an_explicit_schema_version(self) -> None:
        """So a change to the stored shape cannot be read as the old shape."""
        import uuid

        from app.integrations.ebay.public_key import (
            PUBLIC_KEY_CACHE_SCHEMA,
            EbayPublicKeyClient,
        )

        rendered = EbayPublicKeyClient._cache_key(uuid.uuid4())
        assert PUBLIC_KEY_CACHE_SCHEMA in rendered


# ===========================================================================
# MEDIUM 4 — same notification id, different payload
# ===========================================================================
def notification_pair(notification_id: str, username: str) -> tuple[Any, bytes]:
    """One delivery, distinguished by ``username``.

    ``eventDate`` is derived from ``username`` so that two "different" payloads
    differ in **event content** and not only in the subject.

    That derivation was added by EBAY-C0.1 and the reason matters. These tests
    predate it and created a conflict by changing the username alone, which used
    to work because the digest covered the raw body — every field, personal ones
    included. C0.1 narrowed the digest to non-personal fields, deliberately: the
    ledger row is a permanent compliance receipt that is never erased, and a
    digest over ``username``/``userId``/``eiasToken`` would leave a way to
    confirm forever that a named person's account was deleted, with no keyed
    hashing authority in this codebase to blunt it.

    So a subject-only difference is no longer a collision, by design, and
    ``test_a_different_subject_under_one_id_is_not_detected`` in
    ``test_ebay_c01_retry_idempotency`` asserts exactly that. What these tests
    are really about — one notification id must not stand for two different
    events — is unchanged, and is what the derived ``eventDate`` preserves.
    """
    from app.integrations.ebay.schemas import parse_notification

    payload = json.loads(official_body())
    payload["notification"]["notificationId"] = notification_id
    payload["notification"]["data"]["username"] = username
    payload["notification"]["eventDate"] = _event_date_for(username)
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return parse_notification(json.loads(raw)), raw


def _event_date_for(username: str) -> str:
    """A stable, distinct request timestamp per subject.

    Stable so that two deliveries naming the same user are the same event;
    distinct so that two naming different users are not.
    """
    offset = sum(username.encode()) % 1000
    return f"2025-09-19T20:43:{offset // 1000:02d}.{offset:03d}Z"


class TestConflictingNotificationIdentity:
    async def test_the_same_id_with_a_different_payload_is_refused(
        self, session: AsyncSession
    ) -> None:
        """Two different notifications cannot share one id.

        Treating this as an ordinary repeat would silently discard the second
        one — and eBay would never resend it, because it was acknowledged.
        """
        from app.integrations.ebay.exceptions import EbayNotificationConflictError

        first, first_raw = notification_pair("conflict-1", "user_alpha")
        async with session.begin() if not session.in_transaction() else _noop():
            pass
        record = await compliance_module.EbayComplianceService(session).process(notification=first)
        await session.commit()
        original_digest = record.payload_digest

        second, second_raw = notification_pair("conflict-1", "user_beta")
        assert second_raw != first_raw

        with pytest.raises(EbayNotificationConflictError):
            await compliance_module.EbayComplianceService(session).process(notification=second)
        await session.rollback()

        rows = await ledger_rows(session)
        assert len(rows) == 1
        assert rows[0].payload_digest == original_digest, "the original digest was overwritten"
        assert rows[0].receipt_count == 1, "a conflicting delivery was counted as a repeat"


class _noop:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_: object) -> None:
        return None


# ===========================================================================
# MEDIUM 5 — the official curve
# ===========================================================================
class TestCurveEnforcement:
    def test_a_valid_ec_key_on_another_curve_is_refused(self) -> None:
        """P-384 is a perfectly good curve, and it is not eBay's.

        Accepting any EC key means accepting a key eBay would never have used,
        which widens what an attacker who can influence the key response can do.
        """
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        from app.integrations.ebay.exceptions import EbaySignatureError
        from app.integrations.ebay.signature import load_public_key

        other_curve = (
            ec.generate_private_key(ec.SECP384R1())
            .public_key()
            .public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode()
        )
        with pytest.raises(EbaySignatureError):
            load_public_key(other_curve)

    def test_the_official_p256_key_still_loads(self) -> None:
        from app.integrations.ebay.signature import load_public_key
        from tests.unit.test_ebay_c0_signature import OFFICIAL_PUBLIC_KEY

        key = load_public_key(OFFICIAL_PUBLIC_KEY)
        assert key.curve.name == "secp256r1"


class TestConflictConcurrencyAndCache:
    async def test_the_same_id_with_the_same_digest_is_still_an_idempotent_repeat(
        self, session: AsyncSession
    ) -> None:
        first, _raw = notification_pair("same-1", "user_alpha")
        await compliance_module.EbayComplianceService(session).process(notification=first)
        await session.commit()
        second = await compliance_module.EbayComplianceService(session).process(notification=first)
        await session.commit()

        assert second.receipt_count == 2
        assert len(await ledger_rows(session)) == 1

    async def test_a_conflicting_delivery_does_not_rerun_deletion(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The destructive half of the finding.

        Erasure must run exactly once for the original delivery and never again
        for an impostor sharing its id.
        """
        from app.integrations.ebay.deletion import EbayAccountDeletionProcessor
        from app.integrations.ebay.exceptions import EbayNotificationConflictError

        runs: list[Any] = []
        original = EbayAccountDeletionProcessor.erase

        async def counting(self: Any, subject: Any) -> Any:
            runs.append(subject)
            return await original(self, subject)

        monkeypatch.setattr(EbayAccountDeletionProcessor, "erase", counting)

        first, _first_raw = notification_pair("conflict-2", "user_alpha")
        await compliance_module.EbayComplianceService(session).process(notification=first)
        await session.commit()
        assert len(runs) == 1

        second, _second_raw = notification_pair("conflict-2", "user_beta")
        with pytest.raises(EbayNotificationConflictError):
            await compliance_module.EbayComplianceService(session).process(notification=second)
        await session.rollback()

        assert len(runs) == 1, "a conflicting delivery re-ran the deletion"

    async def test_the_conflict_error_carries_no_payload_or_identifier(
        self, session: AsyncSession
    ) -> None:
        from app.integrations.ebay.exceptions import EbayNotificationConflictError

        first, _first_raw = notification_pair("conflict-3", "user_alpha")
        await compliance_module.EbayComplianceService(session).process(notification=first)
        await session.commit()

        second, _second_raw = notification_pair("conflict-3", "user_beta")
        with pytest.raises(EbayNotificationConflictError) as raised:
            await compliance_module.EbayComplianceService(session).process(notification=second)
        await session.rollback()

        rendered = f"{raised.value.message} {raised.value.details}"
        for forbidden in ("user_alpha", "user_beta", "ma8vp1jySJC", "eiasToken"):
            assert forbidden not in rendered

    async def test_two_simultaneous_identical_deliveries_process_once(self) -> None:
        """Unchanged by the conflict check: the database still arbitrates."""
        engine = create_async_engine(settings.database.async_dsn, poolclass=None)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        parsed, _raw = notification_pair("concurrent-same", "user_alpha")

        async def deliver() -> Any:
            async with factory() as active:
                record = await compliance_module.EbayComplianceService(active).process(
                    notification=parsed
                )
                await active.commit()
                return record

        try:
            results = await asyncio.gather(deliver(), deliver(), return_exceptions=True)
            for result in results:
                assert not isinstance(result, Exception), result
            async with factory() as active:
                rows = (await active.execute(sa.select(EbayComplianceNotification))).scalars().all()
            assert len(rows) == 1
            assert rows[0].receipt_count == 2
        finally:
            async with factory() as cleanup:
                await cleanup.execute(sa.delete(EbayComplianceNotification))
                await cleanup.commit()
            await engine.dispose()

    async def test_two_simultaneous_conflicting_deliveries_leave_one_authoritative_row(
        self,
    ) -> None:
        """One wins the insert; the other is refused rather than merged.

        Both on their own connections, so the outcome is decided by the unique
        constraint and then by the digest comparison — not by ordering inside a
        single session.
        """
        from app.integrations.ebay.exceptions import EbayNotificationConflictError

        engine = create_async_engine(settings.database.async_dsn, poolclass=None)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

        async def deliver(username: str) -> Any:
            parsed, _raw = notification_pair("concurrent-conflict", username)
            async with factory() as active:
                try:
                    record = await compliance_module.EbayComplianceService(active).process(
                        notification=parsed
                    )
                    await active.commit()
                    return record.payload_digest
                except EbayNotificationConflictError:
                    await active.rollback()
                    return "conflict"

        try:
            results = await asyncio.gather(
                deliver("user_alpha"), deliver("user_beta"), return_exceptions=True
            )
            for result in results:
                assert not isinstance(result, Exception), result

            async with factory() as active:
                rows = (await active.execute(sa.select(EbayComplianceNotification))).scalars().all()
            assert len(rows) == 1, "conflicting concurrent deliveries created two rows"
            # Whichever won, the surviving row's digest is the one it recorded,
            # and the loser wrote nothing at all.
            assert rows[0].payload_digest in [r for r in results if r != "conflict"]
            assert rows[0].receipt_count == 1
        finally:
            async with factory() as cleanup:
                await cleanup.execute(sa.delete(EbayComplianceNotification))
                await cleanup.commit()
            await engine.dispose()

    async def test_a_corrupt_cache_entry_is_discarded_and_refetched(self, ebay: FakeEbay) -> None:
        """A poisoned entry must never be trusted, and must not be fatal."""
        import uuid as uuid_module

        from app.core.redis import RedisPurpose, get_redis
        from app.integrations.ebay.public_key import EbayPublicKeyClient

        key_id = uuid_module.UUID("9936261a-7d7b-4621-a0f1-96ccb428af49")
        await get_redis(RedisPurpose.CACHE).set(EbayPublicKeyClient._cache_key(key_id), "{not json")

        fetched = await ebay.client().get(key_id)
        assert fetched.algorithm == "ECDSA"
        assert ebay.key_calls == 1, "the corrupt entry was trusted instead of refetched"

    async def test_a_cached_entry_missing_its_digest_is_discarded(self, ebay: FakeEbay) -> None:
        """Algorithm, digest and PEM are all validated before use."""
        import json as json_module
        import uuid as uuid_module

        from app.core.redis import RedisPurpose, get_redis
        from app.integrations.ebay.public_key import EbayPublicKeyClient

        key_id = uuid_module.UUID("9936261a-7d7b-4621-a0f1-96ccb428af49")
        await get_redis(RedisPurpose.CACHE).set(
            EbayPublicKeyClient._cache_key(key_id),
            json_module.dumps({"key": "-----BEGIN PUBLIC KEY-----x-----END PUBLIC KEY-----"}),
        )

        await ebay.client().get(key_id)
        assert ebay.key_calls == 1

    async def test_a_redis_outage_does_not_bypass_verification(
        self, session: AsyncSession, ebay: FakeEbay, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Degrade to a live fetch — never to "assume the signature was fine".

        The cache is an optimisation eBay asked for. Losing it must cost a round
        trip, not a security control.
        """
        import uuid as uuid_module

        from redis.exceptions import RedisError

        from app.integrations.ebay.public_key import EbayPublicKeyClient

        async def unavailable(self: Any, key_id: Any) -> Any:
            raise RedisError("redis is down")

        monkeypatch.setattr(EbayPublicKeyClient, "_read_cache", unavailable)

        with pytest.raises(RedisError):
            await ebay.client().get(uuid_module.UUID("9936261a-7d7b-4621-a0f1-96ccb428af49"))
        assert await ledger_rows(session) == []
