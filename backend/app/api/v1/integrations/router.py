"""Integration endpoints.

Thin, like every router here: validate, delegate to the service, shape the
response. No AliExpress logic appears in this file — not the signing scheme, not
the error vocabulary, not the token handling.

**No endpoint in this module returns a credential.** That is guaranteed
structurally rather than by discipline: the response models in
``integrations.aliexpress.schemas`` have no field capable of holding one.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import RedirectResponse

from app.api.deps import CurrentPrincipal, DbSession, RequireAdmin
from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.aliexpress.schemas import (
    AliExpressAuthorizationResponse,
    AliExpressConnectionRead,
    AliExpressConnectRequest,
    AliExpressStatusResponse,
    AliExpressWebhookAckResponse,
)
from app.integrations.aliexpress.service import AliExpressService
from app.integrations.aliexpress.webhook import receive_webhook
from app.models.integration import AliExpressConnection
from app.schemas.common import MessageResponse

logger = get_logger(__name__)

router = APIRouter(prefix="/integrations", tags=["integrations"])


def _to_read_model(connection: AliExpressConnection) -> AliExpressConnectionRead:
    """Project a connection into its public shape.

    The only place a connection crosses the API boundary, and it copies fields
    explicitly rather than validating the ORM object wholesale — so adding an
    encrypted column to the model can never cause it to appear in a response.
    """
    return AliExpressConnectionRead(
        id=connection.id,
        status=connection.status,
        app_key=connection.app_key,
        connected_at=connection.created_at,
        last_sync_at=connection.last_sync_at,
        token_expires_at=connection.token_expiry,
        is_token_expired=connection.is_token_expired,
        last_error=connection.last_error,
    )


@router.post(
    "/aliexpress/connect",
    response_model=AliExpressAuthorizationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Begin an AliExpress connection",
)
async def connect_aliexpress(
    payload: AliExpressConnectRequest,
    session: DbSession,
    principal: RequireAdmin,
) -> AliExpressAuthorizationResponse:
    """Store credentials and return the URL the browser should visit for consent.

    **Restricted to admins and owners.** Connecting a supplier account decides
    where every future order is placed and how much it costs; that is not a
    change a `viewer` or `member` should be able to make.

    A POST rather than the GET named in the phase brief. The call has side
    effects — it writes encrypted credentials and issues a single-use state
    token — and it may accept a secret in its body, which must not travel in a
    URL where it would land in browser history, proxy logs, and the `Referer`
    header. The response carries the authorization URL for the client to
    navigate to.

    The body is normally empty: credentials default to the platform's AliExpress
    application. A tenant running their own application may override both.
    """
    service = AliExpressService(session)

    authorization_url, state = await service.begin_connection(
        app_key=payload.app_key,
        app_secret=payload.app_secret,
        user_id=principal.user_id,
    )

    return AliExpressAuthorizationResponse(
        authorization_url=authorization_url,
        state=state,
        expires_in_seconds=settings.aliexpress.oauth_state_ttl_seconds,
    )


@router.get(
    "/aliexpress/callback",
    summary="OAuth callback from AliExpress",
    response_class=RedirectResponse,
)
async def aliexpress_callback(
    session: DbSession,
    code: Annotated[str | None, Query(description="Authorization code.")] = None,
    state: Annotated[str | None, Query(description="CSRF state token.")] = None,
    error: Annotated[str | None, Query(description="Error from AliExpress.")] = None,
) -> RedirectResponse:
    """Exchange the authorization code and redirect back into the application.

    Redirects rather than returning JSON: the browser arrives here directly from
    AliExpress's consent screen, so the user must end up on a page, not looking
    at a payload.

    **Failures redirect too**, carrying a short reason in the query string. The
    reason is drawn from a fixed vocabulary this application controls — never an
    upstream message, which could be reflected into the page.

    **No Bearer token is required.** AliExpress redirects the browser here; the
    OAuth ``state`` token — issued during an authenticated ``/connect`` call and
    verified server-side — binds the callback to the correct tenant.
    """
    return_url = settings.aliexpress.frontend_return_url

    if error:
        # The user declined consent, or AliExpress rejected the request.
        logger.warning("aliexpress_callback_error", upstream_error=error[:100])
        return RedirectResponse(f"{return_url}?aliexpress=denied", status_code=303)

    if not code or not state:
        logger.warning("aliexpress_callback_missing_parameters")
        return RedirectResponse(f"{return_url}?aliexpress=invalid", status_code=303)

    service = AliExpressService(session)

    try:
        connection = await service.complete_connection(code=code, state_token=state)
    except Exception:
        # Deliberately broad. Whatever went wrong, the user must land back in
        # the application rather than on an error page they cannot act on. The
        # exception is logged with its traceback; the page shows a generic
        # failure and the integrations page reports the stored `last_error`.
        logger.exception("aliexpress_callback_failed")
        return RedirectResponse(f"{return_url}?aliexpress=failed", status_code=303)

    logger.info("aliexpress_callback_succeeded", tenant_id=str(connection.tenant_id))
    return RedirectResponse(f"{return_url}?aliexpress=connected", status_code=303)


@router.post(
    "/aliexpress/webhook",
    response_model=AliExpressWebhookAckResponse,
    status_code=status.HTTP_200_OK,
    summary="Receive AliExpress push notifications",
)
async def aliexpress_webhook(request: Request) -> AliExpressWebhookAckResponse:
    """Accept inbound notifications from AliExpress.

    Separate from the OAuth callback. AliExpress POSTs server-to-server; there
    is no browser and no redirect. The handler logs the payload and returns
    immediately so upstream retries stop.

    **No authentication header is expected.** Signature verification is not
    implemented yet — see ``app.integrations.aliexpress.webhook``.
    """
    return await receive_webhook(request)


@router.get(
    "/aliexpress/status",
    response_model=AliExpressStatusResponse,
    summary="Current AliExpress connection status",
)
async def aliexpress_status(
    session: DbSession, _principal: CurrentPrincipal
) -> AliExpressStatusResponse:
    """Report whether AliExpress is connected, and since when.

    Readable by any authenticated role: knowing whether the integration is
    healthy is operational information every team member needs, even those who
    cannot change it.

    ``connected`` is computed here rather than left for the client to infer from
    the status enum, so every consumer agrees on what it means — a connection
    whose token has expired is *not* connected.
    """
    connection = await AliExpressService(session).get_connection()

    if connection is None:
        return AliExpressStatusResponse(connected=False, connection=None)

    return AliExpressStatusResponse(
        connected=connection.is_usable,
        connection=_to_read_model(connection),
    )


@router.delete(
    "/aliexpress/disconnect",
    response_model=MessageResponse,
    summary="Disconnect AliExpress",
)
async def disconnect_aliexpress(session: DbSession, _principal: RequireAdmin) -> MessageResponse:
    """Remove the connection and its stored credentials.

    Admin or owner only, matching `connect`: disconnecting stops every future
    sync for the whole workspace.

    Idempotent — disconnecting when nothing is connected reports success rather
    than erroring, so a double click cannot produce a confusing failure.
    """
    removed = await AliExpressService(session).disconnect()

    return MessageResponse(
        message=(
            "AliExpress has been disconnected and the stored credentials deleted."
            if removed
            else "No AliExpress connection was present."
        )
    )
