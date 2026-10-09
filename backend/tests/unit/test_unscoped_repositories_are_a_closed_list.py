"""CLAUDE.md §4: unscoped data access is a short, closed list.

Every repository that extends ``BaseRepository`` without the tenant filter is
named here. Adding one fails this test until it is added both here and to
CLAUDE.md §4 with explicit owner approval. The ids-only lookup classes on the
same list do not extend ``BaseRepository``, so they are reviewed by hand.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Iterator

import pytest

import app.repositories as repositories
from app.repositories.base import BaseRepository, TenantScopedRepository

pytestmark = pytest.mark.unit

APPROVED_UNSCOPED = frozenset(
    {
        # Request path, above the tenancy boundary.
        "TenantRepository",
        "AuthenticationUserRepository",
        # Platform reference data.
        "RoleRepository",
        "PromptRepository",
        # D-019: the feature switches and their platform-wide defaults.
        "FeatureFlagRepository",
        # Cross-tenant maintenance before any tenant context.
        "ShopifyMaintenanceRepository",
        "IntegrationMaintenanceRepository",
        "EbayComplianceLedgerRepository",
        # Track E5 (D-015): platform operators sit above every tenant.
        "PlatformAdminRepository",
    }
)


def _subclasses(cls: type) -> Iterator[type]:
    for sub in cls.__subclasses__():
        yield sub
        yield from _subclasses(sub)


def test_no_repository_escapes_tenant_scoping_without_approval() -> None:
    for module in pkgutil.iter_modules(repositories.__path__):
        importlib.import_module(f"{repositories.__name__}.{module.name}")
    unscoped = {
        cls.__name__
        for cls in _subclasses(BaseRepository)
        if not issubclass(cls, TenantScopedRepository)
    }
    assert unscoped == APPROVED_UNSCOPED
