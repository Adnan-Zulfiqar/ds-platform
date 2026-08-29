"""Which legal documents a registration must acknowledge, and their versions.

Kept in one module so the version a user accepted is recorded against a value
somebody can look up, rather than a timestamp that says only "some wording, at
some point".

**There is currently no Terms of Service document.** `/privacy` exists and is
versioned; a Terms page does not exist anywhere in this repository. Registration
still requires the acceptance flag — otherwise the mechanism could be bypassed
the moment the document lands — but the version recorded is the sentinel below,
and *nobody should read that as the terms having been agreed to*. Publishing a
Terms document and setting a real version here is a launch blocker, recorded in
`docs/governance/README.md`.

No wording is invented here. The privacy version is the `LAST_UPDATED` value the
published page actually shows.
"""

from __future__ import annotations

from typing import Final

from app.core.exceptions import ValidationError

__all__ = [
    "PRIVACY_NOTICE_VERSION",
    "TERMS_PUBLISHED",
    "TERMS_VERSION",
    "LegalAcceptanceError",
]

#: Matches `LAST_UPDATED` on `frontend/app/privacy/page.tsx`. If that page is
#: edited, this must move with it, or users are recorded as having accepted a
#: version they never saw.
PRIVACY_NOTICE_VERSION: Final[str] = "2026-08-28"

#: No Terms document exists. This sentinel is deliberately not a date: a date
#: would look like a real version and hide the gap.
TERMS_VERSION: Final[str] = "unpublished"

TERMS_PUBLISHED: Final[bool] = False


class LegalAcceptanceError(ValidationError):
    """Registration was attempted without valid acceptance.

    A `ValidationError` rather than a bare `ValueError` so it travels through
    the application's error envelope as a 400 the client can act on. A plain
    `ValueError` escaped as an unhandled 500, which told the caller nothing and
    logged a stack trace for an ordinary bad request.
    """

    code = "legal_acceptance_required"
