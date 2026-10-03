"""Integration endpoints.

Thin, like every router here: validate, delegate to the service, shape the
response. No AliExpress logic appears in this file — not the signing scheme, not
the error vocabulary, not the token handling.

**No endpoint in this module returns a credential.** That is guaranteed
structurally rather than by discipline: the response models in
``integrations.aliexpress.schemas`` have no field capable of holding one.
"""

from __future__ import annotations

import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import RedirectResponse

from app.api.deps import (
    CurrentPrincipal,
    DbSession,
    OptionalPrincipal,
    RequireAdmin,
    RequireMember,
)
from app.core.config import settings
from app.core.logging import get_logger
from app.core.request_body import read_bounded_body
from app.integrations.aliexpress.schemas import (
    AliExpressAuthorizationResponse,
    AliExpressConnectionRead,
    AliExpressStatusResponse,
    AliExpressWebhookAckResponse,
)
from app.integrations.aliexpress.service import AliExpressService
from app.integrations.aliexpress.webhook import receive_webhook
from app.integrations.ebay.compliance import (
    MAX_NOTIFICATION_BODY_BYTES,
    EbayComplianceService,
    accepts_content_type,
    challenge_response,
    parse_notification,
)
from app.integrations.ebay.connection import EbayConnectionService
from app.integrations.ebay.exceptions import (
    EbayNotificationRejectedError,
    EbayOAuthStateError,
    EbaySellerAlreadyLinkedError,
)
from app.integrations.ebay.listing_setup import (
    EbayListingSetupService,
    ListingDefaultsChoice,
)
from app.integrations.ebay.schemas import (
    ChallengeResponse,
    EbayAuthorizationResponse,
    EbayConnectionRead,
    EbayListingDefaultsRead,
    EbayListingDefaultsUpdate,
    EbayListingSetupResponse,
    EbayLocationCreate,
    EbayLocationRead,
    EbayPolicyRead,
    EbayStatusResponse,
)
from app.integrations.ebay.seller_setup import (
    EBAY_SUPPORTED_MARKETPLACES,
    EbayInventoryLocation,
    EbayPolicy,
    NewInventoryLocation,
)
from app.integrations.ebay.signature import SIGNATURE_HEADER
from app.integrations.shopify.schemas import (
    ShopifyAuthorizationResponse,
    ShopifyClaimInstallRequest,
    ShopifyConnectionRead,
    ShopifyConnectRequest,
    ShopifyPublishCheckItem,
    ShopifyPublishReadinessRequest,
    ShopifyPublishReadinessResponse,
    ShopifyPublishRequest,
    ShopifyPublishResponse,
    ShopifyStatusResponse,
    ShopifySyncRequest,
    ShopifyWebhookAckResponse,
    ShopifyWebhookReconcileResponse,
    ShopifyWebhookTopicResult,
)
from app.integrations.shopify.service import (
    ShopifyService,
    append_frontend_query,
    webhook_health,
)
from app.integrations.shopify.sync import ShopifySyncService
from app.integrations.shopify.webhook import receive_shopify_webhook
from app.integrations.shopify.webhook_reconciliation import ReconcileReport
from app.models.ebay import EbayConnection, EbayListingDefaults
from app.models.integration import AliExpressConnection
from app.models.role import RoleName
from app.models.shopify import ShopifyConnection
from app.schemas.common import MessageResponse
from app.services.publish_readiness import CHANNEL_SHOPIFY, PublishReadinessService

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
        webhook_health=webhook_health(connection).value,
    )


