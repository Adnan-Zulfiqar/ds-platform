"""Shopify connection and store listing repositories."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import require_tenant_id
from app.core.exceptions import ShopifyWebhookReconcileBusyError
from app.models.shopify import ShopifyConnection, StoreListing
from app.repositories.base import BaseRepository, TenantScopedRepository

#: PostgreSQL SQLSTATE for ``lock_timeout`` expiring on a row lock.
_LOCK_NOT_AVAILABLE = "55P03"


def _is_lock_timeout(exc: DBAPIError) -> bool:
    """Whether this driver error is PostgreSQL refusing to keep waiting.

    Matched on SQLSTATE rather than on the message, which is localised and has
    changed wording between releases. ``sqlstate`` is asyncpg's spelling and
    ``pgcode`` is psycopg's; both are read so the check survives a driver swap
    instead of silently degrading to "never a lock timeout", which would send
    contention back to the client as a 500.
    """
    original = getattr(exc, "orig", None)
    for attribute in ("sqlstate", "pgcode"):
        if getattr(original, attribute, None) == _LOCK_NOT_AVAILABLE:
            return True
    return False


class ShopifyConnectionRepository(TenantScopedRepository[ShopifyConnection]):
    """Tenant-scoped Shopify connections.

    ``ShopifyConnection`` uses ``IdentifiedBase`` rather than soft-delete, so
    this repository overrides ``_base_query`` to filter by tenant only.
    """

    sortable_fields = frozenset({"created_at", "updated_at", "status", "last_sync_at"})
    searchable_fields = frozenset({"shop_domain"})

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, ShopifyConnection)

    def _base_query(self) -> Any:
        tenant_id = require_tenant_id()
        return select(ShopifyConnection).where(ShopifyConnection.tenant_id == tenant_id)

    async def list_all(self) -> Sequence[ShopifyConnection]:
        result = await self.session.execute(self._base_query())
        return list(result.scalars().all())

    async def get_by_store(self, store_id: uuid.UUID) -> ShopifyConnection | None:
        result = await self.session.execute(
            self._base_query().where(ShopifyConnection.store_id == store_id)
        )
        return result.scalar_one_or_none()

    async def get_by_shop_domain(self, shop_domain: str) -> ShopifyConnection | None:
        result = await self.session.execute(
            self._base_query().where(ShopifyConnection.shop_domain == shop_domain)
        )
        return result.scalar_one_or_none()

    async def lock_for_update(
        self, connection_id: uuid.UUID, *, timeout_ms: int | None = None
    ) -> ShopifyConnection | None:
        """Take this connection's row for the rest of the transaction (GQL-2).

        Webhook reconciliation is list-decide-create, and two of them running at
        once would both see a topic missing and both create it — leaving the
        shop receiving every event twice. Serialising on the connection row is
        enough: it is the row the reconciliation is about, one per store, and
        already tenant-scoped by ``_base_query``.

        ``FOR UPDATE`` rather than an in-process lock, because the racing callers
        are usually two web workers or a worker and a Celery task, and an
        ``asyncio.Lock`` protects neither. ``populate_existing`` forces a fresh
        read: a guard that inspects its session's remembered copy is not a guard.

        ``timeout_ms`` bounds how long this waits (GQL-2 acceptance fix, F-02).
        Without it a merchant clicking *Retry* twice would hold an HTTP worker
        for as long as the first reconciliation took, and a genuinely wedged
        transaction would hold it forever. The wait is expressed as
        PostgreSQL's own ``lock_timeout`` rather than an application timer,
        because only the database can abandon a lock request it has already
        queued.

        Two details make that safe to use on a shared request transaction:

        * ``SET LOCAL`` is scoped to this transaction and reverts on commit or
          rollback, so nothing leaks into a pooled connection;
        * it is reset to ``DEFAULT`` immediately after the lock statement, so
          the bound applies to acquiring *this* row and not to every subsequent
          statement in the request.

        Expiry surfaces as SQLSTATE ``55P03``, translated here into a typed
        retryable domain error. Leaving it as a raw ``DBAPIError`` would reach
        the client as a 500, which is the wrong thing to tell someone whose only
        problem is that the work is already running.
        """
        query = (
            self._base_query()
            .where(ShopifyConnection.id == connection_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if timeout_ms is None:
            result = await self.session.execute(query)
            return result.scalar_one_or_none()

        # PostgreSQL does not accept a bind parameter in SET, so the value is
        # formatted in. `int()` makes it structurally incapable of carrying
        # anything but digits, and the only caller passes a module constant --
        # this is not a path any request value reaches.
        await self.session.execute(text(f"SET LOCAL lock_timeout = '{int(timeout_ms)}ms'"))
        try:
            result = await self.session.execute(query)
        except DBAPIError as exc:
            if _is_lock_timeout(exc):
                # `details` carries the bound and nothing else: the connection's
                # own identifier is of no use to the caller and internal ids do
                # not belong in an error envelope.
                raise ShopifyWebhookReconcileBusyError(details={"timeout_ms": timeout_ms}) from exc
            raise
        finally:
            # Lift the bound as soon as the row is held, so it governs acquiring
            # *this* lock rather than every later statement in the request. On
            # the failure path the transaction is already aborted and this is a
            # no-op, which is why it is tolerated rather than required.
            try:
                await self.session.execute(text("SET LOCAL lock_timeout = DEFAULT"))
            except DBAPIError:  # pragma: no cover - transaction already aborted
                pass
        return result.scalar_one_or_none()


class StoreListingRepository(TenantScopedRepository[StoreListing]):
    sortable_fields = frozenset({"created_at", "updated_at", "last_synced_at"})
    searchable_fields = frozenset({"external_product_id"})

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, StoreListing)

    async def get_for_product(
        self, *, store_id: uuid.UUID, product_id: uuid.UUID
    ) -> StoreListing | None:
        result = await self.session.execute(
            self._base_query().where(
                StoreListing.store_id == store_id,
                StoreListing.product_id == product_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_store(self, store_id: uuid.UUID) -> Sequence[StoreListing]:
        result = await self.session.execute(
            self._base_query().where(StoreListing.store_id == store_id)
        )
        return list(result.scalars().all())

    async def list_for_product(self, product_id: uuid.UUID) -> Sequence[StoreListing]:
        result = await self.session.execute(
            self._base_query().where(StoreListing.product_id == product_id)
        )
        return list(result.scalars().all())


class ShopifyMaintenanceRepository(BaseRepository[ShopifyConnection]):
    """Unscoped sweep / webhook lookup — documented unscoped repository.

    HTTP handlers must never use this class for ordinary CRUD. Celery tasks and
    inbound Shopify webhooks bind tenant context per row after an unscoped
    domain lookup.
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, ShopifyConnection)

    async def list_connected(self, *, limit: int = 500) -> Sequence[ShopifyConnection]:
        from app.models.integration import IntegrationStatus

        query = (
            select(ShopifyConnection)
            .where(ShopifyConnection.status == IntegrationStatus.CONNECTED)
            .limit(limit)
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_by_shop_domain(self, shop_domain: str) -> ShopifyConnection | None:
        """Any status — used to reject connect when another tenant owns the shop."""
        result = await self.session.execute(
            select(ShopifyConnection).where(ShopifyConnection.shop_domain == shop_domain)
        )
        return result.scalar_one_or_none()

    async def get_connected_by_shop_domain(self, shop_domain: str) -> ShopifyConnection | None:
        """Indexed domain lookup for HMAC-verified webhooks — not a table scan."""
        from app.models.integration import IntegrationStatus

        result = await self.session.execute(
            select(ShopifyConnection).where(
                ShopifyConnection.shop_domain == shop_domain,
                ShopifyConnection.status == IntegrationStatus.CONNECTED,
            )
        )
        return result.scalar_one_or_none()
