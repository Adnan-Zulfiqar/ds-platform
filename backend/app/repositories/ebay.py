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

import uuid
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError
from app.models.ebay import (
    EbayComplianceNotification,
    EbayConnection,
    EbayConnectionStatus,
    EbayListingDefaults,
    NotificationProcessing,
    NotificationVerification,
)
from app.repositories.base import BaseRepository, TenantScopedRepository


class EbayComplianceLedgerRepository(BaseRepository[EbayComplianceNotification]):
    """Durable receipt and idempotency state for eBay notifications."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, EbayComplianceNotification)

    async def get_by_notification_id(
        self, notification_id: str, *, for_update: bool = False
    ) -> EbayComplianceNotification | None:
        """Find a receipt by eBay's notification id.

        ``for_update`` takes a row lock, and the redelivery path uses it. eBay
        can deliver the same notification to this endpoint more than once at a
        time, and two unlocked readers would both see ``receipt_count = 1`` and
        both write ``2`` — a lost update that makes the repeat accounting quietly
        wrong. It also serialises the legacy-digest upgrade below, so only one
        deliverer rewrites the stored digest.

        A row that does not exist locks nothing, so this does not close the
        first-insert race. That is the unique constraint's job, in ``claim``.
        """
        query = select(EbayComplianceNotification).where(
            EbayComplianceNotification.notification_id == notification_id
        )
        if for_update:
            query = query.with_for_update()
        result = await self.session.execute(query.execution_options(populate_existing=True))
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
        self,
        record: EbayComplianceNotification,
        *,
        received_at: datetime,
        upgraded_digest: str | None = None,
    ) -> EbayComplianceNotification:
        """Count a redelivery without re-running anything destructive.

        The erasure result is untouched on purpose — ``processing_status``,
        ``erased_record_count`` and ``outcome_code`` still describe what was
        actually done, on the delivery that did it. ``publish_date`` is left at
        the first delivery's value for the same reason: it records the
        notification as received and processed, and rewriting it with each
        retry's timestamp would erase that.

        ``upgraded_digest`` rewrites a pre-EBAY-C0.1 raw-body digest to an
        identity digest, once, on the first retry that reaches a legacy row. See
        ``EbayComplianceService._record_duplicate`` for why that is safe.
        """
        record.receipt_count += 1
        record.last_received_at = received_at
        if upgraded_digest is not None:
            record.payload_digest = upgraded_digest
        await self.session.flush()
        return record


class EbayConnectionRepository(TenantScopedRepository[EbayConnection]):
    """A workspace's eBay seller connection. Tenant-scoped, like every other
    business repository.

    Note what is *not* here: no ``get_by_ebay_user_id`` that searches across
    tenants. Cross-tenant ownership is settled by the global unique constraint
    on ``ebay_user_id``, and the conflict surfaces as an integrity error on
    insert. A lookup would be an existence oracle — a caller could discover
    whether a given eBay seller uses the platform by observing which error came
    back — and it would race with a concurrent insert anyway.

    The one place that legitimately reads across tenants is the compliance
    deletion path, which acts on eBay's instruction rather than a merchant's
    request. It lives in ``app.integrations.ebay.deletion`` and holds its own
    narrowly-scoped statement rather than a general query surface here.
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, EbayConnection)

    async def get_for_tenant(self) -> EbayConnection | None:
        """The current tenant's connection, or ``None``.

        ``populate_existing`` because callers act on freshly-refreshed token
        state; a stale identity-mapped row could hand back an access token that
        another request has already rotated.
        """
        result = await self.session.execute(
            self._base_query().execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def lock_for_update(self, connection_id: uuid.UUID) -> EbayConnection | None:
        """Take a row lock so only one refresh runs per connection.

        This is the serialisation point for token refresh. Two concurrent
        requests that both find an expiring token would otherwise both call
        eBay, and the second would store a token the first had already
        superseded — or burn the daily refresh quota for nothing.

        Still tenant-scoped: the predicate comes from ``_base_query``, so a
        caller cannot lock another workspace's row by passing its id.
        """
        result = await self.session.execute(
            self._base_query()
            .where(EbayConnection.id == connection_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    def _translate_integrity_error(self, exc: IntegrityError) -> Exception:
        """Name the two constraints a merchant can actually provoke.

        The base class returns "Another record with these values already
        exists", which is true and useless. These two have specific, actionable
        meanings — and the cross-tenant one must not disclose anything about the
        workspace that holds the account.
        """
        from app.integrations.ebay.exceptions import EbaySellerAlreadyLinkedError

        message = str(getattr(exc, "orig", exc)).lower()
        if "uq_ebay_connections_ebay_user_id" in message:
            return EbaySellerAlreadyLinkedError()
        if "uq_ebay_connections_tenant_id" in message:
            return ConflictError("This workspace already has an eBay account connected.")
        return super()._translate_integrity_error(exc)


class EbayListingDefaultsRepository(TenantScopedRepository[EbayListingDefaults]):
    """A workspace's chosen eBay policies and location, per marketplace.

    Tenant-scoped like every business repository. Erasure across tenants on
    eBay's instruction is not here; it is ``EbayListingDefaultsOwner`` in
    ``app.integrations.ebay.deletion``.
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, EbayListingDefaults)

    async def delete_for_connection(self, connection_id: uuid.UUID) -> int:
        """Physically delete this tenant's defaults for one connection (eBay
        data; no soft delete, see the model). Tenant predicate included."""
        result = await self.session.execute(
            delete(EbayListingDefaults).where(
                EbayListingDefaults.tenant_id == await self._current_tenant_id(),
                EbayListingDefaults.connection_id == connection_id,
            )
        )
        return int(getattr(result, "rowcount", 0) or 0)

    async def get_for_marketplace(self, marketplace_id: str) -> EbayListingDefaults | None:
        result = await self.session.execute(
            self._base_query().where(EbayListingDefaults.marketplace_id == marketplace_id)
        )
        return result.scalar_one_or_none()


class EbayConnectedTenantsSweep:
    """Which workspaces have a usable eBay connection — tenant ids, nothing else.

    **Unscoped by design, and approved for exactly this** (owner, 2026-10-03,
    B-011; see CLAUDE.md §4). The scheduled eBay jobs need to *find* the
    tenants to act on; every action they lead to then runs under that tenant's
    own context through the scoped repositories. Returns ids only, never a
    renderable row, so it cannot leak one workspace's data to another.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def connected_tenant_ids(self, *, limit: int = 5000) -> list[uuid.UUID]:
        result = await self.session.execute(
            select(EbayConnection.tenant_id)
            .where(EbayConnection.status == EbayConnectionStatus.CONNECTED)
            .order_by(EbayConnection.tenant_id)
            .limit(limit)
        )
        return list(result.scalars().all())


__all__ = [
    "EbayComplianceLedgerRepository",
    "EbayConnectedTenantsSweep",
    "EbayConnectionRepository",
    "EbayListingDefaultsRepository",
]