@router.get(
    "/shopify/install",
    summary="Shopify App URL install entry (HMAC-verified)",
    response_class=RedirectResponse,
)
async def shopify_install(
    request: Request,
    session: DbSession,
    principal: OptionalPrincipal,
) -> RedirectResponse:
    """App Store / install-link entry — shop comes from Shopify, not a form.

    Partner Dashboard **App URL** must point here. Merchants never supply API
    keys. Anonymous installs receive a claim ticket on the frontend; signed-in
    admins continue straight to Shopify authorize.
    """
    return_url = settings.shopify.frontend_return_url
    tenant_id = None
    user_id = None
    if principal is not None:
        held_ranks: list[int] = []
        for name in principal.roles:
            try:
                held_ranks.append(RoleName(name).rank)
            except ValueError:
                continue
        if held_ranks and max(held_ranks) >= RoleName.ADMIN.rank:
            tenant_id = principal.tenant_id
            user_id = principal.user_id
    try:
        redirect_to = await ShopifyService(session).begin_app_url_install(
            query_string=str(request.url.query),
            tenant_id=tenant_id,
            user_id=user_id,
        )
    except Exception as exc:
        from app.core.exceptions import ValidationError
        from app.integrations.shopify.exceptions import (
            ShopifyInvalidShopError,
            ShopifyOAuthHmacError,
            ShopifyShopTakenError,
        )

        reason = "failed"
        if isinstance(exc, ShopifyOAuthHmacError):
            reason = "hmac"
        elif isinstance(exc, ShopifyInvalidShopError):
            reason = "invalid_shop"
        elif isinstance(exc, ShopifyShopTakenError):
            reason = "taken"
        elif isinstance(exc, ValidationError):
            reason = "invalid"
        logger.exception(
            "shopify_install_failed",
            reason=reason,
            error_type=type(exc).__name__,
            shop=request.query_params.get("shop"),
        )
        return RedirectResponse(
            append_frontend_query(return_url, shopify=reason),
            status_code=303,
        )
    return RedirectResponse(redirect_to, status_code=303)


@router.post(
    "/shopify/claim-install",
    response_model=ShopifyAuthorizationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Claim a HMAC-verified App URL install for this workspace",
)
async def claim_shopify_install(
    payload: ShopifyClaimInstallRequest,
    session: DbSession,
    principal: RequireAdmin,
) -> ShopifyAuthorizationResponse:
    authorization_url, state = await ShopifyService(session).claim_install(
        install_token=payload.install_token,
        store_name=payload.store_name,
        user_id=principal.user_id,
    )
    return ShopifyAuthorizationResponse(
        authorization_url=authorization_url,
        state=state,
        expires_in_seconds=settings.shopify.oauth_state_ttl_seconds,
    )


@router.post(
    "/shopify/connect",
    response_model=ShopifyAuthorizationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Begin a Shopify OAuth install from a typed store domain",
)
async def connect_shopify(
    payload: ShopifyConnectRequest,
    session: DbSession,
    principal: RequireAdmin,
) -> ShopifyAuthorizationResponse:
    """Connect button path — merchant enters ``*.myshopify.com`` only."""
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
        return RedirectResponse(
            append_frontend_query(return_url, shopify="denied"),
            status_code=303,
        )
    webhooks_healthy = False
    try:
        connection = await ShopifyService(session).complete_connection(
            query_string=str(request.url.query),
        )
        # Webhook registration stays best-effort: a valid access token must not
        # be thrown away because Shopify was briefly unreachable. What changed
        # in the acceptance fix is that the *result* is no longer discarded.
        # Reporting a plain success here is what let a store sit in the UI as
        # "Connected" while every product, inventory and order subscription was
        # missing, with the reconnect control hidden because the status said
        # connected.
        try:
            report = await ShopifyService(session).register_webhooks(connection.store_id)
            webhooks_healthy = report.healthy
            if not webhooks_healthy:
                logger.warning(
                    "shopify_webhook_registration_degraded",
                    store_id=str(connection.store_id),
                    tenant_id=str(connection.tenant_id),
                    created=report.created_count,
                    warnings=len(report.warnings),
                    statuses=[f"{i.topic}={i.status.value}" for i in report.items],
                )
        except Exception:
            # Tenant-safe context only: identifiers and the exception type,
            # never the shop token or a provider payload.
            logger.exception(
                "shopify_webhook_registration_failed",
                store_id=str(connection.store_id),
                tenant_id=str(connection.tenant_id),
            )
    except Exception as exc:
        from app.integrations.shopify.exceptions import (
            ShopifyOAuthExchangeError,
            ShopifyOAuthHmacError,
            ShopifyOAuthStateError,
            ShopifyShopTakenError,
        )

        reason = "failed"
        if isinstance(exc, ShopifyOAuthHmacError):
            reason = "hmac"
        elif isinstance(exc, ShopifyOAuthStateError):
            reason = "state"
        elif isinstance(exc, ShopifyOAuthExchangeError):
            reason = "exchange"
        elif isinstance(exc, ShopifyShopTakenError):
            reason = "taken"
        logger.exception(
            "shopify_callback_failed",
            reason=reason,
            error_type=type(exc).__name__,
            shop=request.query_params.get("shop"),
        )
        return RedirectResponse(
            append_frontend_query(return_url, shopify=reason),
            status_code=303,
        )
    # Two stable, enumerable status codes, no secret and no raw error text. The
    # degraded one is deliberately a distinct value rather than an extra flag on
    # "connected", so a frontend that does not recognise it cannot fall back to
    # rendering a full success.
    return RedirectResponse(
        append_frontend_query(
            return_url,
            shopify="connected" if webhooks_healthy else "connected_webhooks_degraded",
        ),
        status_code=303,
    )


