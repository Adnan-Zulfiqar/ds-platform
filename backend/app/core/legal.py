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
    "TermsNotPublishedError",
]

#: Matches `LAST_UPDATED` on `frontend/app/privacy/page.tsx`. If that page is
#: edited, this must move with it, or users are recorded as having accepted a
#: version they never saw.
PRIVACY_NOTICE_VERSION: Final[str] = "2026-08-28"

#: The Terms exist as a **draft** awaiting solicitor review.
#:
#: The `draft-` prefix is load-bearing, not decoration. A bare date would look
#: like a published version to anybody reading a stored acceptance row a year
#: from now, and the whole point of recording a version is that somebody can
#: later look up exactly what was agreed. Until a solicitor has approved the
#: wording, what would be looked up is a draft, and the identifier says so.
#:
#: Replacing this with a published identifier is a deliberate act that belongs
#: with `TERMS_PUBLISHED`, not an incidental edit — see
#: `docs/legal/TERMS_PUBLICATION_CHECKLIST.md`.
TERMS_VERSION: Final[str] = "draft-2026-08-31"

#: Whether the Terms may be presented as a binding contract.
#:
#: `False` means no deployed environment may form a contract on them. It is not
#: advisory: `LegalAcceptance.require_valid` refuses registration outright while
#: this is false and the environment is deployed, so a production signup cannot
#: record agreement to text nobody has approved. Local and test environments are
#: exempt so the flow can be built and exercised.
TERMS_PUBLISHED: Final[bool] = False


class TermsNotPublishedError(ValidationError):
    """Registration was attempted before the Terms were approved for publication.

    Separate from `LegalAcceptanceError` because it is not the caller's fault
    and there is nothing they can do about it. It says the service is not open
    for new contracts, not that the request was malformed — and a distinct code
    means a client can say something true rather than asking the person to tick
    a box again.
    """

    code = "terms_not_published"


class LegalAcceptanceError(ValidationError):
    """Registration was attempted without valid acceptance.

    A `ValidationError` rather than a bare `ValueError` so it travels through
    the application's error envelope as a 400 the client can act on. A plain
    `ValueError` escaped as an unhandled 500, which told the caller nothing and
    logged a stack trace for an ordinary bad request.
    """

    code = "legal_acceptance_required"
