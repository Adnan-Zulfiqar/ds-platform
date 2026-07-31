"""Authentication endpoints.

Handlers stay thin: validate, delegate to :class:`AuthService`, shape the
response. No credential comparison, no token minting, and no revocation logic
appears here — all of it lives in the service, so a Celery task or a CLI could
drive the same flows.

**Refresh tokens travel in an httpOnly cookie.** A token in ``localStorage`` is
readable by any script on the page, so a single XSS payload exfiltrates a
long-lived credential. An httpOnly cookie is invisible to JavaScript, and
``SameSite`` limits its use from other origins. The cookie is additionally
scoped by ``path`` to the two endpoints that consume it, so it is not attached
to every ordinary API call.

Non-browser clients that have no cookie jar can send the token in the request
body instead; both endpoints accept either.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from app.api.deps import CurrentPrincipal, CurrentTenant, CurrentUser, DbSession, RoleRepo
from app.core.config import settings
from app.core.exceptions import AuthenticationError
from app.schemas.auth import (
    AuthenticatedIdentity,
    AuthResponse,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    TenantRead,
    TokenResponse,
    VerifyEmailConfirmRequest,
)
from app.schemas.common import MessageResponse
from app.schemas.user import UserRead
from app.services.auth import AuthResult, AuthService
from app.services.email_verification import EmailVerificationService

router = APIRouter(prefix="/auth", tags=["auth"])

# Restricting the cookie to these paths means it is not sent with ordinary API
# requests, which shrinks both the CSRF surface and the chance of it being
# captured by a logging proxy.
_REFRESH_COOKIE_PATH = "/api/v1/auth"


def _client_ip(request: Request) -> str | None:
    """Resolve the caller's IP for login throttling.

    ``X-Forwarded-For`` is trustworthy only because Nginx overwrites it. If this
    service were ever exposed directly, the header would be client-controlled
    and the IP dimension of the throttle worthless.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def _set_refresh_cookie(response: Response, token: str, max_age_seconds: int) -> None:
    response.set_cookie(
        key=settings.security.refresh_cookie_name,
        value=token,
        max_age=max_age_seconds,
        # Unreadable by JavaScript — the whole point.
        httponly=True,
        # Configurable so local HTTP development works; must be true anywhere
        # real, or the cookie is never sent over TLS-only connections.
        secure=settings.security.cookie_secure,
        samesite=settings.security.cookie_samesite,
        domain=settings.security.cookie_domain,
        path=_REFRESH_COOKIE_PATH,
    )


def _clear_refresh_cookie(response: Response) -> None:
    # Attributes must match those used to set it, or the browser treats it as a
    # different cookie and the original survives.
    response.delete_cookie(
        key=settings.security.refresh_cookie_name,
        httponly=True,
        secure=settings.security.cookie_secure,
        samesite=settings.security.cookie_samesite,
        domain=settings.security.cookie_domain,
        path=_REFRESH_COOKIE_PATH,
    )


def _build_auth_response(result: AuthResult, *, include_refresh_in_body: bool) -> AuthResponse:
    """Assemble the response body from a service result.

    ``include_refresh_in_body`` is false for browser clients, where the token is
    already in an httpOnly cookie. Sending it in both places would hand the
    credential back to JavaScript and negate the cookie.
    """
    return AuthResponse(
        identity=AuthenticatedIdentity(
            user=UserRead.model_validate(result.user),
            tenant=TenantRead.model_validate(result.tenant),
            roles=sorted(result.roles),
        ),
        tokens=TokenResponse(
            access_token=result.tokens.access_token,
            expires_in=result.tokens.expires_in_seconds,
            refresh_token=result.tokens.refresh_token if include_refresh_in_body else None,
            access_expires_at=result.tokens.access_expires_at,
            refresh_expires_at=result.tokens.refresh_expires_at,
        ),
    )


@router.post(
    "/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a tenant and its first user",
)
async def register(
    payload: RegisterRequest, session: DbSession, response: Response
) -> AuthResponse:
    """Register a new company account.

    Creates the tenant, its first user, and the owner role assignment in one
    transaction, then signs the user in so they are not asked to type the
    password they just chose.
    """
    result = await AuthService(session).register(
        company_name=payload.company_name,
        email=payload.email,
        password=payload.password.get_secret_value(),
        first_name=payload.first_name,
        last_name=payload.last_name,
    )

    _set_refresh_cookie(
        response,
        result.tokens.refresh_token,
        settings.security.refresh_token_ttl_days * 86_400,
    )
    return _build_auth_response(result, include_refresh_in_body=False)


