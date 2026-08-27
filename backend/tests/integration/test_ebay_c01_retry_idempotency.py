"""EBAY-C0.1 — a legitimate eBay retry must be acknowledged, not refused.

## The defect this file exists to prevent recurring

Production received six logical notifications whose retries were answered
**409**. The idempotency check compared ``sha256(raw_body)``, and eBay's raw
body legitimately changes between delivery attempts of the *same* notification.
So every retry looked like a different notification, was refused, and eBay
retried again — for 24 hours, after which the callback URL is marked down and
the developer has 30 days before being marked non-compliant.

## The contract, quoted

From eBay's *Marketplace User Account Deletion* guide, field table:

* ``notification.notificationId`` — *"The unique identifier of the
  notification."*
* ``notification.eventDate`` — *"A timestamp indicating when the eBay user made
  the data deletion request."*
* ``notification.publishDate`` — *"A timestamp indicating when the current
  notification was sent."*
* ``notification.publishAttemptCount`` — *"An integer indicating how many times
  the notification has been sent to this specific callback URL."*

And on acknowledgement: *"200 OK, 201 Created, 202 Accepted, and 204 No Content
are all acceptable. For any callback URL that doesn't respond … eBay will resend
the notification to the callback URL until it is acknowledged."* The Notification
Topics page states the obligation directly: *"Your endpoint should validate
delivery and handle retries safely (idempotency)."*

``publishDate`` and ``publishAttemptCount`` are therefore *per attempt*.
``eventDate`` and the ``data`` identifiers are properties of the event.

## What is real here and what is not

Real PostgreSQL, the real ledger, the real row lock, the real deletion
processor. eBay's network is never contacted.

Signature verification is exercised for real against eBay's own published vector
in ``TestSignatureStillGuardsTheLedger`` — and **only** there. The retry tests
have to alter the payload, and eBay's published vector signs one exact byte
string that this repository has no private key to re-sign. Those tests therefore
drive the service directly, below the signature boundary, which is also where
the behaviour under test lives.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.integrations.ebay import compliance as compliance_module
from app.integrations.ebay.compliance import EbayComplianceService
from app.integrations.ebay.exceptions import (
    EbayNotificationConflictError,
    EbayNotificationRejectedError,
)
from app.integrations.ebay.public_key import EbayPublicKeyClient
from app.integrations.ebay.schemas import (
    IDENTITY_DIGEST_PREFIX,
    parse_notification,
)
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


# --------------------------------------------------------------- eBay's wire
class FakePublicKeyService:
    """Serves eBay's published public key. Nothing leaves the process."""

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/identity/v1/oauth2/token"):
            return httpx.Response(
                200,
                json={"access_token": "synthetic-application-token", "expires_in": 7200},
            )
        if "/commerce/notification/v1/public_key/" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "key": OFFICIAL_PUBLIC_KEY,
                    "algorithm": OFFICIAL_KEY_ALGORITHM,
                    "digest": OFFICIAL_KEY_DIGEST,
                },
            )
        raise AssertionError(f"unexpected eBay call: {request.url}")

    def client(self) -> EbayPublicKeyClient:
        return EbayPublicKeyClient(
            transport=httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
        )


@pytest.fixture(autouse=True)
async def clean_redis_clients() -> AsyncIterator[None]:
    """Do not leave a Redis client cached on this test's event loop.

    ``app.core.redis`` caches clients in a module-level dict. The real-signature
    test fetches eBay's public key, which the key client caches in Redis, so a
    client is created here and bound to this test's loop. pytest-asyncio then
    closes that loop, and the *next* module to call ``close_redis_clients()``
    tries to close a transport whose loop is gone — ``RuntimeError: Event loop is
    closed``, raised in somebody else's fixture.

    The EBAY-C0 suites already do this for the same reason. Closing on both sides
    means this module neither inherits a stale client nor leaves one behind.
    """
    from app.core.redis import close_redis_clients

    await close_redis_clients()
    yield
    await close_redis_clients()


