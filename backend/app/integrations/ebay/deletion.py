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

## What automated discovery cannot do

``test_ebay_c0_ledger`` sweeps the model layer for eBay-shaped column names, and
that sweep is a backstop, not a guarantee. It cannot see an identifier inside a
``JSONB`` bag, an encrypted column named ``credentials``, a field called
``external_reference``, or an audit row that quotes a payload — and any regex
that tried would either miss those or flag half the schema.

That is why ``EBAY_STORAGE_DECLARATIONS`` exists and why it is a *declaration*.
The obligation sits with the change that introduces the storage, and the guard
checks that the declaration and the owner agree. Claiming the sweep alone makes
this safe would be the kind of assurance that is worse than none.

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
from typing import Any, Final, Protocol, cast

from sqlalchemy import delete, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.ebay import EbayConnection, EbayListingDefaults

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


@dataclass(frozen=True, slots=True)
class EbayStorageDeclaration:
    """A place this application stores eBay personal data, declared explicitly.

    **Declaration, not discovery.** A regex over column names finds
    ``ebay_user_id`` and misses every interesting case: an identifier inside a
    ``JSONB`` settings bag, an encrypted token column named ``credentials``, a
    field called ``external_reference``, an audit row that quotes a payload.
    Pretending otherwise would be worse than useless — it would look like a
    guarantee.

    So the contract is inverted. A service that persists eBay personal data
    declares that fact here, naming the storage and the owner that erases it,
    and the guard test checks the two halves agree. The regex sweep still runs
    as a backstop for the obvious cases, but the declaration is the mechanism.

    ``owner_name`` must match a registered owner's ``name``. A declaration with
    no owner is a build failure, which is the whole point: it is not possible to
    declare eBay storage and forget the eraser.
    """

    #: Dotted model path or table name — whatever a reader would grep for.
    storage: str
    #: The ``EbayDataOwner.name`` responsible for erasing it.
    owner_name: str
    #: What personal data lives there, in one line. Reviewed by a human.
    holds: str


class EbayConnectionOwner:
    """Erases a seller's eBay connection when eBay says to delete the account.

    **What "erase" means here, and why it is a delete rather than an anonymise.**
    The row's whole purpose is to hold eBay credentials and eBay's immutable
    account id. Anonymise the identifier and what remains is an encrypted access
    token belonging to an account we have been told to forget — worse than
    useless, because it is retained credential material with nothing left to
    associate it with. So the row goes.

    **Matched on ``ebay_user_id`` only.** eBay's notification carries
    ``userId``, ``username`` and ``eiasToken``; only the first is the immutable
    identifier this platform keys on. Matching on ``username`` as a fallback
    would be worse than not matching: a seller who renamed could collide with a
    different account that has since taken the old name, and erasing the wrong
    workspace's connection is unrecoverable.

    **Deliberately crosses tenants**, which is why it lives here rather than in
    a repository. eBay is not making a request on behalf of one workspace; it is
    telling the platform that a person is gone. The statement is narrow — one
    equality predicate on an indexed, globally-unique column — rather than a
    general query surface.

    Idempotent by construction: a redelivery finds nothing and returns 0.
    """

    name = "ebay_connection"

    async def erase(self, session: AsyncSession, subject: DeletionSubject) -> int:
        if not subject.user_id:
            # Without the immutable id there is nothing safe to match. Erasing
            # on a mutable name is how the wrong tenant loses its connection.
            logger.info("ebay_connection_erase_skipped_no_immutable_id")
            return 0

        result = await session.execute(
            delete(EbayConnection).where(EbayConnection.ebay_user_id == subject.user_id)
        )
        # `rowcount` exists on the cursor result a DELETE returns, but
        # `execute` is typed as returning the general Result — same cast as
        # `BaseRepository.update_where`.
        erased = int(cast("CursorResult[Any]", result).rowcount or 0)
        # Count only. The identifier is the thing being erased; logging it would
        # leave it behind in exactly the place nobody thinks to purge.
        logger.info("ebay_connection_erased", rows=erased)
        return erased


class EbayListingDefaultsOwner:
    """Erases the seller's chosen policy ids and location key (EBAY-C2).

    The rows would also go by ``ON DELETE CASCADE`` when the connection owner
    deletes the connection, but cascade is not a declared, counted erasure:
    running first and deleting explicitly makes the processor's row count
    true, and keeps erasure correct if the foreign key is ever changed.

    Matched through the connection's ``ebay_user_id`` only, for the reason
    ``EbayConnectionOwner`` gives. Crosses tenants for the same reason.
    Idempotent: a redelivery finds nothing.
    """

    name = "ebay_listing_defaults"

    async def erase(self, session: AsyncSession, subject: DeletionSubject) -> int:
        if not subject.user_id:
            logger.info("ebay_listing_defaults_erase_skipped_no_immutable_id")
            return 0

        connection_ids = select(EbayConnection.id).where(
            EbayConnection.ebay_user_id == subject.user_id
        )
        result = await session.execute(
            delete(EbayListingDefaults).where(EbayListingDefaults.connection_id.in_(connection_ids))
        )
        erased = int(cast("CursorResult[Any]", result).rowcount or 0)
        logger.info("ebay_listing_defaults_erased", rows=erased)
        return erased


#: Declared in the **same change** that introduced ``ebay_connections`` — see
#: migration ``0030``. The guard test fails if this and ``_OWNERS`` disagree, so
#: shipping the table without its eraser is not possible.
EBAY_STORAGE_DECLARATIONS: Final[tuple[EbayStorageDeclaration, ...]] = (
    EbayStorageDeclaration(
        storage="app.models.ebay.EbayConnection",
        owner_name="ebay_connection",
        holds=(
            "eBay immutable userId, display username, marketplace and account "
            "type, plus encrypted access and refresh tokens for the seller."
        ),
    ),
    # EBAY-C2, migration 0037.
    EbayStorageDeclaration(
        storage="app.models.ebay.EbayListingDefaults",
        owner_name="ebay_listing_defaults",
        holds=(
            "The seller's eBay business-policy ids and inventory location key, "
            "chosen per marketplace."
        ),
    ),
)

#: One owner per declaration. Defaults first: they hang off the connection
#: row, and erasing them before it keeps both counts accurate.
_OWNERS: Final[tuple[EbayDataOwner, ...]] = (
    EbayListingDefaultsOwner(),
    EbayConnectionOwner(),
)


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

    @staticmethod
    def declarations() -> Sequence[EbayStorageDeclaration]:
        return EBAY_STORAGE_DECLARATIONS

    @staticmethod
    def undeclared_owner_names() -> tuple[str, ...]:
        """Owners with no declaration — an eraser for storage nobody named.

        Not automatically wrong, but always worth explaining: it usually means
        the storage was removed and the owner was left behind, and a dead owner
        is one that stops being maintained while still looking like coverage.
        """
        declared = {declaration.owner_name for declaration in EBAY_STORAGE_DECLARATIONS}
        return tuple(owner.name for owner in _OWNERS if owner.name not in declared)

    @staticmethod
    def unowned_declarations() -> tuple[str, ...]:
        """Declared storage with no eraser. **Always** a release blocker.

        This is the guard the roadmap refers to: declaring that eBay personal
        data is stored, without registering something that erases it, means the
        application cannot honour a deletion request it is legally obliged to
        honour.
        """
        owners = {owner.name for owner in _OWNERS}
        return tuple(
            declaration.storage
            for declaration in EBAY_STORAGE_DECLARATIONS
            if declaration.owner_name not in owners
        )

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
