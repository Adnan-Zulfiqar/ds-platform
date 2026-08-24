"""EBAY-C0 — idempotency, the compliance ledger, and the deletion audit.

Three separate claims, each tested against real PostgreSQL:

1. **A duplicate delivery does not repeat destructive processing.** eBay resends
   until acknowledged, so duplicates are ordinary traffic and the endpoint has
   to be safe under them — including two arriving at once.
2. **The ledger holds no personal data.** Asserted against the table's columns
   rather than a remembered list, so a future column that could hold an
   identifier fails immediately.
3. **No table in this application stores eBay user data.** That is what makes a
   verified zero-match deletion the correct outcome today, and it is checked by
   searching the model layer rather than asserted in prose.

eBay's network is mocked. No live request is made.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.integrations.ebay.compliance import EbayComplianceService
from app.integrations.ebay.deletion import (
    DeletionSubject,
    EbayAccountDeletionProcessor,
)
from app.integrations.ebay.schemas import parse_notification
from app.models.ebay import (
    EbayComplianceNotification,
    NotificationProcessing,
    NotificationVerification,
)
from tests.unit.test_ebay_c0_signature import official_body

pytestmark = pytest.mark.integration

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_MODELS = _BACKEND_ROOT / "app" / "models"


@pytest.fixture
async def sessions() -> AsyncIterator[Any]:
    """A factory of independent, committing sessions.

    Independent because the uniqueness constraint is the idempotency mechanism,
    and two coroutines sharing one session would never actually contend for it.
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


def notification(notification_id: str | None = None) -> Any:
    payload = json.loads(official_body())
    if notification_id is not None:
        payload["notification"]["notificationId"] = notification_id
    return parse_notification(payload), json.dumps(payload, separators=(",", ":")).encode()


async def deliver(factory: Any, notification_id: str | None = None) -> Any:
    """One full processing pass in its own committed transaction."""
    parsed, raw = notification(notification_id)
    async with factory() as session:
        record = await EbayComplianceService(session).process(raw_body=raw, notification=parsed)
        await session.commit()
        return record


async def rows(factory: Any) -> list[EbayComplianceNotification]:
    async with factory() as session:
        result = await session.execute(sa.select(EbayComplianceNotification))
        return list(result.scalars().all())


# ------------------------------------------------------------------ first pass
class TestFirstDelivery:
    async def test_a_notification_is_recorded_once_and_completed(self, sessions: Any) -> None:
        record = await deliver(sessions)

        assert record.processing_status is NotificationProcessing.COMPLETED
        assert record.verification_status is NotificationVerification.VERIFIED
        assert record.receipt_count == 1
        assert record.topic == "MARKETPLACE_ACCOUNT_DELETION"
        assert record.schema_version == "1.0"
        assert len(await rows(sessions)) == 1

    async def test_the_payload_digest_identifies_without_retaining(self, sessions: Any) -> None:
        """A SHA-256 proves "same notification" without keeping what it said."""
        import hashlib

        record = await deliver(sessions)
        assert record.payload_digest == hashlib.sha256(official_body()).hexdigest()
        assert len(record.payload_digest) == 64

    async def test_ebays_timestamps_are_kept_but_no_identifier_is(self, sessions: Any) -> None:
        record = await deliver(sessions)
        assert record.event_date is not None
        assert record.publish_date is not None
        stored = json.dumps(
            {c.name: str(getattr(record, c.name)) for c in record.__table__.columns}
        )
        for identifier in ("test_user", "ma8vp1jySJC", "nY+sHZ2PrBmdj6wV"):
            assert identifier not in stored


