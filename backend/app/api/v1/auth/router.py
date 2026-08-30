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
from app.core.exceptions import AuthenticationError, PermissionDeniedError
from app.integrations.email import EmailDeliveryError, send_password_reset_code
from app.integrations.google import (
    GoogleIdentity,
    GoogleIntent,
    GoogleNonceStore,
    GoogleTokenError,
    GoogleTokenVerifier,
)
from app.models.user import User
from app.schemas.auth import (
    AuthenticatedIdentity,
    AuthResponse,
    GoogleIdentityRead,
    GoogleLinkRequest,
    GoogleLoginRequest,
    GoogleNonceRequest,
    GoogleNonceResponse,
    GoogleSignupRequest,
    GoogleUnlinkRequest,
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
from app.services.auth import AuthResult, AuthService, LegalAcceptance
from app.services.email_verification import EmailVerificationService
from app.services.google_auth import GoogleAuthService
from app.services.login_throttle import StepUpThrottle
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


async def _verified_google_identity(
    credential: str, nonce: str, *, intent: GoogleIntent
) -> tuple[GoogleIdentity, object]:
    """Verify a credential against a nonce issued for exactly this operation.

    One place, used by all three Google endpoints, so they cannot drift into
    checking different things.

    The nonce is consumed **first**. A credential presented with a nonce that
    was already spent, never issued, or issued for a different operation is a
    replay or a redirect, and must fail before anything else is considered.
    Every failure becomes the same 401: naming the failed check would let a
    caller iterate towards one that passes.
    """
    verifier = GoogleTokenVerifier()
    if not verifier.configured:
        raise AuthenticationError("Google sign-in is not available.")

    record = await GoogleNonceStore().consume(nonce, expected=intent)
    if record is None:
        raise AuthenticationError("This sign-in attempt has expired. Start again.")

    try:
        identity = await verifier.verify(credential, expected_nonce=nonce)
    except GoogleTokenError as exc:
        raise AuthenticationError("Google sign-in could not be verified.") from exc

    return identity, record


async def _require_step_up(user: User, password: str, *, client_ip: str | None) -> None:
    """Prove the person at the keyboard is the account holder, not just a session.

    Linking or unlinking a sign-in method changes *how the account can be
    entered*, so a long-lived access token is not enough authority — a stolen
    one would otherwise be enough to attach an attacker's Google account
    silently and keep access after the password is changed.

    Password verification is the step-up rather than a client-supplied
    `recent=true` flag or a token-age check, because both of those are asserted
    by the caller and neither proves anything.

    **The limit is reserved here, before the hash is checked**, so it cannot be
    bypassed by alternating between linking and unlinking and it cannot be
    outrun by firing requests in parallel. Both callers go through this one
    function precisely so neither can be written without it; see
    `StepUpThrottle` for what the counters are keyed on and why Redis being
    unavailable refuses rather than waves the operation through.
    """
    from app.core.password import verify_password

    throttle = StepUpThrottle()
    await throttle.reserve(user_id=user.id, tenant_id=user.tenant_id, client_ip=client_ip)

    hashed = user.password_hash
    if hashed is None:
        # Nothing to step up with. Better an explicit refusal than a sensitive
        # operation guarded by a session alone. The attempt still counts: a
        # passwordless account must not be a free probe.
        raise PermissionDeniedError("Set a password before changing how you sign in.")
    if not verify_password(password, hashed):
        raise AuthenticationError("That password is not correct.")

    # Correct. Someone who mistypes once and then succeeds should not carry that
    # failure toward a lockout for the rest of the window.
    #
    # Only their own counter. The shared address budget is deliberately left
    # alone: clearing it would let anybody with one ordinary account wipe the
    # failed attempts of every other account behind the same address.
    await throttle.clear_user_attempts(user_id=user.id, tenant_id=user.tenant_id)


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
        acceptance=LegalAcceptance(
            terms_accepted=payload.terms_accepted,
            privacy_accepted=payload.privacy_accepted,
            terms_version=payload.terms_version,
            privacy_version=payload.privacy_version,
        ),
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
# Google sign-in (AUTH-G1, hardened in AUTH-G1-R1)
# ---------------------------------------------------------------------------
#
# Three explicit operations. The single `/google` endpoint that decided between
# signing in and registering has been removed: a request meant as a sign-in
# could silently create a workspace, and a signup could silently succeed as a
# login and so reveal that an account existed.


@router.post(
    "/google/nonce",
    response_model=GoogleNonceResponse,
    summary="Issue a one-time nonce for a Google login or signup",
)
async def google_nonce(payload: GoogleNonceRequest) -> GoogleNonceResponse:
    """Mint a nonce bound to one unauthenticated operation.

    Only `login` and `signup` are available here. A `link` nonce is issued by
    the authenticated endpoint below, because it has to record *whose* account
    the credential may be attached to.
    """
    intent = GoogleIntent(payload.intent)
    nonce = await GoogleNonceStore().issue(intent)
    return GoogleNonceResponse(
        nonce=nonce, expires_in_seconds=settings.google_oauth.nonce_ttl_seconds
    )


@router.post(
    "/google/link/nonce",
    response_model=GoogleNonceResponse,
    summary="Issue a link nonce bound to the signed-in user",
)
async def google_link_nonce(user: CurrentUser, tenant: CurrentTenant) -> GoogleNonceResponse:
    """A nonce that can only be used to link, and only to this account.

    Bound server-side to the user and tenant from the session. The later
    request cannot claim a different one, because the stored record decides.
    """
    nonce = await GoogleNonceStore().issue(GoogleIntent.LINK, user_id=user.id, tenant_id=tenant.id)
    return GoogleNonceResponse(
        nonce=nonce, expires_in_seconds=settings.google_oauth.nonce_ttl_seconds
    )


@router.post("/google/login", response_model=AuthResponse, summary="Sign in with Google")
async def google_login(
    payload: GoogleLoginRequest, session: DbSession, response: Response
) -> AuthResponse:
    """Authenticate an already-linked Google account.

    **Creates nothing** — no user, no tenant, no identity. An unknown Google
    account is refused with the same message whether or not a local account
    shares the address.
    """
    identity, _ = await _verified_google_identity(
        payload.credential, payload.nonce, intent=GoogleIntent.LOGIN
    )
    result = await GoogleAuthService(session).login(identity)

    _set_refresh_cookie(
        response,
        result.tokens.refresh_token,
        settings.security.refresh_token_ttl_days * 86_400,
    )
    return _build_auth_response(result, include_refresh_in_body=False)


@router.post(
    "/google/signup",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account with Google",
)
async def google_signup(
    payload: GoogleSignupRequest, session: DbSession, response: Response
) -> AuthResponse:
    """Register a new workspace, with the same acceptance a password signup needs.

    Signing up with Google is still signing up: the Terms and Privacy Notice
    have to be accepted here too, and the backend rather than a checkbox is what
    enforces it.
    """
    identity, _ = await _verified_google_identity(
        payload.credential, payload.nonce, intent=GoogleIntent.SIGNUP
    )
    result = await GoogleAuthService(session).signup(
        identity,
        acceptance=LegalAcceptance(
            terms_accepted=payload.terms_accepted,
            privacy_accepted=payload.privacy_accepted,
            terms_version=payload.terms_version,
            privacy_version=payload.privacy_version,
        ),
        company_name=payload.company_name,
    )

    _set_refresh_cookie(
        response,
        result.tokens.refresh_token,
        settings.security.refresh_token_ttl_days * 86_400,
    )
    return _build_auth_response(result, include_refresh_in_body=False)


@router.post(
    "/google/link",
    response_model=GoogleIdentityRead,
    status_code=status.HTTP_201_CREATED,
    summary="Link a Google account to the signed-in user",
)
async def link_google(
    payload: GoogleLinkRequest, user: CurrentUser, request: Request, session: DbSession
) -> GoogleIdentityRead:
    """Attach Google to an existing account, behind a step-up.

    Two independent checks: the password proves the account holder is present,
    and the nonce proves this credential was obtained for *this* account.
    """
    await _require_step_up(user, payload.password.get_secret_value(), client_ip=_client_ip(request))

    identity, record = await _verified_google_identity(
        payload.credential, payload.nonce, intent=GoogleIntent.LINK
    )
    # The nonce was bound to a user at issue time. If it names somebody else,
    # this credential is being redirected onto another account.
    if getattr(record, "user_id", None) != user.id:
        raise AuthenticationError("This sign-in attempt has expired. Start again.")

    link = await GoogleAuthService(session).link(user_id=user.id, identity=identity)
    return GoogleIdentityRead(
        provider=link.provider,
        provider_email=link.provider_email,
        linked_at=link.created_at,
        last_authenticated_at=link.last_authenticated_at,
    )


@router.post(
    "/google/unlink",
    response_model=MessageResponse,
    summary="Disconnect Google from the signed-in user",
)
async def unlink_google(
    payload: GoogleUnlinkRequest, user: CurrentUser, request: Request, session: DbSession
) -> MessageResponse:
    """Detach Google, behind the same step-up, refusing to lock the account out.

    A `POST` rather than a `DELETE` because it carries a body: the step-up
    password has no business in a query string or a URL.

    The same throttle counter as linking, deliberately. Two counters would be
    two sets of guesses for anyone willing to alternate between them.
    """
    await _require_step_up(user, payload.password.get_secret_value(), client_ip=_client_ip(request))

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

    Contention is the one thing that is *not* folded into that response.
    `PasswordResetBusyError` travels on its own as a 503, because the attempt
    was never evaluated: reporting it as a bad code would spend a guess the
    person did not make. It still says nothing about the code itself.
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
