"""Authentication endpoints — reserved.

Phase 0 establishes the module and registers the router so that the URL prefix,
the OpenAPI tag, and the import graph are fixed now. No endpoints exist yet:
authentication is a phase of its own, and a half-built login route is worse than
none at all.

When implemented, this module owns:

* ``POST /auth/login`` — exchange credentials for an access and refresh token
* ``POST /auth/refresh`` — rotate a refresh token
* ``POST /auth/logout`` — revoke the current session
* ``POST /auth/register`` — provision a tenant and its owner
* ``POST /auth/password/forgot`` and ``/auth/password/reset``

The single change required elsewhere is the body of ``resolve_tenant`` in
``app.api.deps``, which switches from the development header to the verified
token claim. Every endpoint and repository already depends on that abstraction.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/auth", tags=["auth"])
