"""ORM models.

Every model must be imported here. Alembic's autogenerate walks
``Base.metadata``, which is only populated as a side effect of importing the
module that defines the model — a model that is never imported is silently
omitted from migrations.
"""

from app.models.base import (
    Base,
    IdentifiedBase,
    ReferenceBase,
    SoftDeleteMixin,
    TenantMixin,
    TenantScopedBase,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)
from app.models.tenant import Tenant, TenantStatus
from app.models.user import User, UserRole

__all__ = [
    "Base",
    "IdentifiedBase",
    "ReferenceBase",
    "SoftDeleteMixin",
    "Tenant",
    "TenantMixin",
    "TenantScopedBase",
    "TenantStatus",
    "TimestampMixin",
    "UUIDPrimaryKeyMixin",
    "User",
    "UserRole",
]
