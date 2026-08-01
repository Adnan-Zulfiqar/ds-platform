"""Shopify integration errors."""

from __future__ import annotations

from typing import Any

from app.core.exceptions import AuthenticationError, ExternalServiceError, ValidationError

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
