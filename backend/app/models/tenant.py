"""Tenant model — the root of the multi-tenancy hierarchy.

A tenant is the billable account: one company, agency, or individual seller. All
business data hangs off a tenant, and the tenant is the unit of isolation, of
subscription, and of data residency.

Phase 0 defines only the fields needed to establish and enforce isolation.
Subscription, plan limits, and billing state are deliberately absent — they
belong to the billing phase, and inventing their shape now would guarantee a
migration later.
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import Boolean, CheckConstraint, Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import IdentifiedBase, SoftDeleteMixin


class TenantStatus(StrEnum):
    """Lifecycle state of a tenant account.

    Stored as a native Postgres enum. The alternative — a free-text column —
    permits typos that silently disable a paying customer's access.
    """

    TRIAL = "trial"
    ACTIVE = "active"
    SUSPENDED = "suspended"  # non-payment or abuse; data retained, access denied
    CANCELLED = "cancelled"  # customer left; retained for the grace period


class Tenant(IdentifiedBase, SoftDeleteMixin):
    """A customer account.

    Note this inherits ``Base`` directly rather than ``TenantScopedBase``: the
    tenants table is the top of the hierarchy and cannot carry a ``tenant_id``
    pointing at itself.
    """

    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Subdomain label used for tenant resolution, e.g. "acme" in
    # acme.droppilot.ai. Unique across the platform and immutable in practice —
    # changing it breaks every bookmark and webhook the customer has configured.
    slug: Mapped[str] = mapped_column(
        String(63),  # RFC 1035 limit on a single DNS label
        nullable=False,
        unique=True,
        index=True,
    )

    status: Mapped[TenantStatus] = mapped_column(
        Enum(TenantStatus, name="tenant_status", native_enum=True, validate_strings=True),
        nullable=False,
        default=TenantStatus.TRIAL,
        index=True,
    )

    # Denormalised access switch. Checked on every authenticated request, so it
    # is kept as a plain indexed boolean rather than derived from `status` at
    # query time.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true", index=True
    )

    # IANA timezone name. Reports and scheduled jobs run in the customer's local
    # day, while all stored timestamps remain UTC.
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default="UTC", server_default="UTC"
    )

    # ISO 4217 currency for display and reporting.
    default_currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="USD", server_default="USD"
    )

    __table_args__ = (
        CheckConstraint(
            "slug ~ '^[a-z0-9]([a-z0-9-]*[a-z0-9])?$'",
            name="slug_is_valid_dns_label",
        ),
        CheckConstraint("char_length(default_currency) = 3", name="currency_is_iso4217"),
    )

    def __repr__(self) -> str:
        return f"<Tenant id={self.id} slug={self.slug!r} status={self.status}>"
