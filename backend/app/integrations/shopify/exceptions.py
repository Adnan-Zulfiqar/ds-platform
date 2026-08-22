"""Shopify integration errors."""

from __future__ import annotations

from typing import Any

from app.core.exceptions import (
    AuthenticationError,
    ConflictError,
    ExternalServiceError,
    ValidationError,
)

SERVICE_NAME = "shopify"


class ShopifyError(ExternalServiceError):
    code = "shopify_error"
    message = "The Shopify integration encountered an error."
    retryable: bool = False

    def __init__(
        self,
        message: str | None = None,
        *,
        upstream_code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        merged: dict[str, Any] = {**(details or {})}
        if upstream_code:
            merged["upstream_code"] = upstream_code
        super().__init__(message, service=SERVICE_NAME, details=merged)
        self.upstream_code = upstream_code


class ShopifyTimeoutError(ShopifyError):
    code = "shopify_timeout"
    message = "Shopify did not respond in time."
    retryable = True


class ShopifyRateLimitError(ShopifyError):
    code = "shopify_rate_limited"
    message = "Shopify rate limit exceeded."
    retryable = True


class ShopifyGraphQLError(ShopifyError):
    """Shopify returned a GraphQL response this platform will not act on.

    Separate from :class:`ShopifyResponseError` because the failure modes are
    genuinely different: a REST response error is about HTTP, while this covers
    a transport-level 200 whose *body* says the operation did not succeed.
    Collapsing the two would make "did Shopify do the thing" unanswerable from
    the error type alone.
    """

    code = "shopify_graphql_error"
    message = "Shopify's GraphQL API returned errors."

    def __init__(
        self,
        message: str | None = None,
        *,
        upstream_code: str | None = None,
        details: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> None:
        merged: dict[str, Any] = {**(details or {})}
        if request_id:
            # Shopify's own request id, which is what their support asks for.
            # Safe to surface: it identifies a request, not its contents.
            merged["shopifyRequestId"] = request_id
        super().__init__(message, upstream_code=upstream_code, details=merged)
        self.request_id = request_id


class ShopifyThrottledError(ShopifyGraphQLError):
    """The GraphQL `THROTTLED` code, or an HTTP 429 on the GraphQL endpoint."""

    code = "shopify_graphql_throttled"
    message = "Shopify is throttling this app's GraphQL requests."
    retryable = True


class ShopifyQueryCostError(ShopifyGraphQLError):
    """`MAX_COST_EXCEEDED` — the document is too expensive to ever run.

    Explicitly **not** retryable. The same document will cost the same next
    time, so retrying converts a fixable authoring mistake into a slow outage.
    """

    code = "shopify_graphql_cost_exceeded"
    message = "The Shopify GraphQL query exceeds the maximum permitted cost."
    retryable = False


class ShopifyUserError(ShopifyGraphQLError):
    """A mutation ran and Shopify refused the change via ``userErrors``.

    Transport succeeded. This is the failure that a naive client reports as
    success, which is why it has its own type rather than being folded into a
    generic response error.
    """

    code = "shopify_user_error"
    message = "Shopify rejected the requested change."
    retryable = False


class ShopifyGidError(ValidationError):
    code = "shopify_invalid_gid"
    message = "A Shopify global identifier was missing or malformed."


class ShopifyPaginationError(ShopifyError):
    """Cursor pagination could not be continued safely.

    A missing cursor, a repeated cursor or a page budget exhausted. All three
    mean "stop", never "loop again" — an unbounded catalogue walk against a
    merchant's shop is a self-inflicted outage.
    """

    code = "shopify_pagination_error"
    message = "Shopify pagination could not be continued safely."


class ShopifyResponseError(ShopifyError):
    code = "shopify_response_error"


class ShopifyAuthError(AuthenticationError):
    code = "shopify_auth_error"
    message = "Shopify rejected the credentials for this store."


class ShopifyNotConnectedError(ValidationError):
    code = "shopify_not_connected"
    message = "Shopify is not connected for this workspace."


class ShopifyOAuthStateError(AuthenticationError):
    code = "shopify_oauth_state_invalid"
    message = "The Shopify authorization state is invalid or has expired."


class ShopifyOAuthHmacError(AuthenticationError):
    code = "shopify_oauth_hmac_invalid"
    message = "The Shopify OAuth callback signature is not valid."


class ShopifyOAuthExchangeError(AuthenticationError):
    code = "shopify_oauth_exchange_failed"
    message = "Shopify rejected the authorization code exchange."


class ShopifyConfigError(ValidationError):
    code = "shopify_not_configured"
    message = "Shopify is not configured on this server."


class ShopifyInvalidShopError(ValidationError):
    code = "shopify_invalid_shop"
    message = (
        "Enter the store's *.myshopify.com domain (Shopify Admin -> Settings -> Domains), "
        "not a custom storefront domain."
    )


class ShopifyShopTakenError(ConflictError):
    """This *.myshopify.com domain is already bound to another tenant.

    Cross-tenant installs of the same shop would make webhook routing by
    ``X-Shopify-Shop-Domain`` ambiguous — audit A-01.
    """

    code = "shopify_shop_taken"
    message = "This Shopify shop is already connected to another workspace."


class ShopifyWebhookConfigError(ValidationError):
    """``SHOPIFY_WEBHOOK_CALLBACK_BASE`` does not match a live receiver path."""

    code = "shopify_webhook_base_invalid"
    message = (
        "SHOPIFY_WEBHOOK_CALLBACK_BASE must end with /webhooks, /callback, or /webhook "
        "so registered addresses hit a DropPilot receiver."
    )


class ShopifyInstallTicketError(AuthenticationError):
    code = "shopify_install_ticket_invalid"
    message = "The Shopify install ticket is invalid or has expired."