@router.post("/login", response_model=AuthResponse, summary="Sign in")
async def login(
    payload: LoginRequest, request: Request, session: DbSession, response: Response
) -> AuthResponse:
    """Exchange credentials for tokens.

    Every failure mode — unknown address, wrong password, disabled account,
    suspended tenant — returns the same 401 with the same message. Anything
    else turns this endpoint into an account enumeration oracle.
    """
    result = await AuthService(session).login(
        email=payload.email,
        password=payload.password.get_secret_value(),
        client_ip=_client_ip(request),
    )

    _set_refresh_cookie(
        response,
        result.tokens.refresh_token,
        settings.security.refresh_token_ttl_days * 86_400,
    )
    return _build_auth_response(result, include_refresh_in_body=False)


@router.post("/refresh", response_model=AuthResponse, summary="Rotate tokens")
async def refresh(
    payload: RefreshRequest, request: Request, session: DbSession, response: Response
) -> AuthResponse:
    """Exchange a refresh token for a new pair.

    The presented token is consumed. Presenting an already-consumed token is
    treated as theft and terminates every session for that user — see
    ``AuthService.refresh``.
    """
    # Cookie first: a browser client always has one, and preferring it means a
    # stale value in a request body cannot override the live session.
    token = request.cookies.get(settings.security.refresh_cookie_name) or payload.refresh_token
    from_cookie = bool(request.cookies.get(settings.security.refresh_cookie_name))

    if not token:
        raise AuthenticationError("A refresh token is required.")

    result = await AuthService(session).refresh(token)

    if from_cookie:
        _set_refresh_cookie(
            response,
            result.tokens.refresh_token,
            settings.security.refresh_token_ttl_days * 86_400,
        )

    # A caller that sent the token in the body has no cookie jar, so the new
    # token must come back in the body or their session ends here.
    return _build_auth_response(result, include_refresh_in_body=not from_cookie)


@router.post("/logout", response_model=MessageResponse, summary="Sign out")
async def logout(
    payload: LogoutRequest, request: Request, session: DbSession, response: Response
) -> MessageResponse:
    """Revoke the current refresh token.

    Requires no access token. A user whose access token has already expired
    must still be able to end their session — and the refresh token itself is
    the credential being revoked, so it is sufficient proof of ownership.

    Always reports success. Signing out must appear to work regardless of token
    state; a user who sees an error and assumes they are still signed in is a
    worse outcome than a no-op.
    """
    token = request.cookies.get(settings.security.refresh_cookie_name) or payload.refresh_token

    await AuthService(session).logout(token)
    _clear_refresh_cookie(response)

    return MessageResponse(message="Signed out successfully.")


@router.get(
    "/me",
    response_model=AuthenticatedIdentity,
    summary="Current user, tenant, and roles",
)
async def me(
    user: CurrentUser,
    tenant: CurrentTenant,
    principal: CurrentPrincipal,
    roles: RoleRepo,
) -> AuthenticatedIdentity:
    """Return the authenticated identity.

    Roles are re-read from the database rather than taken from the token
    claims. This endpoint exists to tell a client the current truth — a UI that
    hides an admin control should hide it as soon as the role is revoked, not
    up to fifteen minutes later when the access token expires.
    """
    role_names = await roles.list_role_names_for_user(principal.user_id)

    return AuthenticatedIdentity(
        user=UserRead.model_validate(user),
        tenant=TenantRead.model_validate(tenant),
        roles=sorted(role_names),
    )


@router.post(
    "/logout-all",
    response_model=MessageResponse,
    summary="Sign out of every session",
)
async def logout_all(
    principal: CurrentPrincipal, session: DbSession, response: Response
) -> MessageResponse:
    """Revoke every refresh token for the current user.

    Requires a valid access token, unlike ``/logout``: this affects sessions on
    other devices, so it needs proof of current identity rather than possession
    of one refresh token.
    """
    count = await AuthService(session).logout_all_sessions(principal.user_id)
    _clear_refresh_cookie(response)
    return MessageResponse(message=f"Signed out of {count} session(s).")


@router.post(
    "/verify-email/request",
    response_model=MessageResponse,
    summary="Issue an email verification token",
)
async def request_email_verification(user: CurrentUser, session: DbSession) -> MessageResponse:
    """Send (or log) a verification token for the signed-in user.

    The raw token is never returned in the HTTP response — only the mailer
    (currently a logging backend) receives it. Callers must not treat this as
    proof that mail was delivered.
    """
    await EmailVerificationService(session).request_for_user(user)
    return MessageResponse(
        message="If verification is required, a message has been prepared for your account."
    )


@router.post(
    "/verify-email/confirm",
    response_model=MessageResponse,
    summary="Confirm email verification",
)
async def confirm_email_verification(
    payload: VerifyEmailConfirmRequest,
    user: CurrentUser,
    session: DbSession,
) -> MessageResponse:
    """Consume a verification token for the authenticated user."""
    await EmailVerificationService(session).confirm(
        raw_token=payload.token,
        user=user,
    )
    return MessageResponse(message="Email address verified.")