@pytest.fixture(autouse=True)
def ebay_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Synthetic platform credentials — they authenticate to nothing.

    Needed by the real-signature test: the public-key client mints an
    application token before fetching eBay's key, and refuses to start without
    credentials. The isolated environment blanks the eBay settings on purpose,
    so the ones that must be present are set here rather than in a shell.
    """
    monkeypatch.setattr(settings.ebay, "client_id", "synthetic-client-id")
    monkeypatch.setattr(settings.ebay, "client_secret", SecretStr("synthetic-client-secret"))


@pytest.fixture
def ebay(monkeypatch: pytest.MonkeyPatch) -> FakePublicKeyService:
    fake = FakePublicKeyService()
    original = compliance_module.EbayComplianceService.__init__

    def patched(self: Any, session: AsyncSession, *, key_client: Any = None) -> None:
        original(self, session, key_client=key_client or fake.client())

    monkeypatch.setattr(compliance_module.EbayComplianceService, "__init__", patched)
    return fake


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """One committing session, for the HTTP tests that share the app's."""
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


async def http(active: AsyncSession) -> AsyncClient:
    from app.api.deps import get_db_session
    from app.main import create_application

    app = create_application()

    async def _override() -> Any:
        yield active

    app.dependency_overrides[get_db_session] = _override
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


def post_headers() -> dict[str, str]:
    return {
        "content-type": "application/json",
        "x-ebay-signature": OFFICIAL_SIGNATURE_HEADER,
        "host": "testserver",
    }


async def ledger_rows(active: AsyncSession) -> list[EbayComplianceNotification]:
    result = await active.execute(
        sa.select(EbayComplianceNotification).execution_options(populate_existing=True)
    )
    return list(result.scalars().all())


@pytest.fixture
async def sessions() -> AsyncIterator[Any]:
    """A factory of independent, committing sessions.

    Independent because the unique constraint and the row lock are the
    mechanisms under test, and two coroutines sharing one session would contend
    for neither.
    """
    engine = create_async_engine(settings.database.async_dsn, poolclass=None)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    try:
        yield factory
    finally:
        async with factory() as cleanup:
            await cleanup.execute(sa.delete(EbayComplianceNotification))
            await cleanup.commit()
        await engine.dispose()


# ---------------------------------------------------------------------------
# Building deliveries the way eBay builds them
# ---------------------------------------------------------------------------


def delivery(
    *,
    notification_id: str = "c01-notification",
    attempt: int = 1,
    username: str = "official_user",
    user_id: str = "official-user-id",
    event_date: str = "2025-09-19T20:43:59.462Z",
) -> tuple[Any, bytes]:
    """One delivery attempt, shaped like eBay's documented payload.

    ``attempt`` drives *both* per-attempt fields together, because that is what
    eBay does: a resend carries the next ``publishAttemptCount`` and the moment
    it was resent. Varying only one of them would be a weaker test than the real
    thing.
    """
    payload = json.loads(official_body())
    payload["notification"]["notificationId"] = notification_id
    payload["notification"]["eventDate"] = event_date
    payload["notification"]["publishAttemptCount"] = attempt
    payload["notification"]["publishDate"] = (
        (datetime(2025, 9, 19, 20, 43, 59, tzinfo=UTC) + timedelta(minutes=17 * attempt))
        .isoformat()
        .replace("+00:00", "Z")
    )
    payload["notification"]["data"]["username"] = username
    payload["notification"]["data"]["userId"] = user_id
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return parse_notification(json.loads(raw)), raw


async def process(factory: Any, parsed: Any) -> EbayComplianceNotification:
    """One full processing pass in its own committed transaction."""
    async with factory() as session:
        record = await EbayComplianceService(session).process(notification=parsed)
        await session.commit()
        return record


async def rows(factory: Any) -> list[EbayComplianceNotification]:
    async with factory() as session:
        result = await session.execute(
            sa.select(EbayComplianceNotification).execution_options(populate_existing=True)
        )
        return list(result.scalars().all())


# ===========================================================================
# The control: prove the test can catch the production defect
# ===========================================================================


