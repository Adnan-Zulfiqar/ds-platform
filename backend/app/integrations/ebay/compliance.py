"""The eBay Marketplace Account Deletion/Closure receiver.

Two protocol obligations, both from eBay's guide:

**The challenge.** ``GET <endpoint>?challenge_code=…`` must answer
``{"challengeResponse": "<sha256 hex>"}`` with 200 and
``Content-Type: application/json``, where the hash is
``SHA256(challengeCode + verificationToken + endpoint)`` — *"The three
parameters must be hashed in the following order or the verification will
fail"*.

**The notification.** ``POST <endpoint>`` carrying a signed
``MARKETPLACE_ACCOUNT_DELETION`` payload. eBay's own SDKs return *"HTTP status
412 - Precondition Failed"* when the signature does not verify, and 200/201/202/
204 on success.

The order this module works in is deliberate and is the part worth reading:
**verify, parse, claim, erase, acknowledge**. Nothing is acknowledged until the
erasure has actually happened in a committed transaction. eBay resends anything
unacknowledged for 24 hours, so returning a retryable failure costs a delay;
acknowledging early costs a person's deletion request.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Final

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.ebay.deletion import EbayAccountDeletionProcessor
from app.integrations.ebay.exceptions import (
    EbayChallengeError,
    EbayNotConfiguredError,
    EbayPayloadTooLargeError,
)
from app.integrations.ebay.public_key import EbayPublicKeyClient
from app.integrations.ebay.schemas import (
    ChallengeResponse,
    MarketplaceAccountDeletion,
    parse_notification,
)
from app.integrations.ebay.signature import (
    parse_signature_header,
    verify_notification_signature,
)
from app.models.ebay import (
    EbayComplianceNotification,
)
from app.repositories.ebay import EbayComplianceLedgerRepository

logger = get_logger(__name__)

#: eBay's challenge codes are short opaque strings. A generous ceiling that
#: still refuses anything designed to make the hash expensive.
MAX_CHALLENGE_CODE_LENGTH: Final = 256

#: eBay's account-deletion payload is well under a kilobyte; the published
#: sample is ~500 bytes. 64 KiB leaves two orders of magnitude of headroom for
#: a future field while refusing a body sent to exhaust memory. Enforced on the
#: bytes actually read, not on a Content-Length header a caller controls.
MAX_NOTIFICATION_BODY_BYTES: Final = 64 * 1024

#: Content types eBay's AsyncAPI contract declares for the notification.
_ACCEPTED_CONTENT_TYPES: Final = frozenset({"application/json"})


def challenge_response(challenge_code: str | None) -> ChallengeResponse:
    """Answer eBay's endpoint-validation challenge.

    The endpoint in the hash is the **configured** string, never anything
    derived from ``Host``, ``X-Forwarded-Host`` or the request URL. Behind a
    proxy those headers are attacker-influenced, and hashing one would let
    somebody else's Host header decide our challenge answer — which would fail
    validation at best and, at worst, make the answer depend on who asked.
    """
    if not settings.ebay.is_deletion_configured:
        # Fail closed. Hashing an empty token produces a perfectly well-formed
        # digest that eBay rejects, and the operator would be left staring at
        # "endpoint validation failed" with nothing to act on.
        logger.warning("ebay_challenge_rejected", reason="not_configured")
        raise EbayNotConfiguredError()

    if challenge_code is None:
        raise _challenge_rejected("missing")
    code = challenge_code.strip()
    if not code:
        raise _challenge_rejected("blank")
    if len(code) > MAX_CHALLENGE_CODE_LENGTH:
        raise _challenge_rejected("oversized")

    token = settings.ebay.marketplace_deletion_verification_token
    assert token is not None  # guaranteed by is_deletion_configured
    endpoint = settings.ebay.marketplace_deletion_endpoint

    digest = hashlib.sha256()
    # Order is load-bearing and is eBay's, not a preference: challengeCode,
    # then verificationToken, then endpoint. Each fed as UTF-8, matching every
    # snippet in eBay's guide.
    digest.update(code.encode("utf-8"))
    digest.update(token.get_secret_value().encode("utf-8"))
    digest.update(endpoint.encode("utf-8"))

    logger.info("ebay_challenge_answered")
    return ChallengeResponse(challenge_response=digest.hexdigest())


def _challenge_rejected(reason: str) -> EbayChallengeError:
    # The challenge code itself is never logged: it is unauthenticated input,
    # and the reason category is what an operator actually needs.
    logger.warning("ebay_challenge_rejected", reason=reason)
    return EbayChallengeError(details={"reason": reason})


def enforce_body_limit(raw: bytes) -> None:
    """Refuse an oversized body after reading, on the real byte count."""
    if len(raw) > MAX_NOTIFICATION_BODY_BYTES:
        logger.warning("ebay_notification_rejected", reason="oversized_body", size=len(raw))
        raise EbayPayloadTooLargeError()


def accepts_content_type(header: str | None) -> bool:
    """Whether the declared content type is one eBay's contract names."""
    if not header:
        return False
    return header.split(";", 1)[0].strip().lower() in _ACCEPTED_CONTENT_TYPES


