"""SQLAlchemy declarative base and the mixins every table is built from.

Three concerns are factored into mixins so that no table re-implements them:

* :class:`UUIDPrimaryKeyMixin` — UUID identifiers rather than sequential ints.
* :class:`TimestampMixin` — UTC created/updated tracking.
* :class:`SoftDeleteMixin` — logical deletion.
* :class:`TenantMixin` — the multi-tenancy discriminator.

**Why UUIDs.** Sequential integer keys leak business volume (an order id tells a
competitor how many orders exist) and make cross-tenant enumeration trivial. They
also force a round trip to the database before an id exists, which prevents
building an object graph client-side or in a worker. UUIDv4 costs 16 bytes and an
index that is random rather than append-ordered; at the write volumes this
platform targets that trade is worth making, and Postgres handles it well.

**Why soft deletes.** Customers delete products and orders by mistake, support
needs to restore them, and financial records must remain auditable. Physical
deletion is reserved for GDPR erasure requests, which are handled explicitly
rather than through the ordinary delete path.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Index, MetaData, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

# Explicit naming convention for constraints and indexes.
#
# Without this, Alembic autogenerates names that differ between Postgres versions
# and SQLAlchemy releases, producing migrations that cannot be reliably
# downgraded. Naming them deterministically is a prerequisite for trustworthy
# schema migrations over a multi-year project.
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    # Fetch server-generated values with RETURNING as part of the INSERT or
    # UPDATE, rather than leaving the attribute expired for a later lazy load.
    #
    # **This is mandatory in an async codebase, not an optimisation.** Columns
    # with `server_default` or `onupdate` — `created_at` and `updated_at` on
    # every table here — are expired after a flush. Touching one afterwards
    # triggers a lazy refresh, which needs IO, and implicit IO in async
    # SQLAlchemy raises `MissingGreenlet` rather than awaiting.
    #
    # Concretely: without this, updating a row and then serialising it through a
    # response schema crashes. That is exactly the shape of `AuthService.login`,
    # which stamps `last_login_at` and then returns the user.
    #
    # RUF012 wants a ClassVar annotation on a mutable class attribute, but
    # SQLAlchemy declares `__mapper_args__` as an instance variable on
    # `DeclarativeBase`, so annotating it that way is a type error. The lint rule
    # loses to the library's own declaration.
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012

    def __repr__(self) -> str:
        identifier = getattr(self, "id", None)
        return f"<{type(self).__name__} id={identifier}>"

    def to_dict(self, exclude: set[str] | None = None) -> dict[str, Any]:
        """Return a plain dict of column values.

        Intended for logging and debugging. API responses go through Pydantic
        schemas instead, which control exactly which fields are exposed — an ORM
        model dumped straight to JSON is how password hashes end up in responses.
        """
        excluded = exclude or set()
        return {
            column.name: getattr(self, column.name)
            for column in self.__table__.columns
            if column.name not in excluded
        }


class UUIDPrimaryKeyMixin:
    """Adds a UUID primary key generated application-side."""

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        # Generated in Python, not by the database. The identifier therefore
        # exists before the INSERT, which lets a service build related rows and
        # emit domain events without an extra flush.
        default=uuid.uuid4,
        nullable=False,
    )


class TimestampMixin:
    """Adds UTC creation and modification timestamps.

    ``server_default``/``onupdate`` use database-side clocks. Application servers
    drift and may sit in different regions; the database is the single authority
    on time, which matters for ordering and for audit trails.

    Columns are ``TIMESTAMP WITH TIME ZONE``. Postgres stores these as UTC
    instants, so a later region migration cannot silently reinterpret history.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class SoftDeleteMixin:
    """Adds logical deletion.

    ``deleted_at`` is nullable and indexed: ``NULL`` means live. A nullable
    timestamp is preferred over a boolean flag because it records *when* the
    deletion happened at no extra cost.

    Note that the repository layer applies the ``deleted_at IS NULL`` filter.
    This mixin deliberately does not install a global SQLAlchemy event filter —
    implicit query rewriting is very hard to reason about when a query does need
    to see deleted rows, such as during restore or audit.
    """

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
        index=True,
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def mark_deleted(self) -> None:
        self.deleted_at = datetime.now(UTC)

    def restore(self) -> None:
        self.deleted_at = None


class TenantMixin:
    """Adds the tenant discriminator that every business table must carry.

    **Why a shared schema with a tenant column**, rather than schema-per-tenant
    or database-per-tenant: at tens of thousands of tenants, per-schema
    approaches make migrations operationally painful (tens of thousands of DDL
    executions per release) and waste connections. A discriminator column with
    enforced filtering scales to that tenant count on ordinary Postgres.

    The cost is that isolation becomes a property of application code rather
    than of the database. That risk is mitigated in two ways: the tenant filter
    lives in a single base repository that all data access goes through, and
    every tenant-scoped index leads with ``tenant_id`` so that a forgotten
    filter is a performance cliff — visible in testing — rather than silently
    correct-looking.

    ``ondelete="CASCADE"`` matches the deletion contract: when a tenant is
    genuinely purged, their rows go with them.
    """

    @declared_attr
    @classmethod
    def tenant_id(cls) -> Mapped[uuid.UUID]:
        from sqlalchemy import ForeignKey

        return mapped_column(
            PGUUID(as_uuid=True),
            ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )


class IdentifiedBase(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Abstract base for any table with a UUID primary key and timestamps.

    Exists so that the repository layer has a meaningful type bound. ``Base``
    alone declares no columns, so a repository generic over ``Base`` could not
    reference ``id`` without the type checker rejecting it — and loosening that
    to ``Any`` would discard exactly the checking that catches a typo'd column
    name before it reaches production.
    """

    __abstract__ = True


class TenantScopedBase(
    IdentifiedBase,
    SoftDeleteMixin,
    TenantMixin,
):
    """Abstract base for every tenant-owned business table.

    Business models should inherit from this rather than assembling mixins by
    hand — a table that forgets ``TenantMixin`` is a data-isolation breach, and
    making the correct thing the default is the cheapest possible defence.
    """

    __abstract__ = True

    @declared_attr.directive
    @classmethod
    def __table_args__(cls) -> tuple[Any, ...]:
        """Index supporting the access pattern every tenant-scoped query uses.

        Almost every query filters ``tenant_id = ? AND deleted_at IS NULL`` and
        orders by ``created_at``. A composite index in that column order serves
        the filter and the sort together, so Postgres does not need a separate
        sort step.
        """
        return (
            Index(
                f"ix_{cls.__tablename__}_tenant_active",
                "tenant_id",
                "deleted_at",
                "created_at",
            ),
        )


class ReferenceBase(IdentifiedBase):
    """Base for platform-global tables that are not owned by any tenant.

    Currency codes, supported marketplaces, plan definitions. These are readable
    by every tenant and writable only by platform administrators, so they carry
    no ``tenant_id`` and no soft delete.
    """

    __abstract__ = True


__all__ = [
    "NAMING_CONVENTION",
    "Base",
    "IdentifiedBase",
    "ReferenceBase",
    "SoftDeleteMixin",
    "TenantMixin",
    "TenantScopedBase",
    "TimestampMixin",
    "UUIDPrimaryKeyMixin",
]
