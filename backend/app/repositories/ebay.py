"""eBay compliance ledger repository — a documented unscoped repository.

**The third unscoped repository in this codebase**, after ``TenantRepository``
and ``AuthenticationUserRepository``, and it is here for the same kind of
reason: the row it manages does not belong to a tenant.

An eBay account deletion is an instruction from eBay about a *person*. That
person may have data under several workspaces, or — as of EBAY-C0 — none. There
is no tenant to scope to, and inventing one would either fabricate an owner or
force one notification to become several rows, which would destroy the
uniqueness that makes duplicate delivery safe.

The narrowness that makes it acceptable:

* it is reachable only from the public, signature-verified compliance receiver,
  never from a merchant-authenticated endpoint;
* every row it writes is free of personal data by construction — see
  ``app/models/ebay.py``;
* it exposes no general query surface: four methods, all keyed by eBay's own
  notification id.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ebay import (
    EbayComplianceNotification,
    NotificationProcessing,
    NotificationVerification,
)
from app.repositories.base import BaseRepository


class EbayComplianceLedgerRepository(BaseRepository[EbayComplianceNotification]):
    """Durable receipt and idempotency state for eBay notifications."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, EbayComplianceNotification)

    async def get_by_notification_id(
        self, notification_id: str
    ) -> EbayComplianceNotification | None:
        result = await self.session.execute(
            select(EbayComplianceNotification)
            .where(EbayComplianceNotification.notification_id == notification_id)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def claim(
        self,
        *,
        notification_id: str,
        topic: str,
        schema_version: str,
        event_date: datetime | None,
        publish_date: datetime | None,
        payload_digest: str,
        received_at: datetime,
    ) -> EbayComplianceNotification:
        """Insert the receipt, letting the unique constraint arbitrate.

        Flushed rather than merely added, so a concurrent duplicate raises
        ``IntegrityError`` *here* — where the caller can turn it into an
        idempotent acknowledgement — instead of at some later commit, by which
        point erasure would already have run twice.
        """
        record = EbayComplianceNotification(
            notification_id=notification_id,
            topic=topic,
            schema_version=schema_version,
            event_date=event_date,
            publish_date=publish_date,
            payload_digest=payload_digest,
            verification_status=NotificationVerification.VERIFIED,
            processing_status=NotificationProcessing.RECEIVED,
            first_received_at=received_at,
            last_received_at=received_at,
            receipt_count=1,
            erased_record_count=0,
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def complete(
        self,
        record: EbayComplianceNotification,
        *,
        erased: int,
        outcome_code: str,
    ) -> EbayComplianceNotification:
        """Mark the erasure done. Only this state may be acknowledged to eBay."""
        record.processing_status = NotificationProcessing.COMPLETED
        record.erased_record_count = erased
        record.outcome_code = outcome_code
        await self.session.flush()
        return record

    async def record_repeat(
        self, record: EbayComplianceNotification, *, received_at: datetime
    ) -> EbayComplianceNotification:
        """Count a redelivery without re-running anything destructive."""
        record.receipt_count += 1
        record.last_received_at = received_at
        await self.session.flush()
        return record


__all__ = ["EbayComplianceLedgerRepository"]
