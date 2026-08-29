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
from app.core.client_ip import resolve_client_ip
from app.core.config import settings
from app.core.exceptions import AuthenticationError
from app.integrations.email import EmailDeliveryError, send_password_reset_code
from app.integrations.google import (
    GoogleIdentity,
    GoogleNonceStore,
    GoogleTokenError,
    GoogleTokenVerifier,
)
from app.schemas.auth import (
    AuthenticatedIdentity,
    AuthResponse,
    GoogleIdentityRead,
    GoogleLinkRequest,
    GoogleNonceResponse,
    GoogleSignInRequest,
    LoginRequest,
    LogoutRequest,
    PasswordResetChallengeResponse,
    PasswordResetCompleteRequest,
    PasswordResetRequestRequest,
    PasswordResetTicketResponse,
    PasswordResetVerifyRequest,
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
from app.services.google_auth import GoogleAuthService
from app.services.password_reset import PasswordResetService

router = APIRouter(prefix="/auth", tags=["auth"])

# Restricting the cookie to these paths means it is not sent with ordinary API
# requests, which shrinks both the CSRF surface and the chance of it being
# captured by a logging proxy.
_REFRESH_COOKIE_PATH = "/api/v1/auth"


def _client_ip(request: Request) -> str | None:
    """Resolve the caller's IP for login throttling.

    Delegates to ``app.core.client_ip``, which believes a forwarding header only
    when the immediate peer is a configured trusted proxy. The previous version
    trusted ``X-Forwarded-For`` from anyone, which made the IP dimension of the
    login throttle worthless against exactly the attacker it exists to slow
    down: one who can send an extra header per attempt.
    """
    return resolve_client_ip(request)


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


async def _verified_google_identity(credential: str, nonce: str | None) -> GoogleIdentity:
    """Verify a credential, and consume the nonce that came with it.

    One place, used by both sign-in and linking, so the two cannot drift into
    checking different things. Every failure becomes the same 401: telling the
    caller *which* check failed would let them iterate towards a token that
    passes.
    """
    verifier = GoogleTokenVerifier()
    if not verifier.configured:
        raise AuthenticationError("Google sign-in is not available.")

    # Consume first. A credential presented with a nonce that was already spent
    # is a replay, and must fail before anything else is considered.
    if nonce is not None and not await GoogleNonceStore().consume(nonce):
        raise AuthenticationError("This sign-in attempt has expired. Try again.")

    try:
        return await verifier.verify(credential, expected_nonce=nonce)
    except GoogleTokenError as exc:
        raise AuthenticationError("Google sign-in could not be verified.") from exc


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


# ---------------------------------------------------------------------------
# Google sign-in (AUTH-G1)
# ---------------------------------------------------------------------------
#
# The browser gets a signed credential from Google's official button and posts
# it here. Everything the browser says *about* that credential is ignored; only
# what survives `GoogleTokenVerifier` is believed.


@router.post(
    "/google/nonce",
    response_model=GoogleNonceResponse,
    summary="Issue a one-time nonce for a Google sign-in attempt",
)
async def google_nonce() -> GoogleNonceResponse:
    """Mint a nonce the browser passes to Google.

    Google embeds it in the signed credential, so a token minted for a different
    attempt — or captured and replayed later — fails verification here. It is
    stored server-side and consumed on use, which is what makes it one-time
    rather than merely unpredictable.
    """
    nonce = await GoogleNonceStore().issue()
    return GoogleNonceResponse(
        nonce=nonce, expires_in_seconds=settings.google_oauth.nonce_ttl_seconds
    )


@router.post(
    "/google",
    response_model=AuthResponse,
    summary="Sign in or sign up with Google",
)
async def google_sign_in(
    payload: GoogleSignInRequest, session: DbSession, response: Response
) -> AuthResponse:
    """Exchange a verified Google credential for a DropPilot session.

    A first-time Google user gets a tenant, an owner role and a session — the
    same registration path a password signup takes, minus the password.

    An address that already has a **local** account is refused with a specific,
    actionable code rather than linked automatically: an email match proves the
    person controls the address today, not that they are the account holder.
    """
    identity = await _verified_google_identity(payload.credential, payload.nonce)

    result = await GoogleAuthService(session).sign_in(identity, company_name=payload.company_name)

    _set_refresh_cookie(
        response,
        result.auth.tokens.refresh_token,
        settings.security.refresh_token_ttl_days * 86_400,
    )
    if result.created:
        response.status_code = status.HTTP_201_CREATED
    return _build_auth_response(result.auth, include_refresh_in_body=False)


@router.post(
    "/google/link",
    response_model=GoogleIdentityRead,
    status_code=status.HTTP_201_CREATED,
    summary="Link a Google account to the signed-in user",
)
async def link_google(
    payload: GoogleLinkRequest, user: CurrentUser, session: DbSession
) -> GoogleIdentityRead:
    """Attach Google to an existing account.

    Requires an active session, which is the proof of continuity an email match
    cannot give: whoever does this already holds the account.
    """
    identity = await _verified_google_identity(payload.credential, payload.nonce)
    link = await GoogleAuthService(session).link(user_id=user.id, identity=identity)
    return GoogleIdentityRead(
        provider=link.provider,
        provider_email=link.provider_email,
        linked_at=link.created_at,
        last_authenticated_at=link.last_authenticated_at,
    )


@router.delete(
    "/google/link",
    response_model=MessageResponse,
    summary="Disconnect Google from the signed-in user",
)
async def unlink_google(user: CurrentUser, session: DbSession) -> MessageResponse:
    """Detach Google, refusing to leave the account with no way in.

    There is no self-service recovery for an account with neither a password nor
    a provider, so the refusal is the difference between an inconvenience and a
    permanently unreachable workspace.
    """
    removed = await GoogleAuthService(session).unlink(user_id=user.id)
    return MessageResponse(
        message="Google disconnected." if removed else "No Google account was linked."
    )


# ---------------------------------------------------------------------------
# Password reset by one-time code (AUTH-G1)
# ---------------------------------------------------------------------------


@router.post(
    "/password-reset/request",
    response_model=PasswordResetChallengeResponse,
    summary="Request a password-reset code",
)
async def request_password_reset(
    payload: PasswordResetRequestRequest, request: Request, session: DbSession
) -> PasswordResetChallengeResponse:
    """Start a reset.

    **The response is identical for every address** — registered, unregistered,
    password-holding or Google-only. A challenge id is minted either way; one
    for an unknown address simply never verifies. Anything else would make this
    endpoint a list of who has an account here.
    """
    service = PasswordResetService(session)
    challenge, code = await service.request(email=payload.email, client_ip=_client_ip(request))

    if code is not None:
        try:
            await send_password_reset_code(email=payload.email, code=code)
        except EmailDeliveryError:
            # Do not leave a challenge the person cannot possibly satisfy: they
            # would sit waiting for a code that was never sent. The response
            # stays generic — a delivery failure is not their business and
            # reporting it would also confirm the address exists.
            await service.discard(challenge.challenge_id)

    return PasswordResetChallengeResponse(
        challenge_id=challenge.challenge_id,
        expires_in_seconds=challenge.expires_in_seconds,
        message=(
            "If that address has an account, a six-digit code is on its way. "
            "It expires in 10 minutes."
        ),
    )


@router.post(
    "/password-reset/verify",
    response_model=PasswordResetTicketResponse,
    summary="Exchange a code for a reset ticket",
)
async def verify_password_reset(
    payload: PasswordResetVerifyRequest, session: DbSession
) -> PasswordResetTicketResponse:
    """Check the code and hand back a single-use ticket.

    Expiry, a wrong code and an exhausted attempt count are one response, for
    the same reason every login failure is: distinguishing them tells an
    attacker which lever to pull next.
    """
    outcome = await PasswordResetService(session).verify(
        challenge_id=payload.challenge_id, code=payload.code
    )
    if outcome is None:
        raise AuthenticationError("That code is not valid. Request a new one.")
    return PasswordResetTicketResponse(
        reset_ticket=outcome.reset_ticket, expires_in_seconds=outcome.expires_in_seconds
    )


@router.post(
    "/password-reset/complete",
    response_model=MessageResponse,
    summary="Set a new password with a reset ticket",
)
async def complete_password_reset(
    payload: PasswordResetCompleteRequest, session: DbSession
) -> MessageResponse:
    """Spend the ticket, set the password, and end every existing session.

    Revoking sessions is the point of the last step rather than a nicety: a
    reset usually means the account may have been compromised, and leaving the
    intruder's refresh token alive would make the reset cosmetic.
    """
    user_id = await PasswordResetService(session).complete(
        reset_ticket=payload.reset_ticket, new_password=payload.new_password
    )
    if user_id is None:
        raise AuthenticationError("That reset link is no longer valid. Start again.")

    await AuthService(session).logout_all_sessions(user_id)
    return MessageResponse(message="Password updated. Sign in with your new password.")