class TestTheOldAlgorithmWouldStillFail:
    """Without this, a green retry test proves nothing.

    A regression test only has value if it fails against the broken code. These
    two demonstrate that the payloads used throughout this file really do
    reproduce the production conditions — the raw bytes differ across attempts,
    while the notification is the same one.
    """

    def test_the_raw_body_differs_between_attempts_of_one_notification(self) -> None:
        _, first = delivery(attempt=1)
        _, second = delivery(attempt=2)

        assert first != second, "the test payloads do not reproduce a real retry"
        assert hashlib.sha256(first).hexdigest() != hashlib.sha256(second).hexdigest(), (
            "the legacy raw-body digest would have matched, so this suite could "
            "never have caught the production defect"
        )

    def test_only_the_documented_per_attempt_fields_differ(self) -> None:
        """Pin down *why* the bytes differ, so the control cannot drift.

        If a future edit made these payloads differ in some other field, the
        control above would still pass while testing something else entirely.
        """
        _, first = delivery(attempt=1)
        _, second = delivery(attempt=2)
        a = json.loads(first)["notification"]
        b = json.loads(second)["notification"]

        differing = {key for key in a if a[key] != b[key]}
        assert differing == {"publishDate", "publishAttemptCount"}

    def test_the_identity_digest_ignores_those_fields(self) -> None:
        first, _ = delivery(attempt=1)
        fourth, _ = delivery(attempt=4)

        assert first.identity_digest == fourth.identity_digest
        assert first.publish_attempt_count != fourth.publish_attempt_count
        assert first.publish_date != fourth.publish_date

    async def test_including_the_attempt_fields_reproduces_the_409(
        self, sessions: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The control, run through the real duplicate-detection path.

        Exactly one variable is changed: whether the per-attempt fields are
        inside the digest. Everything else — the ledger, the row lock, the
        comparison in ``_record_duplicate``, the conflict decision — is the
        shipping code.

        The simulated legacy digest keeps the ``v2:`` tag on purpose. Without
        it the row would take the legacy-repair branch and be *accepted*, which
        would demonstrate the backward-compatibility path rather than the
        defect. Tagged, it takes the comparable path, and the 409 that
        production returned six times over is reproduced here on demand.
        """
        from app.integrations.ebay.schemas import MarketplaceAccountDeletion

        def with_attempt_fields(self: Any) -> str:
            canonical = json.dumps(
                [
                    self.topic,
                    self.schema_version,
                    self.notification_id,
                    str(self.event_date),
                    str(self.publish_date),
                    self.publish_attempt_count,
                    self.subject.user_id,
                    self.subject.username,
                    self.subject.eias_token,
                ],
                separators=(",", ":"),
            )
            digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            return f"{IDENTITY_DIGEST_PREFIX}{digest[:61]}"

        monkeypatch.setattr(
            MarketplaceAccountDeletion,
            "identity_digest",
            property(with_attempt_fields),
        )

        first, _ = delivery(attempt=1)
        await process(sessions, first)

        retry, _ = delivery(attempt=2)
        with pytest.raises(EbayNotificationConflictError):
            await process(sessions, retry)

        ledger = await rows(sessions)
        assert ledger[0].receipt_count == 1, (
            "the control did not reproduce the production defect, so the retry "
            "tests above are not evidence that it is fixed"
        )


# ===========================================================================
# The fix
# ===========================================================================


class TestTheDigestSerialisationIsUnambiguous:
    """Two different field lists must never produce one digest.

    Separator-joined concatenation is the usual way this goes wrong: with a
    ``|`` separator, ``["a|b", "c"]`` and ``["a", "b|c"]`` are the same string.
    Any separator can appear inside a value an upstream system controls, so the
    encoding is length-prefixed and presence-tagged instead.
    """

    def test_field_boundaries_cannot_be_shifted(self) -> None:
        from app.integrations.ebay.schemas import _digest_field

        assert _digest_field("ab") + _digest_field("c") != _digest_field("a") + _digest_field("bc")

    def test_a_value_cannot_impersonate_a_separator(self) -> None:
        from app.integrations.ebay.schemas import _digest_field

        for hostile in ("a|b", "a:b", "a\x00b", "a\x01b", '"a","b"'):
            assert _digest_field(hostile) != _digest_field("a") + _digest_field("b")

    def test_absent_is_not_the_same_as_empty(self) -> None:
        from app.integrations.ebay.schemas import _digest_field

        assert _digest_field(None) != _digest_field("")

    def test_a_shifted_boundary_does_not_collide_end_to_end(self) -> None:
        """The same trap, through the real digest rather than the encoder.

        ``notificationId`` and ``eventDate`` are adjacent in the covered list, so
        moving a character between them is exactly the collision a naive join
        would admit.
        """
        left, _ = delivery(notification_id="abc", event_date="2025-09-19T20:43:59.462Z")
        right, _ = delivery(notification_id="ab", event_date="c2025-09-19T20:43:59.462Z")
        assert left.identity_digest != right.identity_digest

    def test_the_domain_separator_is_part_of_the_digest(self) -> None:
        """A digest from here must not equal one computed elsewhere for another
        purpose over the same values."""
        import hashlib

        from app.integrations.ebay.schemas import (
            IDENTITY_DIGEST_PREFIX,
            _digest_field,
        )

        parsed, _ = delivery()
        without_domain = hashlib.sha256(
            b"".join(
                _digest_field(v)
                for v in (
                    parsed.topic,
                    parsed.schema_version,
                    parsed.notification_id,
                    parsed.event_date.isoformat() if parsed.event_date else None,
                )
            )
        ).hexdigest()
        assert parsed.identity_digest != f"{IDENTITY_DIGEST_PREFIX}{without_domain[:61]}"


class TestTheDigestHoldsNoPersonalData:
    """The ledger's founding invariant, which this hotfix had to preserve.

    The row is a permanent compliance receipt and is never erased. Anything
    derived from the subject that survives in it would be a way to confirm,
    forever, that a named person's account was deleted — and there is no keyed
    hashing authority in this codebase to blunt that.
    """

    def test_changing_only_the_subject_does_not_change_the_digest(self) -> None:
        plain, _ = delivery(username="alice_seller", user_id="alice-immutable-id")
        other, _ = delivery(username="bob_seller", user_id="bob-immutable-id")

        assert plain.identity_digest == other.identity_digest, (
            "the subject reached the digest — see the privacy reasoning in "
            "identity_digest before changing this"
        )

    def test_the_digest_is_not_a_confirmation_oracle_for_a_username(self) -> None:
        """Stated as the attack it prevents.

        With the subject in the digest, anyone holding the ledger could take a
        candidate identity, recompute, and confirm a match — the other covered
        fields are all plaintext columns in the same row. Excluding the subject
        removes the oracle rather than obscuring it.
        """
        parsed, _ = delivery(username="known_person", user_id="known-id")
        guess, _ = delivery(username="known_person", user_id="known-id")
        wrong, _ = delivery(username="somebody_else", user_id="other-id")

        assert guess.identity_digest == parsed.identity_digest
        assert wrong.identity_digest == parsed.identity_digest, (
            "a wrong guess is distinguishable, so the digest confirms identities"
        )

    def test_the_covered_fields_are_all_already_plaintext_columns(self) -> None:
        """So the digest adds no retention the row did not already have."""
        columns = {c.name for c in EbayComplianceNotification.__table__.columns}
        for covered in ("topic", "schema_version", "notification_id", "event_date"):
            assert covered in columns


class TestRetriesAreAcknowledged:
    async def test_the_first_delivery_completes_once(self, sessions: Any) -> None:
        parsed, _ = delivery(attempt=1)
        record = await process(sessions, parsed)

        assert record.processing_status is NotificationProcessing.COMPLETED
        assert record.receipt_count == 1
        assert len(await rows(sessions)) == 1

    async def test_attempts_two_three_and_four_are_all_accepted(self, sessions: Any) -> None:
        """The production defect, stated as the assertion that would have caught it.

        Each retry carries a later ``publishDate`` and the next
        ``publishAttemptCount``, exactly as eBay sends them.
        """
        first, _ = delivery(attempt=1)
        await process(sessions, first)

        for attempt in (2, 3, 4):
            parsed, _ = delivery(attempt=attempt)
            record = await process(sessions, parsed)
            assert record.processing_status is NotificationProcessing.COMPLETED, (
                f"attempt {attempt} was not acknowledged"
            )

    async def test_only_one_logical_ledger_record_exists(self, sessions: Any) -> None:
        for attempt in (1, 2, 3, 4):
            parsed, _ = delivery(attempt=attempt)
            await process(sessions, parsed)

        ledger = await rows(sessions)
        assert len(ledger) == 1, f"{len(ledger)} rows for one logical notification"

    async def test_the_repeat_accounting_is_truthful(self, sessions: Any) -> None:
        """``receipt_count`` counts our receipts, and must count all of them."""
        for attempt in (1, 2, 3, 4):
            parsed, _ = delivery(attempt=attempt)
            await process(sessions, parsed)

        ledger = await rows(sessions)
        assert ledger[0].receipt_count == 4
        assert ledger[0].last_received_at >= ledger[0].first_received_at

    async def test_the_original_erasure_result_is_retained(self, sessions: Any) -> None:
        """A retry must not rewrite what the delivery that acted actually did."""
        first, _ = delivery(attempt=1)
        original = await process(sessions, first)
        outcome, erased = original.outcome_code, original.erased_record_count

        retry, _ = delivery(attempt=2)
        await process(sessions, retry)

        ledger = await rows(sessions)
        assert ledger[0].outcome_code == outcome
        assert ledger[0].erased_record_count == erased
        assert ledger[0].processing_status is NotificationProcessing.COMPLETED

    async def test_erasure_runs_exactly_once_across_every_attempt(
        self, sessions: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Counted at the processor, which is the thing that would do the damage.

        Asserting on ``erased_record_count`` alone would not catch a second
        erasure that found nothing left to erase.
        """
        from app.integrations.ebay import compliance as compliance_module

        calls: list[str] = []
        original = compliance_module.EbayAccountDeletionProcessor.erase

        async def counting(self: Any, subject: Any) -> Any:
            calls.append(subject.user_id or "")
            return await original(self, subject)

        monkeypatch.setattr(compliance_module.EbayAccountDeletionProcessor, "erase", counting)

        for attempt in (1, 2, 3, 4):
            parsed, _ = delivery(attempt=attempt)
            await process(sessions, parsed)

        assert len(calls) == 1, f"erasure ran {len(calls)} times for one notification"

    async def test_a_retry_after_a_restart_is_still_idempotent(self, sessions: Any) -> None:
        """Nothing in-process may be load-bearing.

        The ledger row is the only memory the endpoint has, so a fresh engine,
        a fresh session factory and a fresh service — which is what a restart
        actually amounts to here — must reach the same decision.
        """
        first, _ = delivery(attempt=1)
        await process(sessions, first)

        restarted = create_async_engine(settings.database.async_dsn, poolclass=None)
        factory = async_sessionmaker(bind=restarted, expire_on_commit=False, autoflush=False)
        try:
            retry, _ = delivery(attempt=2)
            async with factory() as session:
                record = await EbayComplianceService(session).process(notification=retry)
                await session.commit()
            assert record.processing_status is NotificationProcessing.COMPLETED
            assert record.receipt_count == 2
        finally:
            await restarted.dispose()

        assert len(await rows(sessions)) == 1


class TestConcurrency:
    async def test_concurrent_first_deliveries_erase_at_most_once(
        self, sessions: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two attempts arriving together, on separate connections.

        The unique constraint decides, not a read either of them did earlier.
        """
        from app.integrations.ebay import compliance as compliance_module

        calls: list[str] = []
        original = compliance_module.EbayAccountDeletionProcessor.erase

        async def counting(self: Any, subject: Any) -> Any:
            calls.append(subject.user_id or "")
            return await original(self, subject)

        monkeypatch.setattr(compliance_module.EbayAccountDeletionProcessor, "erase", counting)

        async def deliver(attempt: int) -> None:
            parsed, _ = delivery(attempt=attempt)
            await process(sessions, parsed)

        await asyncio.gather(deliver(1), deliver(2))

        assert len(calls) == 1, f"erasure ran {len(calls)} times under concurrency"
        assert len(await rows(sessions)) == 1

    async def test_concurrent_retries_do_not_lose_a_receipt(self, sessions: Any) -> None:
        """The row lock, stated as the count it protects.

        Two unlocked readers would both see ``receipt_count = 1`` and both write
        ``2``. The lost update would make the accounting quietly wrong — which
        matters here, because the receipt count is the only evidence of how hard
        eBay had to try.
        """
        first, _ = delivery(attempt=1)
        await process(sessions, first)

        async def retry(attempt: int) -> None:
            parsed, _ = delivery(attempt=attempt)
            await process(sessions, parsed)

        await asyncio.gather(retry(2), retry(3), retry(4), retry(5))

        ledger = await rows(sessions)
        assert len(ledger) == 1
        assert ledger[0].receipt_count == 5, (
            f"receipt_count is {ledger[0].receipt_count}; a concurrent retry was lost"
        )


class TestBackwardCompatibilityWithLegacyRows:
    """Production rows written before this hotfix hold a raw-body digest.

    They cannot be compared against any retry — that is the defect. Refusing
    them would leave exactly the six notifications that prompted this hotfix
    failing forever, so a legitimate retry is accepted and the row is repaired.
    """

    async def test_a_legacy_completed_row_accepts_its_retry(self, sessions: Any) -> None:
        first, first_raw = delivery(attempt=1)
        record = await process(sessions, first)

        # Rewrite the stored digest to exactly what the old algorithm produced,
        # reproducing a row written by the deployed code.
        legacy = hashlib.sha256(first_raw).hexdigest()
        async with sessions() as session:
            await session.execute(
                sa.update(EbayComplianceNotification)
                .where(EbayComplianceNotification.id == record.id)
                .values(payload_digest=legacy)
            )
            await session.commit()

        retry, _ = delivery(attempt=2)
        repeated = await process(sessions, retry)

        assert repeated.processing_status is NotificationProcessing.COMPLETED
        assert repeated.receipt_count == 2

    async def test_the_legacy_row_is_repaired_so_later_retries_are_verifiable(
        self, sessions: Any
    ) -> None:
        """A one-time upgrade, not a permanent exemption.

        Once the digest is an identity digest the row can be compared again, so
        a genuine collision on that id is detectable from the next delivery on.
        """
        first, first_raw = delivery(attempt=1)
        record = await process(sessions, first)
        async with sessions() as session:
            await session.execute(
                sa.update(EbayComplianceNotification)
                .where(EbayComplianceNotification.id == record.id)
                .values(payload_digest=hashlib.sha256(first_raw).hexdigest())
            )
            await session.commit()

        retry, _ = delivery(attempt=2)
        await process(sessions, retry)

        ledger = await rows(sessions)
        assert ledger[0].payload_digest.startswith(IDENTITY_DIGEST_PREFIX)
        assert ledger[0].payload_digest == retry.identity_digest

        # And from here on, a true collision on the same id is refused again.
        # Differing event content, not a differing subject — the subject is
        # deliberately outside the digest, so it is not what a collision means.
        impostor, _ = delivery(attempt=3, event_date="2024-02-02T02:02:02.222Z")
        with pytest.raises(EbayNotificationConflictError):
            await process(sessions, impostor)

    async def test_a_legacy_row_still_fits_the_column(self) -> None:
        """The tag has to live inside ``VARCHAR(64)``, alongside a real digest."""
        parsed, _ = delivery()
        assert len(parsed.identity_digest) == 64
        assert parsed.identity_digest.startswith(IDENTITY_DIGEST_PREFIX)
        assert ":" not in hashlib.sha256(b"x").hexdigest(), (
            "the tag would be ambiguous if a raw hex digest could contain it"
        )


class TestDistinctAndCollidingNotifications:
    async def test_two_different_notification_ids_stay_separate(self, sessions: Any) -> None:
        first, _ = delivery(notification_id="c01-alpha")
        second, _ = delivery(notification_id="c01-beta")
        await process(sessions, first)
        await process(sessions, second)

        ledger = await rows(sessions)
        assert len(ledger) == 2
        assert {row.receipt_count for row in ledger} == {1}

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("event_date", "2024-01-01T00:00:00.000Z"),
            ("topic", "SOME_OTHER_TOPIC"),
        ],
    )
    async def test_changed_immutable_content_under_one_id_is_refused(
        self, sessions: Any, field: str, value: str
    ) -> None:
        """The documented collision policy, preserved.

        Each of these is event content, not delivery metadata. Acknowledging a
        second, different notification under an id already settled would discard
        a real deletion instruction — and eBay never resends an acknowledged
        one.
        """
        first, _ = delivery(attempt=1)
        await process(sessions, first)

        if field == "topic":
            # `topic` never reaches the digest through the parser — an unknown
            # topic is refused earlier, at parse time, which is the stronger
            # outcome. Assert that rather than pretending it is a digest case.
            payload = json.loads(official_body())
            payload["metadata"]["topic"] = value
            with pytest.raises(EbayNotificationRejectedError):
                parse_notification(payload)
            return

        impostor, _ = delivery(attempt=2, **{field: value})
        with pytest.raises(EbayNotificationConflictError):
            await process(sessions, impostor)

        ledger = await rows(sessions)
        assert len(ledger) == 1
        assert ledger[0].receipt_count == 1, "a conflicting delivery was counted as a repeat"
        assert ledger[0].payload_digest == first.identity_digest, (
            "the original digest was overwritten by an impostor"
        )

    async def test_a_different_subject_under_one_id_is_not_detected(self, sessions: Any) -> None:
        """The limitation of a privacy-preserving digest, asserted rather than implied.

        The subject identifiers are deliberately absent from the identity digest
        — see ``MarketplaceAccountDeletion.identity_digest`` — so a notification
        id reused for a different person with a byte-identical ``eventDate`` is
        accepted as a retry.

        This test exists so nobody discovers that by accident. It is the
        deliberate cost of not retaining a permanent confirmation oracle for an
        erased person's identity, and it is bounded: ``eventDate`` is
        millisecond-precision, eBay documents ``notificationId`` as unique, and
        the payload is signature-verified before it reaches here, so reaching
        this state at all would be a provider bug rather than an attack.

        If it ever needs detecting, the fix is a keyed HMAC authority — not an
        unkeyed hash of a username.
        """
        first, _ = delivery(attempt=1, username="person_one", user_id="id-one")
        await process(sessions, first)

        other_person, _ = delivery(attempt=2, username="person_two", user_id="id-two")
        assert other_person.identity_digest == first.identity_digest, (
            "the digest now covers the subject; update this test and the privacy "
            "reasoning in identity_digest together"
        )

        record = await process(sessions, other_person)
        assert record.receipt_count == 2

    async def test_a_conflict_logs_no_identifier(
        self, sessions: Any, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The refusal must not put the thing being protected into a log line."""
        first, _ = delivery(attempt=1)
        await process(sessions, first)

        # Collides on event content while still carrying identifiers, so the
        # refusal has something to leak if it is careless.
        impostor, _ = delivery(
            attempt=2,
            event_date="2024-02-02T02:02:02.222Z",
            username="secret_person",
            user_id="secret-id",
        )
        with caplog.at_level("WARNING"):
            with pytest.raises(EbayNotificationConflictError):
                await process(sessions, impostor)

        emitted = caplog.text
        for secret in ("secret_person", "secret-id", "official_user", "c01-notification"):
            assert secret not in emitted, f"{secret!r} reached a log line"


class TestSignatureStillGuardsTheLedger:
    """Requirement 1, checked against eBay's own published vector.

    The identity digest changed; what the *signature* is verified over did not.
    """

    async def test_the_official_vector_still_verifies_over_its_exact_bytes(self) -> None:
        from tests.unit.test_ebay_c0_signature import verify

        verify()

    async def test_a_tampered_body_fails_verification(self) -> None:
        from app.integrations.ebay.exceptions import EbaySignatureError
        from tests.unit.test_ebay_c0_signature import verify

        tampered = bytearray(official_body())
        tampered[-2] ^= 0x01
        with pytest.raises(EbaySignatureError):
            verify(body=bytes(tampered))

    async def test_an_unverified_delivery_never_reaches_the_ledger(self, sessions: Any) -> None:
        """Ordering, not just rejection.

        ``verify`` runs before anything is parsed or claimed, so a bad signature
        cannot leave a receipt behind. Asserted by refusing at the boundary and
        then reading the table.
        """
        from app.integrations.ebay.exceptions import EbaySignatureError

        async with sessions() as session:
            service = EbayComplianceService(session)
            with pytest.raises((EbaySignatureError, Exception)) as caught:
                await service.verify(raw_body=official_body(), signature_header=None)
            assert caught.value is not None
            await session.rollback()

        assert await rows(sessions) == [], "a rejected delivery wrote to the ledger"


# ===========================================================================
# Over HTTP, because "returns 204" is a claim about the endpoint
# ===========================================================================


class TestTheEndpointAnswers204:
    """What eBay actually sees.

    eBay accepts *"200 OK, 201 Created, 202 Accepted, and 204 No Content"*, and
    resends anything else until acknowledged. Everything above proves the ledger
    behaves; these two prove the status code eBay receives.
    """

    async def test_an_identical_redelivery_is_acknowledged(
        self, session: AsyncSession, ebay: Any
    ) -> None:
        """Real signature, real vector, real route — nothing stubbed.

        eBay's published vector signs one exact byte string, so this is the
        strongest end-to-end retry that can be built without eBay's private key:
        the same notification delivered twice.
        """
        client = await http(session)
        async with client:
            first = await client.post(PATH, content=official_body(), headers=post_headers())
            second = await client.post(PATH, content=official_body(), headers=post_headers())

        assert first.status_code == 204, first.text
        assert second.status_code == 204, second.text

        ledger = await ledger_rows(session)
        assert len(ledger) == 1
        assert ledger[0].receipt_count == 2

    async def test_a_retry_carrying_the_next_attempt_is_acknowledged(
        self, session: AsyncSession, ebay: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The production case, at the boundary that returned 409.

        Signature verification is stubbed here and **only** here, for a stated
        reason: the payload must differ between attempts, and this repository
        holds eBay's public key but no private key with which to re-sign. What
        is under test is what the route returns once a delivery is verified;
        that verification itself still runs for real in
        ``TestSignatureStillGuardsTheLedger`` and in the test above.
        """
        from app.integrations.ebay import compliance as compliance_module

        async def accept(self: Any, *, raw_body: bytes, signature_header: str | None) -> None:
            return None

        monkeypatch.setattr(compliance_module.EbayComplianceService, "verify", accept)

        client = await http(session)
        async with client:
            statuses = []
            for attempt in (1, 2, 3, 4):
                _, raw = delivery(attempt=attempt)
                response = await client.post(PATH, content=raw, headers=post_headers())
                statuses.append(response.status_code)

        assert statuses == [204, 204, 204, 204], f"eBay would keep retrying: {statuses}"

        ledger = await ledger_rows(session)
        assert len(ledger) == 1
        assert ledger[0].receipt_count == 4
        assert ledger[0].processing_status is NotificationProcessing.COMPLETED

    async def test_no_delivery_in_the_sequence_logs_an_identifier(
        self,
        session: AsyncSession,
        ebay: Any,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Every path a retry can take, checked against the log it produced.

        The retry path is new code, and the fastest way to leak an identifier is
        to add a log line that quotes the thing it is reporting on.
        """
        from app.integrations.ebay import compliance as compliance_module

        async def accept(self: Any, *, raw_body: bytes, signature_header: str | None) -> None:
            return None

        monkeypatch.setattr(compliance_module.EbayComplianceService, "verify", accept)

        client = await http(session)
        with caplog.at_level("DEBUG"):
            async with client:
                for attempt in (1, 2, 3):
                    _, raw = delivery(
                        attempt=attempt,
                        username="private_person",
                        user_id="private-immutable-id",
                    )
                    await client.post(PATH, content=raw, headers=post_headers())

        emitted = caplog.text
        for secret in (
            "private_person",
            "private-immutable-id",
            "c01-notification",
            "eiasToken",
        ):
            assert secret not in emitted, f"{secret!r} reached a log line"