# ---------------------------------------------------------------------------
# eBay Marketplace Account Deletion/Closure (EBAY-C0)
#
# Both routes are **public by protocol design**: eBay is not an authenticated
# user of this API and cannot present a JWT. Neither depends on a principal.
# What replaces authentication is not nothing:
#
#   * GET  proves endpoint ownership through a secret only eBay and this server
#          share, and returns a digest rather than any stored data;
#   * POST proves origin cryptographically, against eBay's own published key,
#          over the exact bytes received, before a single field is read.
#
# Registered with no trailing slash, matching the URL in the eBay developer
# portal exactly. FastAPI is not asked to redirect between the two spellings:
# a redirect would change the URL eBay actually reached, and the configured
# endpoint string is part of the challenge hash.
# ---------------------------------------------------------------------------


@router.get(
    "/ebay/marketplace-account-deletion",
    response_model=ChallengeResponse,
    response_model_by_alias=True,
    summary="eBay marketplace account deletion endpoint validation challenge",
)
async def ebay_marketplace_account_deletion_challenge(
    challenge_code: Annotated[str | None, Query(alias="challenge_code")] = None,
) -> ChallengeResponse:
    """Answer eBay's challenge with ``SHA256(code + token + endpoint)``.

    The alias is exactly ``challenge_code`` because that is the parameter eBay
    sends; the Python name is snake_case for the same reason every other
    parameter here is.

    Returns 200 with ``application/json`` — both required by eBay's guide, and
    both produced by FastAPI's real JSON encoder rather than a hand-built
    string, which is what avoids the byte order mark the guide warns about.
    """
    return challenge_response(challenge_code)


@router.post(
    "/ebay/marketplace-account-deletion",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Receive an eBay marketplace account deletion notification",
)
async def ebay_marketplace_account_deletion_notification(
    request: Request,
    session: DbSession,
) -> Response:
    """Verify, record and process one account-deletion notification.

    Order is the security property here: **verify, parse, claim, erase,
    acknowledge**. Nothing is parsed until the signature is proven over the raw
    bytes, and nothing is acknowledged until the erasure and the ledger row have
    been committed together.

    204 on success — one of the four codes eBay accepts, and the honest one:
    there is no representation to return. A signature or schema failure is 412,
    matching what eBay's own SDKs return. A transient failure anywhere else
    propagates as a 5xx so eBay resends; it retries for 24 hours, and losing a
    deletion request is far worse than delaying one.
    """
    # Streamed and bounded, never `await request.body()`. On an
    # unauthenticated route that call lets anyone who knows the URL decide how
    # much memory the process allocates, because the size is only measured once
    # the whole body has already arrived.
    raw = await read_bounded_body(request, max_bytes=MAX_NOTIFICATION_BODY_BYTES)

    if not accepts_content_type(request.headers.get("content-type")):
        raise EbayNotificationRejectedError(details={"reason": "unsupported_content_type"})

    service = EbayComplianceService(session)
    # Signature first, over the bytes exactly as received. Parsing before
    # verifying would mean acting on the shape of an unauthenticated payload.
    await service.verify(
        raw_body=raw,
        signature_header=request.headers.get(SIGNATURE_HEADER),
    )

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EbayNotificationRejectedError(details={"reason": "malformed_json"}) from exc

    notification = parse_notification(payload)
    await service.process(notification=notification)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# eBay seller connection (EBAY-C1)
