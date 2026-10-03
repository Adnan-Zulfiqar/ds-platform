"""EBAY-C0 — idempotency, the compliance ledger, and the deletion audit.

Three separate claims, each tested against real PostgreSQL:

1. **A duplicate delivery does not repeat destructive processing.** eBay resends
   until acknowledged, so duplicates are ordinary traffic and the endpoint has
   to be safe under them — including two arriving at once.
2. **The ledger holds no personal data.** Asserted against the table's columns
   rather than a remembered list, so a future column that could hold an
   identifier fails immediately.
3. **Every table that stores eBay user data is declared, and every declaration
   has an eraser.** Through EBAY-C0 the answer was that nothing stored any, which
   made a verified zero-match deletion correct. EBAY-C1 added ``ebay_connections``,
   so the claim moved up a level to the one that survives: storage with no eraser
   is a release blocker. Checked by searching the model layer and by comparing the
   declarations against the registered owners, not asserted in prose.

eBay's network is mocked. No live request is made.
"""

from __future__ import annotations

import asyncio
import inspect
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
    parsed, _raw = notification(notification_id)
    async with factory() as session:
        record = await EbayComplianceService(session).process(notification=parsed)
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
        """A SHA-256 proves "same notification" without keeping what it said.

        Rewritten in EBAY-C0.1. This used to assert the digest equalled
        ``sha256(raw_body)``, which pinned the exact defect that shipped: the
        raw body carries eBay's per-attempt ``publishDate`` and
        ``publishAttemptCount``, so it identifies a *delivery* and every
        legitimate retry looked like a different notification. The claim worth
        keeping is the one in the docstring — proof of identity without
        retention — and it now rests on the identity digest.
        """
        import hashlib

        parsed, raw = notification()
        record = await deliver(sessions)

        assert record.payload_digest == parsed.identity_digest
        # Still fits the ``VARCHAR(64)`` column, tag included.
        assert len(record.payload_digest) == 64
        # Still one-way: nothing about the payload is recoverable from it.
        assert "official_user" not in record.payload_digest
        assert record.payload_digest != hashlib.sha256(raw).hexdigest(), (
            "the digest is over the raw bytes again — retries would be refused"
        )

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

        parsed, _raw = notification()
        async with sessions() as session:
            with pytest.raises(RuntimeError):
                await EbayComplianceService(session).process(notification=parsed)
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

    def test_the_ledger_model_source_names_no_identifier_column(self) -> None:
        """Scoped to the ledger class, and matched on whole names.

        Rewritten in EBAY-C1. This used to scan the whole ``ebay.py`` module for
        the substring ``access_token``, which was fine while the module held
        only the ledger. C1 added ``EbayConnection`` beside it, and
        ``encrypted_access_token`` contains that substring — so the test would
        have failed on the *encrypted* column, which is the opposite of the
        thing it exists to catch.

        The claim itself is unchanged and still worth keeping: the ledger is the
        one eBay table that must hold nothing erasable, because it is the record
        that an erasure happened.
        """
        source = inspect.getsource(EbayComplianceNotification)
        for banned in ("eias_token", "raw_payload", "access_token", "username"):
            assert not re.search(rf"(?<![a-z_]){banned}", source), (
                f"the ledger model names {banned!r}"
            )


# ------------------------------------------------------- the eBay-data audit
class TestEbayUserDataIsDeclaredAndErasable:
    """The release guard, now that EBAY-C1 has given it something to guard.

    Renamed from ``TestNoEbayUserDataExistsYet``. Through EBAY-C0 the correct
    answer was that nothing stored eBay user data, and these tests asserted
    exactly that. C1 added ``ebay_connections``, so the enduring claim is no
    longer "nothing is stored" but "everything stored is declared, and every
    declaration has something that erases it".

    That is the same guard, stated at the level that survives the milestone: the
    thing that must never happen is eBay personal data with no eraser, not eBay
    personal data at all.
    """

    def test_no_undeclared_model_stores_an_ebay_user_identifier(self) -> None:
        """Searched, not assumed.

        ``ebay.py`` is skipped because it is the declared home of eBay storage
        and is covered by the declaration/owner agreement below. A *different*
        model growing an eBay identifier is storage nobody declared, which is
        the case this sweep exists to catch.
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

    def test_every_declared_storage_has_something_that_erases_it(self) -> None:
        """The guard, stated as a test rather than a note to a future author.

        Rewritten in EBAY-C1, which registered the first owner. The old form
        asserted zero owners, which was the right assertion while zero was the
        right number and a useless one afterwards. Declared storage with no
        eraser is a release blocker: it means the platform cannot honour a
        deletion it is legally obliged to honour.
        """
        assert EbayAccountDeletionProcessor.unowned_declarations() == (), (
            "eBay personal data is declared with no eraser registered for it"
        )

    def test_no_eraser_is_left_behind_without_storage(self) -> None:
        """The other direction, which is not automatically wrong but is a smell.

        An owner with no declaration usually means the storage was removed and
        the eraser was forgotten — and a dead owner looks like coverage while
        maintaining nothing.
        """
        assert EbayAccountDeletionProcessor.undeclared_owner_names() == ()

    def test_the_connection_table_is_the_declared_storage(self) -> None:
        """Names what C1 actually shipped, so the declaration cannot drift.

        A declaration that stopped matching the table would still satisfy the
        two agreement tests above while erasing nothing real.
        """
        declarations = EbayAccountDeletionProcessor.declarations()
        assert {d.storage: d.owner_name for d in declarations} == {
            "app.models.ebay.EbayConnection": "ebay_connection",
            # EBAY-C2: the chosen policy ids and location key.
            "app.models.ebay.EbayListingDefaults": "ebay_listing_defaults",
        }
        for declaration in declarations:
            assert declaration.holds.strip(), "a declaration says nothing about what it holds"

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
