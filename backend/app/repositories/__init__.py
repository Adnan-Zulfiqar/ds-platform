"""Repository layer — the only place SQL is constructed.

See ``app.repositories.base`` for the tenant-isolation guarantees that every
tenant-owned repository inherits.

Two repositories are deliberately unscoped, each documented in its own module:
``TenantRepository`` (the tenants table sits above the tenancy boundary) and
``AuthenticationUserRepository`` (login must find a user before a tenant is
known).
"""

from app.repositories.base import BaseRepository, TenantScopedRepository
from app.repositories.integration import (
    AliExpressConnectionRepository,
    IntegrationMaintenanceRepository,
)
from app.repositories.refresh_token import (
    RefreshTokenRepository,
    generate_token_secret,
    hash_token,
)
from app.repositories.role import RoleRepository
from app.repositories.tenant import TenantRepository
from app.repositories.user import AuthenticationUserRepository, UserRepository, normalise_email

__all__ = [
    "AliExpressConnectionRepository",
    "AuthenticationUserRepository",
    "BaseRepository",
    "IntegrationMaintenanceRepository",
    "RefreshTokenRepository",
    "RoleRepository",
    "TenantRepository",
    "TenantScopedRepository",
    "UserRepository",
    "generate_token_secret",
    "hash_token",
    "normalise_email",
]