#
# Shaped like the AliExpress endpoints rather than inventing a second style:
# status is readable by any authenticated role, mutation is admin-only, and the
# callback is public because eBay redirects a browser to it with no session.
# ---------------------------------------------------------------------------


def _ebay_to_read(connection: EbayConnection) -> EbayConnectionRead:
    return EbayConnectionRead(
        id=connection.id,
        status=connection.status,
        environment=connection.environment,
        ebay_username=connection.ebay_username,
        marketplace_id=connection.marketplace_id,
        account_type=connection.account_type,
        scopes=connection.granted_scopes.split() if connection.granted_scopes else [],
        connected_at=connection.connected_at,
        last_verified_at=connection.last_verified_at,
        access_token_expires_at=connection.access_token_expires_at,
        needs_reconnect=connection.needs_reconnect,
        reconnect_reason=connection.reconnect_reason,
        last_error=connection.last_error,
    )


@router.get(
    "/ebay/status",
    response_model=EbayStatusResponse,
    summary="Current eBay seller connection status",
)
async def ebay_status(session: DbSession, _principal: CurrentPrincipal) -> EbayStatusResponse:
    """Report whether eBay is configured, connected, and healthy.

    Readable by every authenticated role, matching the other integrations:
    knowing whether a sales channel is working is operational information the
    whole team needs, even members who cannot change it.

    ``configured`` is reported separately from ``connected`` so the card can
    tell "this server has no eBay credentials" apart from "nobody has connected
    yet" — two situations with completely different remedies.
    """
    connection = await EbayConnectionService(session).get_connection()
    return EbayStatusResponse(
        configured=settings.ebay.is_oauth_configured,
        connected=connection is not None and connection.is_usable,
        connection=_ebay_to_read(connection) if connection else None,
    )


@router.post(
    "/ebay/connect",
    response_model=EbayAuthorizationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Begin an eBay seller connection",
)
async def connect_ebay(
    session: DbSession,
    principal: RequireAdmin,
) -> EbayAuthorizationResponse:
    """Return the eBay consent URL for this workspace.

    **Admin or owner only.** Connecting a selling channel decides where this
    workspace's listings and orders go; that is not a change a viewer or member
    should be able to make.

    The same endpoint serves reconnection. eBay's consent flow is identical in
    both cases, and a separate "reconnect" route would be the same code behind a
    different name — with two places to keep the scope list correct.
    """
    url, state = await EbayConnectionService(session).begin_connection(user_id=principal.user_id)
    return EbayAuthorizationResponse(
        authorization_url=url,
        state=state,
        expires_in_seconds=settings.ebay.oauth_state_ttl_seconds,
    )


