"""API version 1.

This module deliberately does not re-export ``api_router``. Doing so would make
``app.api.v1`` import ``app.api.v1.router``, which in turn imports the domain
subpackages back out of ``app.api.v1`` — a cycle that only works by accident of
the import system's partial-module fallback. Import the router from its own
module instead::

    from app.api.v1.router import api_router
"""
