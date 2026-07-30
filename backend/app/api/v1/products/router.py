"""Product endpoints — reserved.

No endpoints in Phase 0. Products are the platform's highest-volume entity, so
the model and its access patterns are designed alongside the import pipeline
rather than guessed at now.

When implemented, this module owns product listing, retrieval, editing,
variant management, and the AI optimisation actions. Every route will be
tenant-scoped through ``TenantScopedRepository`` exactly as ``users`` is.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/products", tags=["products"])