@router.get(
    "/ebay/callback",
    summary="OAuth callback from eBay",
    response_class=RedirectResponse,
)
async def ebay_callback(
    session: DbSession,
    code: Annotated[str | None, Query(description="Authorization code.")] = None,
    state: Annotated[str | None, Query(description="CSRF state token.")] = None,
    error: Annotated[str | None, Query(description="Error from eBay.")] = None,
    error_description: Annotated[str | None, Query()] = None,
) -> RedirectResponse:
    """Complete consent and send the seller back into the application.

    **No bearer token is required, and none would help.** eBay redirects the
    browser here directly. The ``state`` token — issued during an authenticated
    admin request and validated server-side — is what binds this callback to a
    workspace. Nothing in the query string is treated as authority: the tenant,
    the user and the environment all come from the stored state record.

    Redirects rather than returning JSON, because the seller arrives from eBay's
    consent screen and must land on a page. Failures redirect too, carrying a
    short reason drawn from a fixed vocabulary this application controls — never
    an upstream message, which could be reflected into the page.

    The full query string is never logged: it carries the authorization code.
    """
    return_url = settings.ebay.frontend_return_url

    if error:
        # The seller pressed "Not now", or eBay refused the request. Logged as a
        # bounded code, not the upstream description.
        logger.info("ebay_callback_declined", upstream_error=error[:64])
        return RedirectResponse(f"{return_url}?ebay=denied", status_code=303)

    if not code or not state:
        logger.warning("ebay_callback_missing_parameters")
        return RedirectResponse(f"{return_url}?ebay=invalid", status_code=303)

    service = EbayConnectionService(session)
    try:
        connection = await service.complete_connection(code=code, state_token=state)
    except EbayOAuthStateError:
        logger.warning("ebay_callback_state_invalid")
        return RedirectResponse(f"{return_url}?ebay=invalid", status_code=303)
    except EbaySellerAlreadyLinkedError:
        logger.warning("ebay_callback_seller_already_linked")
        return RedirectResponse(f"{return_url}?ebay=already_linked", status_code=303)
    except Exception:
        # Deliberately broad. Whatever failed, the seller must land back in the
        # application rather than on an error page they cannot act on. The
        # traceback goes to the log; the page shows a generic failure.
        logger.exception("ebay_callback_failed")
        return RedirectResponse(f"{return_url}?ebay=failed", status_code=303)

    logger.info("ebay_callback_succeeded", tenant_id=str(connection.tenant_id))
    return RedirectResponse(f"{return_url}?ebay=connected", status_code=303)


@router.delete(
    "/ebay/disconnect",
    response_model=MessageResponse,
    summary="Disconnect eBay",
)
async def disconnect_ebay(session: DbSession, _principal: RequireAdmin) -> MessageResponse:
    """Remove the connection and its stored credentials.

    Admin or owner only, matching connect. Idempotent: disconnecting when
    nothing is connected reports success rather than erroring, so a double click
    cannot produce a confusing failure.

    Local credentials are destroyed. The grant itself lives on eBay and is not
    revoked from here — see ``EbayConnectionService.disconnect`` and the eBay
    integration guide for why, and for what to tell a merchant who wants it gone
    at eBay too.
    """
    removed = await EbayConnectionService(session).disconnect()
    return MessageResponse(
        message=(
            "eBay has been disconnected and the stored credentials deleted."
            if removed
            else "No eBay connection was present."
        )
    )


# ---------------------------------------------------------------------------
# EBAY-C2: listing setup — the seller's policies and locations, and the
# defaults this workspace lists with. Reads for members, writes for admins.
# docs/ebay/EBAY_C2_LISTING_SETUP.md
# ---------------------------------------------------------------------------


def _policies_read(policies: tuple[EbayPolicy, ...]) -> list[EbayPolicyRead]:
    return [EbayPolicyRead(id=p.id, name=p.name) for p in policies]


def _location_read(location: EbayInventoryLocation) -> EbayLocationRead:
    return EbayLocationRead(
        key=location.key,
        name=location.name,
        city=location.city,
        postal_code=location.postal_code,
        country=location.country,
        enabled=location.enabled,
    )


def _defaults_read(defaults: EbayListingDefaults) -> EbayListingDefaultsRead:
    return EbayListingDefaultsRead(
        marketplace_id=defaults.marketplace_id,
        fulfillment_policy_id=defaults.fulfillment_policy_id,
        payment_policy_id=defaults.payment_policy_id,
        return_policy_id=defaults.return_policy_id,
        merchant_location_key=defaults.merchant_location_key,
        updated_at=defaults.updated_at,
    )