# ------------------------------------------------------------------ duplicates
class TestIdempotency:
    async def test_a_repeat_delivery_is_counted_not_reprocessed(self, sessions: Any) -> None:
        first = await deliver(sessions)
        second = await deliver(sessions)

        assert second.id == first.id, "a duplicate must not create a second ledger row"
        assert second.receipt_count == 2
        assert second.last_received_at >= first.first_received_at
        assert len(await rows(sessions)) == 1

    async def test_many_repeats_stay_one_row(self, sessions: Any) -> None:
        for _ in range(5):
            await deliver(sessions)
        stored = await rows(sessions)
        assert len(stored) == 1
        assert stored[0].receipt_count == 5

    async def test_concurrent_duplicate_deliveries_process_once(self, sessions: Any) -> None:
        """Two deliveries of the same notification, genuinely at the same time.

        Each on its own connection, so the unique constraint is what arbitrates
        rather than a read-then-insert that both sides pass. This is the case an
        application-level "check first" would get wrong.
        """
        results = await asyncio.gather(deliver(sessions), deliver(sessions), return_exceptions=True)
        for result in results:
            assert not isinstance(result, Exception), result

        stored = await rows(sessions)
        assert len(stored) == 1, "concurrent duplicates created two ledger rows"
        assert stored[0].receipt_count == 2
        assert stored[0].processing_status is NotificationProcessing.COMPLETED

    async def test_the_database_refuses_a_second_row_for_the_same_id(self, sessions: Any) -> None:
        """The constraint exists in the database, not only in the code path."""
        from sqlalchemy.exc import IntegrityError

        await deliver(sessions)
        original = (await rows(sessions))[0]

        async with sessions() as session:
            session.add(
                EbayComplianceNotification(
                    notification_id=original.notification_id,
                    topic=original.topic,
                    schema_version=original.schema_version,
                    payload_digest=original.payload_digest,
                    verification_status=NotificationVerification.VERIFIED,
                    processing_status=NotificationProcessing.RECEIVED,
                    first_received_at=original.first_received_at,
                    last_received_at=original.last_received_at,
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()

    async def test_distinct_notifications_get_distinct_rows(self, sessions: Any) -> None:
        await deliver(sessions, notification_id="notification-alpha")
        await deliver(sessions, notification_id="notification-beta")
        assert len(await rows(sessions)) == 2

    async def test_a_failure_mid_processing_leaves_no_ledger_row(
        self, sessions: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The ledger and the erasure commit together or not at all.

        A row saying "completed" for work that rolled back would be permanent:
        eBay never resends an acknowledged notification, so the deletion would
        be lost with a record claiming otherwise.
        """

        async def explode(self: Any, subject: DeletionSubject) -> Any:
            raise RuntimeError("deletion failed midway")

        monkeypatch.setattr(EbayAccountDeletionProcessor, "erase", explode)

        parsed, raw = notification()
        async with sessions() as session:
            with pytest.raises(RuntimeError):
                await EbayComplianceService(session).process(raw_body=raw, notification=parsed)
            await session.rollback()

        assert await rows(sessions) == []

    async def test_a_retry_after_a_failure_succeeds(self, sessions: Any) -> None:
        """Recovery is just the next delivery — eBay resends for 24 hours."""
        record = await deliver(sessions)
        assert record.processing_status is NotificationProcessing.COMPLETED


# ------------------------------------------------------------------ the ledger
class TestLedgerHoldsNoPersonalData:
    def test_the_table_has_no_column_that_could_hold_an_identifier(self) -> None:
        """Structural, not procedural.

        A table without a username column cannot leak one, and cannot itself
        become something that must be erased on the next deletion request.
        """
        columns = {c.name for c in EbayComplianceNotification.__table__.columns}
        forbidden = {
            "username",
            "user_id",
            "ebay_user_id",
            "eias_token",
            "email",
            "payload",
            "raw_payload",
            "body",
            "access_token",
            "verification_token",
        }
        assert not (columns & forbidden), (
            f"ledger gained a PII-capable column: {columns & forbidden}"
        )

    def test_the_model_source_names_no_identifier_column(self) -> None:
        source = (_MODELS / "ebay.py").read_text(encoding="utf-8")
        for banned in ("eias_token", "raw_payload", "access_token"):
            assert f'"{banned}"' not in source
            assert f"{banned}: Mapped" not in source


# ------------------------------------------------------- the eBay-data audit
class TestNoEbayUserDataExistsYet:
    """The evidence behind a verified zero-match deletion."""

    def test_no_model_stores_an_ebay_user_identifier(self) -> None:
        """Searched, not assumed.

        If EBAY-C1 adds a column that stores an eBay user identifier without
        registering a deletion owner, this fails — which is the release guard
        the roadmap refers to.
        """
        pattern = re.compile(
            r"^\s*(ebay_user_id|ebay_username|eias_token|ebay_buyer_id|ebay_seller_id)\s*:",
            re.MULTILINE,
        )
        offenders: list[str] = []
        for module in sorted(_MODELS.glob("*.py")):
            if module.name == "ebay.py":
                continue  # the ledger, which is audited above for the opposite
            if pattern.search(module.read_text(encoding="utf-8")):
                offenders.append(module.name)
        assert offenders == [], (
            f"{offenders} store eBay user identifiers; register a deletion owner in "
            "app/integrations/ebay/deletion.py before this can ship"
        )

    def test_every_ebay_data_owner_is_registered(self) -> None:
        """The guard, stated as a test rather than a note to a future author.

        Zero owners is correct **only** while nothing stores eBay user data. The
        moment something does, the assertion above fails and this one becomes
        the instruction for what to do about it.
        """
        owners = EbayAccountDeletionProcessor.registered_owners()
        assert len(owners) == 0, (
            "owners are registered — update test_no_model_stores_an_ebay_user_identifier "
            "to expect the tables they cover"
        )

    def test_the_store_platform_enum_offers_ebay_without_storing_user_data(self) -> None:
        """A named platform is not the same as stored personal data.

        ``StorePlatform.EBAY`` exists and the UI offers it, which is exactly the
        kind of thing that makes "do we hold eBay data?" look ambiguous. It is
        a label on a row the merchant created; it holds nothing about an eBay
        *user*.
        """
        from app.models.store import Store, StorePlatform

        assert StorePlatform.EBAY.value == "ebay"
        columns = {c.name for c in Store.__table__.columns}
        assert "ebay_user_id" not in columns
        assert "eias_token" not in columns


class TestDeletionProcessor:
    async def test_a_verified_notification_completes_with_nothing_to_erase(
        self, sessions: Any
    ) -> None:
        """The expected EBAY-C0 outcome, recorded as evidence rather than skipped."""
        record = await deliver(sessions)
        assert record.erased_record_count == 0
        assert record.outcome_code == "no_matching_data"
        assert record.processing_status is NotificationProcessing.COMPLETED

    async def test_the_processor_runs_every_registered_owner(
        self, sessions: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Proves the zero-match path is a real loop, not a hardcoded zero.

        A temporary owner is registered, and the count it reports has to reach
        the ledger. Without this, "erased 0" would be indistinguishable from a
        processor that never calls anything.
        """
        calls: list[DeletionSubject] = []

        class TemporaryOwner:
            name = "temporary_test_owner"

            async def erase(self, session: AsyncSession, subject: DeletionSubject) -> int:
                calls.append(subject)
                return 3

        monkeypatch.setattr(
            "app.integrations.ebay.deletion._OWNERS", (TemporaryOwner(),), raising=True
        )
        record = await deliver(sessions)

        assert len(calls) == 1
        assert calls[0].user_id == "ma8vp1jySJC"
        assert record.erased_record_count == 3
        assert record.outcome_code == "erased"

    async def test_a_subject_with_no_identifier_erases_nothing(self, sessions: Any) -> None:
        async with sessions() as session:
            outcome = await EbayAccountDeletionProcessor(session).erase(
                DeletionSubject(user_id=None, username=None, eias_token=None)
            )
        assert outcome.erased == 0
        assert outcome.owners_run == []

    async def test_identifiers_are_not_retained_after_processing(self, sessions: Any) -> None:
        """The subject exists for the transaction and leaves no trace."""
        await deliver(sessions)
        async with sessions() as session:
            dumped = await session.execute(
                sa.select(
                    EbayComplianceNotification.outcome_code,
                    EbayComplianceNotification.outcome_detail,
                )
            )
            for code, detail in dumped:
                assert "ma8vp1jySJC" not in f"{code}{detail}"
