"""Platform operator endpoints (Track E5, decision D-015).

Every route here is behind :func:`require_platform_network`, which answers
404 unless ``PLATFORM_ADMIN_ALLOWED_CIDRS`` is set and the caller is inside
it. Every route except sign-in also requires a platform token. Tenant tokens
are refused by audience, so no tenant role ever reaches these handlers.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.deps import DbSession, PlatformNetwork, RequirePlatformAdmin, endpoint_rate_limit
from app.core.client_ip import resolve_client_ip
from app.schemas.platform_admin import (
    PlatformAdminRead,
    PlatformLoginRequest,
    PlatformLoginResponse,
)
from app.services.platform_admin import PlatformAdminService

router = APIRouter(prefix="/platform", tags=["platform"], dependencies=[PlatformNetwork])

#: Counted per client address (there is no principal yet). Tight: a real
#: operator signs in a few times a day.
_login_limit = endpoint_rate_limit("platform-login", limit=10, window_seconds=900)


@router.post(
    "/auth/login",
    response_model=PlatformLoginResponse,
    summary="Platform operator sign-in (password + one-time code)",
    dependencies=[Depends(_login_limit)],
)
async def platform_login(
    payload: PlatformLoginRequest, request: Request, session: DbSession
) -> PlatformLoginResponse:
    issued = await PlatformAdminService(session).authenticate(
        email=payload.email,
        password=payload.password.get_secret_value(),
        code=payload.code,
        client_ip=resolve_client_ip(request),
    )
    return PlatformLoginResponse(access_token=issued.token, expires_at=issued.expires_at)


@router.get("/me", response_model=PlatformAdminRead, summary="The signed-in operator")
async def platform_me(admin: RequirePlatformAdmin) -> PlatformAdminRead:
    return PlatformAdminRead.model_validate(admin)
