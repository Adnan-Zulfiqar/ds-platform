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
from uuid import UUID

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import RedirectResponse

from app.api.deps import CurrentPrincipal, DbSession, RequireAdmin
from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.aliexpress.schemas import (
    AliExpressAuthorizationResponse,
    AliExpressConnectionRead,
    AliExpressStatusResponse,
    AliExpressWebhookAckResponse,
)
from app.integrations.aliexpress.service import AliExpressService
from app.integrations.aliexpress.webhook import receive_webhook
from app.integrations.shopify.schemas import (
    ShopifyAuthorizationResponse,
    ShopifyConnectionRead,
    ShopifyConnectRequest,
    ShopifyPublishRequest,
    ShopifyStatusResponse,
    ShopifySyncRequest,
    ShopifyWebhookAckResponse,
)
from app.integrations.shopify.service import ShopifyService
from app.integrations.shopify.sync import ShopifySyncService
from app.integrations.shopify.webhook import receive_shopify_webhook
from app.models.integration import AliExpressConnection
from app.models.shopify import ShopifyConnection
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
    session: DbSession,
    principal: RequireAdmin,
) -> AliExpressAuthorizationResponse:
    """Begin OAuth using platform AliExpress credentials; return the consent URL.

    **Restricted to admins and owners.** Connecting a supplier account decides
    where every future order is placed and how much it costs; that is not a
    change a `viewer` or `member` should be able to make.

    Merchants never send an app key or secret. DropPilot's ``ALIEXPRESS_APP_KEY``
    / ``ALIEXPRESS_APP_SECRET`` sign the flow; after consent, only the seller's
    access/refresh tokens are encrypted on the tenant connection row.
    """
    service = AliExpressService(session)

    authorization_url, state = await service.begin_connection(
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


def _shopify_to_read(connection: ShopifyConnection) -> ShopifyConnectionRead:
    return ShopifyConnectionRead(
        id=connection.id,
        store_id=connection.store_id,
        shop_domain=connection.shop_domain,
        status=connection.status.value,
        scopes=connection.scopes,
        connected_at=connection.created_at,
        last_sync_at=connection.last_sync_at,
        last_error=connection.last_error,
        webhooks_registered_at=connection.webhooks_registered_at,
    )


@router.post(
    "/shopify/connect",
    response_model=ShopifyAuthorizationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Begin a Shopify OAuth install",
)
async def connect_shopify(
    payload: ShopifyConnectRequest,
    session: DbSession,
    principal: RequireAdmin,
) -> ShopifyAuthorizationResponse:
    authorization_url, state = await ShopifyService(session).begin_connection(
        shop=payload.shop,
        store_name=payload.store_name,
        user_id=principal.user_id,
    )
    return ShopifyAuthorizationResponse(
        authorization_url=authorization_url,
        state=state,
        expires_in_seconds=settings.shopify.oauth_state_ttl_seconds,
    )


@router.get(
    "/shopify/callback",
    summary="OAuth callback from Shopify",
    response_class=RedirectResponse,
)
async def shopify_callback(request: Request, session: DbSession) -> RedirectResponse:
    return_url = settings.shopify.frontend_return_url
    if request.query_params.get("error"):
        # Shopify may return error / error_description — log names only.
        logger.warning(
            "shopify_oauth_denied",
            error=request.query_params.get("error"),
            shop=request.query_params.get("shop"),
        )
        return RedirectResponse(f"{return_url}?shopify=denied", status_code=303)
    try:
        connection = await ShopifyService(session).complete_connection(
            query_string=str(request.url.query),
        )
        # Best-effort webhook registration — failure must not undo OAuth.
        try:
            await ShopifyService(session).register_webhooks(connection.store_id)
        except Exception:
            logger.exception("shopify_webhook_registration_failed")
    except Exception as exc:
        from app.integrations.shopify.exceptions import (
            ShopifyOAuthExchangeError,
            ShopifyOAuthHmacError,
            ShopifyOAuthStateError,
        )

        reason = "failed"
        if isinstance(exc, ShopifyOAuthHmacError):
            reason = "hmac"
        elif isinstance(exc, ShopifyOAuthStateError):
            reason = "state"
        elif isinstance(exc, ShopifyOAuthExchangeError):
            reason = "exchange"
        logger.exception(
            "shopify_callback_failed",
            reason=reason,
            error_type=type(exc).__name__,
            shop=request.query_params.get("shop"),
        )
        return RedirectResponse(f"{return_url}?shopify={reason}", status_code=303)
    return RedirectResponse(f"{return_url}?shopify=connected", status_code=303)


@router.get(
    "/shopify/status",
    response_model=ShopifyStatusResponse,
    summary="Shopify connection status",
)
async def shopify_status(session: DbSession, _principal: CurrentPrincipal) -> ShopifyStatusResponse:
    configured, connections = await ShopifyService(session).list_status()
    return ShopifyStatusResponse(
        configured=configured,
        connections=[_shopify_to_read(row) for row in connections],
    )


@router.delete(
    "/shopify/stores/{store_id}",
    response_model=MessageResponse,
    summary="Disconnect a Shopify store",
)
async def disconnect_shopify(
    store_id: UUID, session: DbSession, _principal: RequireAdmin
) -> MessageResponse:
    await ShopifyService(session).disconnect(store_id=store_id)
    return MessageResponse(message="Shopify store disconnected and credentials deleted.")


@router.post(
    "/shopify/publish",
    response_model=MessageResponse,
    summary="Publish a product to Shopify",
)
async def publish_to_shopify(
    payload: ShopifyPublishRequest,
    session: DbSession,
    _principal: RequireAdmin,
) -> MessageResponse:
    result = await ShopifySyncService(session).publish_product(
        store_id=payload.store_id,
        product_id=payload.product_id,
    )
    return MessageResponse(
        message=f"Published to Shopify product {result.get('external_product_id')}."
    )


@router.post(
    "/shopify/sync/orders",
    response_model=MessageResponse,
    summary="Import Shopify orders for a store",
)
async def sync_shopify_orders(
    payload: ShopifySyncRequest,
    session: DbSession,
    _principal: RequireAdmin,
) -> MessageResponse:
    if payload.store_id is None:
        from app.core.exceptions import ValidationError

        raise ValidationError("storeId is required.")
    result = await ShopifySyncService(session).import_orders(store_id=payload.store_id)
    return MessageResponse(
        message=(
            f"Imported Shopify orders: created={result['created']} updated={result['updated']}."
        )
    )


@router.post(
    "/shopify/webhooks/{topic}",
    response_model=ShopifyWebhookAckResponse,
    summary="Receive Shopify webhooks",
)
async def shopify_webhook(topic: str, request: Request) -> ShopifyWebhookAckResponse:
    return await receive_shopify_webhook(request, topic=topic)