@router.get(
    "/ebay/listing-setup",
    response_model=EbayListingSetupResponse,
    summary="The seller's eBay policies, locations and saved listing defaults",
)
async def ebay_listing_setup(
    session: DbSession,
    _principal: RequireMember,
    marketplace_id: Annotated[str, Query(alias="marketplaceId", max_length=32)] = "EBAY_US",
) -> EbayListingSetupResponse:
    """Read live from eBay on every call; only the chosen defaults are stored."""
    setup = await EbayListingSetupService(session).get_setup(marketplace_id)
    policies = setup.policies
    return EbayListingSetupResponse(
        marketplace_id=setup.marketplace_id,
        supported_marketplaces=list(EBAY_SUPPORTED_MARKETPLACES),
        business_policies_enabled=setup.business_policies_enabled,
        fulfillment_policies=_policies_read(policies.fulfillment) if policies else None,
        payment_policies=_policies_read(policies.payment) if policies else None,
        return_policies=_policies_read(policies.returns) if policies else None,
        locations=[_location_read(loc) for loc in setup.locations],
        defaults=_defaults_read(setup.defaults) if setup.defaults else None,
    )


@router.put(
    "/ebay/listing-defaults",
    response_model=EbayListingDefaultsRead,
    summary="Choose the policies and location eBay listings will use",
)
async def save_ebay_listing_defaults(
    body: EbayListingDefaultsUpdate, session: DbSession, _principal: RequireAdmin
) -> EbayListingDefaultsRead:
    """Each id is checked against the seller's current eBay objects first."""
    saved = await EbayListingSetupService(session).save_defaults(
        ListingDefaultsChoice(
            marketplace_id=body.marketplace_id,
            fulfillment_policy_id=body.fulfillment_policy_id,
            payment_policy_id=body.payment_policy_id,
            return_policy_id=body.return_policy_id,
            merchant_location_key=body.merchant_location_key,
        )
    )
    return _defaults_read(saved)


@router.post(
    "/ebay/locations",
    response_model=EbayLocationRead,
    status_code=201,
    summary="Create a warehouse location on the seller's eBay account",
)
async def create_ebay_location(
    body: EbayLocationCreate, session: DbSession, _principal: RequireAdmin
) -> EbayLocationRead:
    """Admin only: this writes to the seller's eBay account."""
    created = await EbayListingSetupService(session).create_location(
        NewInventoryLocation(
            name=body.name,
            address_line1=body.address_line1,
            city=body.city,
            state_or_province=body.state_or_province,
            postal_code=body.postal_code,
            country=body.country,
        )
    )
    return _location_read(created)


@router.post(
    "/shopify/callback",
    response_model=ShopifyWebhookAckResponse,
    summary="Receive Shopify webhooks (shared callback URL)",
)
async def shopify_webhook_via_callback(request: Request) -> ShopifyWebhookAckResponse:
    """Accept webhooks on the OAuth callback path when the tunnel only forwards that URL.

    GET remains OAuth. POST is HMAC-verified webhook delivery; topic comes from
    ``X-Shopify-Topic``.
    """
    topic = (request.headers.get("x-shopify-topic") or "").strip().replace("/", "-")
    return await receive_shopify_webhook(request, topic=topic or "unknown")


@router.post(
    "/shopify/webhook",
    response_model=ShopifyWebhookAckResponse,
    summary="Receive Shopify webhooks (singular shared base)",
)
async def shopify_webhook_singular(request: Request) -> ShopifyWebhookAckResponse:
    """Shared receiver when ``SHOPIFY_WEBHOOK_CALLBACK_BASE`` ends with ``/webhook``."""
    topic = (request.headers.get("x-shopify-topic") or "").strip().replace("/", "-")
    return await receive_shopify_webhook(request, topic=topic or "unknown")


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


def _reconcile_response(
    *,
    store_id: UUID,
    report: ReconcileReport,
    connection: ShopifyConnection | None,
) -> ShopifyWebhookReconcileResponse:
    """Project a reconciliation into its public shape.

    Copies fields explicitly, like ``_to_read_model`` above, so the response
    cannot be widened by accident into carrying a token or a provider payload.
    """
    return ShopifyWebhookReconcileResponse(
        store_id=store_id,
        healthy=report.healthy,
        webhook_health=(webhook_health(connection).value if connection is not None else "degraded"),
        topics=[
            ShopifyWebhookTopicResult(
                topic=item.topic,
                status=item.status.value,
                webhook_gid=item.webhook_gid,
                detail=item.detail,
            )
            for item in report.items
        ],
        warnings=list(report.warnings),
        listed_count=report.listed_count,
        created_count=report.created_count,
        webhooks_registered_at=(
            connection.webhooks_registered_at if connection is not None else None
        ),
    )


