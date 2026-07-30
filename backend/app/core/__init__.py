"""Cross-cutting concerns: configuration, logging, context, exceptions.

This package must not import from ``app.api``, ``app.services``, or
``app.repositories``. Everything else depends on core, so any import in the
other direction creates a cycle.
"""

from app.core.config import Environment, Settings, get_settings, settings
from app.core.logging import configure_logging, get_logger

__all__ = [
    "Environment",
    "Settings",
    "configure_logging",
    "get_logger",
    "get_settings",
    "settings",
]
