"""AliExpress Open Platform integration.

Phase 3 builds the connection foundation only: OAuth, encrypted credential
storage, a signed HTTP client, and a health check. Product discovery, import,
price and inventory synchronisation, and order automation are later phases —
this package exists so those are feature work rather than infrastructure work.

See docs/ALIEXPRESS_INTEGRATION.md, including the note on verifying the API
endpoints and signing scheme against current AliExpress documentation before
live traffic.
"""

from app.integrations.aliexpress.client import AliExpressClient
from app.integrations.aliexpress.exceptions import (
    AliExpressAuthError,
    AliExpressError,
    AliExpressNotConnectedError,
    AliExpressOAuthStateError,
    AliExpressRateLimitError,
    AliExpressResponseError,
    AliExpressTimeoutError,
    AliExpressTokenExpiredError,
    AliExpressUnavailableError,
)
from app.integrations.aliexpress.service import AliExpressService

__all__ = [
    "AliExpressAuthError",
    "AliExpressClient",
    "AliExpressError",
    "AliExpressNotConnectedError",
    "AliExpressOAuthStateError",
    "AliExpressRateLimitError",
    "AliExpressResponseError",
    "AliExpressService",
    "AliExpressTimeoutError",
    "AliExpressTokenExpiredError",
    "AliExpressUnavailableError",
]
