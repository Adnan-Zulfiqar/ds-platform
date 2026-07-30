"""Pure utility functions.

Everything here must be free of side effects and free of dependencies on other
application layers. A helper that needs a database session or configuration
belongs in a service, not here — that boundary is what keeps this package
trivially testable and safe to import from anywhere.
"""

from app.utils.strings import slugify, truncate

__all__ = ["slugify", "truncate"]