class EbayComplianceService:
    """Verify, record and process one account-deletion notification."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        key_client: EbayPublicKeyClient | None = None,
    ) -> None:
        self.session = session
        self.ledger = EbayComplianceLedgerRepository(session)
        self._keys = key_client or EbayPublicKeyClient()

    async def verify(self, *, raw_body: bytes, signature_header: str | None) -> None:
        """Prove the notification came from eBay, before anything else happens.

        A failure here raises: either 412 (the signature is wrong and will
        always be wrong) or a 502-class error (eBay's key service or our OAuth
        is down, so eBay should resend). The distinction matters — telling eBay
        "precondition failed" for a transient outage would discard a real
        deletion request.
        """
        header = parse_signature_header(signature_header)
        key = await self._keys.get(header.key_id)
        verify_notification_signature(
            raw_body=raw_body,
            header=header,
            public_key_pem=key.key,
            key_algorithm=key.algorithm,
            key_digest=key.digest,
        )
        logger.info("ebay_notification_signature_verified", key_id=str(header.key_id))

    async def process(
        self, *, raw_body: bytes, notification: MarketplaceAccountDeletion
    ) -> EbayComplianceNotification:
        """Claim the notification, erase, and record the outcome.

        All of it in the caller's single transaction. The ledger row and the
        erasure commit together or not at all, so there is no state in which
        the ledger says "completed" while the deletion was rolled back — which
        would be a permanent, silent compliance failure, since eBay would never
        resend an acknowledged notification.
        """
        digest = hashlib.sha256(raw_body).hexdigest()
        now = datetime.now(UTC)

        existing = await self.ledger.get_by_notification_id(notification.notification_id)
        if existing is not None:
            return await self._record_duplicate(existing, now)

        try:
            record = await self.ledger.claim(
                notification_id=notification.notification_id,
                topic=notification.topic,
                schema_version=notification.schema_version,
                event_date=notification.event_date,
                publish_date=notification.publish_date,
                payload_digest=digest,
                received_at=now,
            )
        except IntegrityError:
            # Another delivery of the same notification won the race. The
            # unique constraint is what decides, not a read we did earlier —
            # between that read and this insert there is a window, and this is
            # what closes it.
            await self.session.rollback()
            duplicate = await self.ledger.get_by_notification_id(notification.notification_id)
            if duplicate is None:  # pragma: no cover - only if the row vanished
                raise
            logger.info("ebay_notification_duplicate", reason="insert_race")
            return await self._record_duplicate(duplicate, now)

        outcome = await EbayAccountDeletionProcessor(self.session).erase(notification.subject)
        await self.ledger.complete(
            record,
            erased=outcome.erased,
            outcome_code="erased" if outcome.erased else "no_matching_data",
        )
        logger.info(
            "ebay_notification_processed",
            erased=outcome.erased,
            owners=len(outcome.owners_run),
        )
        return record

    async def _record_duplicate(
        self, record: EbayComplianceNotification, now: datetime
    ) -> EbayComplianceNotification:
        """Acknowledge a repeat without repeating destructive work.

        The receipt is counted so an operator can see redelivery happening, but
        no owner is run again. eBay retries until acknowledged, so duplicates
        are normal traffic rather than an anomaly.
        """
        logger.info("ebay_notification_duplicate", receipts=record.receipt_count + 1)
        return await self.ledger.record_repeat(record, received_at=now)


__all__ = [
    "MAX_CHALLENGE_CODE_LENGTH",
    "MAX_NOTIFICATION_BODY_BYTES",
    "EbayComplianceService",
    "accepts_content_type",
    "challenge_response",
    "enforce_body_limit",
    "parse_notification",
]
