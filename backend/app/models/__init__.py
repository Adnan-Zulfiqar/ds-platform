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
from app.models.integration import AliExpressConnection, IntegrationStatus
from app.models.product import (
    ImportStatus,
    Product,
    ProductImage,
    ProductImport,
    ProductSource,
    ProductStatus,
    ProductVariant,
)
from app.models.refresh_token import RefreshToken
from app.models.role import Role, RoleName, UserRole
from app.models.tenant import Tenant, TenantStatus
from app.models.user import User

__all__ = [
    "AliExpressConnection",
    "Base",
    "IdentifiedBase",
    "ImportStatus",
    "IntegrationStatus",
    "Product",
    "ProductImage",
    "ProductImport",
    "ProductSource",
    "ProductStatus",
    "ProductVariant",
    "ReferenceBase",
    "RefreshToken",
    "Role",
    "RoleName",
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