@router.post(
    "/shopify/stores/{store_id}/webhooks/reconcile",
    response_model=ShopifyWebhookReconcileResponse,
    summary="Retry Shopify webhook registration for a connected store",
)
async def reconcile_shopify_webhooks(
    store_id: UUID,
    session: DbSession,
    _principal: RequireAdmin,
) -> ShopifyWebhookReconcileResponse:
    """Deterministic recovery for a connected-but-degraded store.

    **Why this route and not** ``/shopify/webhooks/reconcile``. That path sits
    under the same prefix as ``POST /shopify/webhooks/{topic}``, the
    unauthenticated HMAC-verified receiver, and would be matched as a topic
    named "reconcile" the moment declaration order changed. Store-scoped
    mutations in this module already live under ``/shopify/stores/{store_id}``,
    which is where ``disconnect`` is, so this follows the established shape and
    cannot collide with merchant traffic.

    Admin-only, and the store id is resolved through the tenant-scoped
    repository, so a foreign or unknown id produces the same "not connected"
    answer and never confirms that somebody else's store exists.

    Delegates to ``ShopifyService.register_webhooks`` — the *same* reconciler
    OAuth uses. There is no second implementation and no queue: it lists every
    page first, creates only what is genuinely missing, never replays a mutation
    whose outcome is unknown, and stamps ``webhooks_registered_at`` only when
    the whole report is healthy.

    Returns 200 with an explicit verdict for a degraded outcome rather than an
    error status: the request was handled correctly, and the useful thing to
    show a merchant is *what* is still missing.
    """
    service = ShopifyService(session)
    report = await service.register_webhooks(store_id)
    connection = await service.connections.get_by_store(store_id)
    return _reconcile_response(store_id=store_id, report=report, connection=connection)


@router.post(
    "/shopify/publish-readiness",
    response_model=ShopifyPublishReadinessResponse,
    summary="Evaluate Shopify publish blockers for a draft",
)
async def shopify_publish_readiness(
    payload: ShopifyPublishReadinessRequest,
    session: DbSession,
    _principal: RequireAdmin,
) -> ShopifyPublishReadinessResponse:
    """Server-authoritative publish check used by Review & publish.

    Same rule set as ``POST /shopify/publish`` — recommendations never set
    ``canPublish`` to false. Foreign draft/store ids return the established
    non-disclosing 404. Response never includes tokens or provider secrets.
    """
    result = await PublishReadinessService(session).evaluate(
        channel=CHANNEL_SHOPIFY,
        product_id=payload.product_id,
        store_id=payload.store_id,
        expected_updated_at=payload.expected_updated_at,
        enforce_version=False,
    )
    return ShopifyPublishReadinessResponse(
        channel=result.channel,
        store_id=result.store_id,
        draft_id=result.draft_id,
        draft_updated_at=result.draft_updated_at,
        can_publish=result.can_publish,
        blockers=[
            ShopifyPublishCheckItem(
                code=item.code,
                message=item.message,
                field=item.field,
                section=item.section,
                action=item.action,
            )
            for item in result.blockers
        ],
        recommendations=[
            ShopifyPublishCheckItem(
                code=item.code,
                message=item.message,
                field=item.field,
                section=item.section,
                action=item.action,
            )
            for item in result.recommendations
        ],
        checked_at=result.checked_at,
    )


@router.post(
    "/shopify/publish",
    response_model=ShopifyPublishResponse,
    summary="Publish a product to Shopify",
)
async def publish_to_shopify(
    payload: ShopifyPublishRequest,
    session: DbSession,
    _principal: RequireAdmin,
) -> ShopifyPublishResponse:
    result = await ShopifySyncService(session).publish_product(
        store_id=payload.store_id,
        product_id=payload.product_id,
        expected_updated_at=payload.expected_updated_at,
        replace_ai_content=payload.replace_ai_content,
    )
    return ShopifyPublishResponse.from_result(result)


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
