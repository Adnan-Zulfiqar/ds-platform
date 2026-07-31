"""AliExpress request and response shapes.

Two groups, kept separate on purpose:

* **Wire models** — what AliExpress sends. Mirrors their field names, including
  the ones that are not our style. Renaming at the boundary is what stops their
  naming leaking into the rest of the codebase.
* **API models** — what this platform returns to its own clients. **No secret
  ever appears in one of these**, which is the structural reason a credential
  cannot leak through the API: there is no field for it.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.integration import IntegrationStatus
from app.schemas.base import CamelCaseModel


class AliExpressTokenResponse(BaseModel):
    """Token payload from the OAuth exchange.

    ``extra="allow"`` because AliExpress returns additional fields that vary by
    account type and API generation. Rejecting unknown keys here would break the
    integration the first time they add one — the opposite of the strictness
    applied to our own request schemas, where an unexpected field is a client
    error worth surfacing.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    access_token: str
    refresh_token: str | None = None

    # Seconds until expiry. AliExpress has used both `expires_in` and
    # `expire_time` across API generations, so both are accepted and normalised
    # by the auth module rather than by every caller.
    expires_in: int | None = None
    expire_time: int | None = None

    refresh_token_valid_time: int | None = None

    # Identifies the authorised seller account. Useful for support — it answers
    # "which AliExpress account is this connected to" without decrypting a token.
    user_id: str | None = None
    account: str | None = None
    account_platform: str | None = None


class AliExpressErrorResponse(BaseModel):
    """Documented error payload.

    AliExpress signals failure inside a 200 response as often as through a
    status code, so the client inspects the body regardless of status. Field
    names vary between gateways, hence the aliases.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    code: str | None = None
    message: str | None = Field(default=None, alias="msg")
    request_id: str | None = None
    sub_code: str | None = None
    sub_message: str | None = Field(default=None, alias="sub_msg")

    @property
    def is_error(self) -> bool:
        """Whether this payload represents a failure.

        AliExpress returns ``code: "0"`` for success on some endpoints and omits
        the field entirely on others, so only a present, non-zero code counts.
        """
        return bool(self.code) and self.code not in {"0", "0000"}


# ---------------------------------------------------------------------------
# What this platform returns to its own clients
# ---------------------------------------------------------------------------


class AliExpressConnectionRead(CamelCaseModel):
    """Connection state as shown in the UI.

    **Contains no credential material.** Not the app secret, not the tokens, not
    even a masked token — only ``app_key``, which is a public identifier that
    travels in every request URL anyway.

    This is the structural guarantee: a credential cannot leak through this
    endpoint because the response model has nowhere to put one.
    """

    id: uuid.UUID
    status: IntegrationStatus
    app_key: str
    connected_at: datetime | None = Field(
        default=None, description="When the connection was first established."
    )
    last_sync_at: datetime | None = None
    token_expires_at: datetime | None = None
    is_token_expired: bool
    last_error: str | None = None


class AliExpressStatusResponse(CamelCaseModel):
    """Response for the status endpoint.

    ``connected`` is a computed boolean rather than something the client derives
    from the status enum, so every consumer agrees on what "connected" means. A
    connection whose token has expired is *not* connected, and a UI reading the
    enum alone would get that wrong.
    """

    connected: bool
    connection: AliExpressConnectionRead | None = None


class AliExpressAuthorizationResponse(CamelCaseModel):
    """The URL the browser should be sent to for consent."""

    authorization_url: str
    state: str = Field(description="Opaque CSRF token; echoed back on callback.")
    expires_in_seconds: int


class AliExpressConnectRequest(CamelCaseModel):
    """Optional per-tenant credentials for beginning a connection.

    **Both fields are optional, and normally omitted.** In the AliExpress model
    the developer application belongs to the platform operator: DropPilot
    registers one app, and each seller authorises *that* app. A seller does not
    register their own, so requiring them to supply an app key would make
    onboarding impossible.

    They remain accepted for the case where a tenant genuinely operates their
    own AliExpress application — an agency with an existing integration, for
    example. When supplied they take precedence over the platform credentials.

    When an app secret is supplied it arrives once, over TLS, and is encrypted
    before it touches the database. It is never returned by any endpoint.
    """

    app_key: str | None = Field(default=None, max_length=64)
    app_secret: str | None = Field(default=None, max_length=512)


__all__ = [
    "AliExpressAuthorizationResponse",
    "AliExpressConnectRequest",
    "AliExpressConnectionRead",
    "AliExpressErrorResponse",
    "AliExpressStatusResponse",
    "AliExpressTokenResponse",
]
