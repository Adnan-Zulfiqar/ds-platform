"""Erase a person's eBay data across every tenant that holds it.

## The audit this is built on

A sweep of every model in ``app/models/`` on 23 August 2026 found **no table
that stores eBay user data**. `StorePlatform.EBAY` exists as an enum member and
the frontend offers "eBay" in a store-platform dropdown, but nothing persists an
eBay username, user id, EIAS token, eBay order, eBay listing or eBay OAuth
token. ``test_ebay_c0_deletion.py`` asserts that by searching the model layer,
so the claim is checked on every run rather than believed.

That makes EBAY-C0's correct behaviour a **verified zero-match deletion**: the
notification is authenticated, recorded and completed, and the count of erased
records is zero because there was nothing to erase. It is not a stub. The same
processor runs when the first owner is registered — what changes then is the
contents of ``_OWNERS``, not this code path.

## The guard that makes that safe

``_OWNERS`` is empty, and it is the single place a future eBay data owner is
registered. EBAY-C1 cannot ship eBay OAuth or eBay data persistence without
adding an entry here, because ``test_every_ebay_data_owner_is_registered``
fails when a model gains eBay-identifying columns that no owner covers. That is
the release guard the roadmap refers to: it is not a note asking a future author
to remember, it is a test that fails.

## Cross-tenant by necessity

An eBay account deletion is an instruction about a *person*, and one person may
have data under several workspaces. The processor therefore runs unscoped —
deliberately, narrowly, and only from the compliance receiver. It is not
reachable from any merchant-authenticated endpoint, which is what stops it
becoming a way for one tenant to reach another's rows.

## Erasure, not soft deletion

The compliance requirement is that *"even the highest system privilege cannot
reverse the deletion"*. Every owner registered here must therefore physically
delete or irreversibly anonymise. ``deleted_at`` is not erasure, and this module
must never grow a soft-delete path — a row that can be undeleted has not been
erased.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class DeletionSubject:
    """Who eBay is asking about.

    Held **only** for the life of one processing transaction. None of these
    values is written to the ledger, logged, cached or returned; the whole point
    of the compliance flow is that they leave no trace here.
    """

    user_id: str | None
    username: str | None
    eias_token: str | None

    @property
    def has_any_identifier(self) -> bool:
        return bool(self.user_id or self.username or self.eias_token)


@dataclass
class DeletionOutcome:
    """What the processor actually erased."""

    erased: int = 0
    #: Owner names that ran, for the log line. Never contains an identifier.
    owners_run: list[str] = field(default_factory=list)

    def record(self, owner: str, count: int) -> None:
        self.owners_run.append(owner)
        self.erased += count


class EbayDataOwner(Protocol):
    """A table (or group of tables) that stores eBay user data.

    Every future eBay data model registers one of these. ``erase`` must delete
    or irreversibly anonymise, must be safe to run when nothing matches, and
    must be idempotent — a second delivery of the same notification will run it
    again and must not fail.
    """

    name: str

    async def erase(self, session: AsyncSession, subject: DeletionSubject) -> int:
        """Erase everything belonging to ``subject``; return the row count."""
        ...


#: **Empty by design as of EBAY-C0.** No table in this application stores eBay
#: user data yet. EBAY-C1 registers the first owner here, in the same change
#: that introduces the storage — never after it.
_OWNERS: Final[tuple[EbayDataOwner, ...]] = ()


class EbayAccountDeletionProcessor:
    """Runs every registered owner for one deletion subject.

    Unscoped on purpose and documented as such: see the module docstring. The
    class takes a session rather than opening one so the caller decides the
    transaction boundary — the receiver runs erasure and the ledger write in
    the *same* transaction, so a store can never be left half-erased with a
    ledger row claiming it finished.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def registered_owners() -> Sequence[EbayDataOwner]:
        return _OWNERS

    async def erase(self, subject: DeletionSubject) -> DeletionOutcome:
        outcome = DeletionOutcome()
        if not subject.has_any_identifier:
            # eBay always sends at least one identifier. Nothing to match on is
            # a malformed payload, and the caller has already rejected it —
            # this is the belt to that braces.
            logger.warning("ebay_deletion_no_identifier")
            return outcome

        for owner in _OWNERS:
            erased = await owner.erase(self._session, subject)
            outcome.record(owner.name, erased)

        # No identifier appears in this log line: owner names and a count only.
        logger.info(
            "ebay_deletion_processed",
            owners=len(_OWNERS),
            erased=outcome.erased,
            owners_run=outcome.owners_run,
        )
        return outcome


__all__ = [
    "DeletionOutcome",
    "DeletionSubject",
    "EbayAccountDeletionProcessor",
    "EbayDataOwner",
]
