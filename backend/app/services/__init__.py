"""Service layer — business logic.

Services orchestrate repositories and enforce rules. They raise domain
exceptions from ``app.core.exceptions`` and never import from ``app.api``.
"""

from app.services.base import BaseService

__all__ = ["BaseService"]
