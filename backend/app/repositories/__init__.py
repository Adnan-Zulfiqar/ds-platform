"""Repository layer — the only place SQL is constructed.

See ``app.repositories.base`` for the tenant-isolation guarantees that every
tenant-owned repository inherits.
"""

from app.repositories.base import BaseRepository, TenantScopedRepository
from app.repositories.tenant import TenantRepository
from app.repositories.user import UserRepository

__all__ = [
    "BaseRepository",
    "TenantRepository",
    "TenantScopedRepository",
    "UserRepository",
]
