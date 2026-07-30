"""Presentation layer — HTTP routing, dependency wiring, error translation.

This package may import from services, repositories, schemas and core. Nothing
in those layers may import from here: the domain must not know that HTTP exists.
"""

from app.api.error_handlers import register_exception_handlers

__all__ = ["register_exception_handlers"]
