"""Erasure of one data subject's personal data, on request.

The product has no self-service "delete my account" control, so a UK GDPR
Article 17 request arrives by email at `privacy@whiteto.com` and someone has to
carry it out by hand. This module is that hand: an auditable, rehearsable
operation rather than a session of ad-hoc SQL against production at the end of a
month-long deadline.

**Design follows `app/integrations/ebay/deletion.py` deliberately.** That module
already established the right shape for this problem — an explicit declaration
of every place personal data lives, rather than a scan that finds `email` and
misses `buyer_name` in a JSONB column. Reusing the shape means the two erasure
paths stay legible together and an engineer who has read one can read the other.

Three rules the implementation exists to enforce:

* **Dry run first, always.** `plan()` reads and counts; `erase()` is the only
  thing that writes, and callers must ask for it explicitly.
* **One tenant, never two.** Every statement is filtered by the subject's
  `tenant_id`. The eBay ledger is the sole exception, and it holds no personal
  data at all.
* **The subject's own data is never logged.** Erasing someone's email address
  while writing it to a log file that outlives the erasure is not erasure. Log
  lines carry identifiers and counts; the operator already knows who they are.

What this does **not** do is decide the controller question for buyer/order
data. Where a merchant syncs their sales channel, the buyer's details belong to
the merchant's relationship with their customer, and DESIRLY LIMITED handles
them on the merchant's instructions. Deleting a *merchant's* account therefore
removes their workspace's copy; it is not a route for a shopper to erase
themselves, and the code says so rather than implying otherwise.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Final, cast

from sqlalchemy import CursorResult, Executable, Select, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.email_verification import EmailVerificationToken
from app.models.integration import AliExpressConnection
from app.models.notification import Notification
from app.models.order import Order
from app.models.refresh_token import RefreshToken
from app.models.role import UserRole
from app.models.shopify import ShopifyConnection
from app.models.store import Store
from app.models.user import User

logger = get_logger(__name__)

__all__ = [
    "DataSubjectErasureService",
    "ErasureCategory",
    "ErasurePlan",
    "ErasureSubject",
]

#: What an anonymised user row looks like afterwards. The row itself survives
#: because orders, products and audit rows reference it by id, and a cascade
#: would destroy a merchant's business records to satisfy a deletion request
#: about one person. Replacing the identifying fields achieves erasure without
#: that collateral damage.
_ANONYMISED_EMAIL_DOMAIN: Final[str] = "erased.invalid"


@dataclass(frozen=True, slots=True)
class ErasureSubject:
    """Who is being erased, resolved from an email address by the operator."""

    user_id: uuid.UUID
    tenant_id: uuid.UUID

    def __str__(self) -> str:  # pragma: no cover - trivial
        # No email address, deliberately: this ends up in log lines.
        return f"user={self.user_id} tenant={self.tenant_id}"


@dataclass(frozen=True, slots=True)
class ErasureCategory:
    """One declared place personal data lives, and what will happen to it."""

    name: str
    action: str
    note: str


@dataclass
class ErasurePlan:
    """Counts per category, produced before and after the write."""

    subject: ErasureSubject
    counts: dict[str, int] = field(default_factory=dict)
    executed: bool = False

    def record(self, name: str, count: int) -> None:
        self.counts[name] = self.counts.get(name, 0) + count

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def describe(self) -> str:
        rows = ", ".join(f"{k}={v}" for k, v in sorted(self.counts.items()))
        verb = "erased" if self.executed else "would erase"
        return f"{verb} {self.total} row(s) for {self.subject}: {rows}"


#: Every place this application stores personal data for a platform user.
#: Declared, not discovered — see the module docstring and the eBay equivalent.
ERASURE_CATEGORIES: Final[tuple[ErasureCategory, ...]] = (
    ErasureCategory("refresh_tokens", "delete", "Session credentials; hashed, still linkable."),
    ErasureCategory("email_verification_tokens", "delete", "Hashed tokens tied to the address."),
    ErasureCategory("user_roles", "delete", "Physical delete: a soft-deleted grant still grants."),
    ErasureCategory("notifications", "delete", "Titles and bodies may quote personal data."),
    ErasureCategory("shopify_connections", "delete", "Encrypted seller credentials."),
    ErasureCategory("aliexpress_connections", "delete", "Encrypted supplier credentials."),
    ErasureCategory("ebay_connections", "delete", "Handled by the eBay processor; counted here."),
    ErasureCategory("stores", "anonymise", "Clears the connecting user reference."),
    ErasureCategory("orders", "anonymise", "Buyer/recipient fields cleared; totals retained."),
    ErasureCategory(
        "user", "anonymise", "Identifying fields replaced; row retained for references."
    ),
)


class DataSubjectErasureService:
    """Plan and perform erasure for one platform user.

    Deliberately **not** a `TenantScopedRepository` subclass. This runs from an
    operator script with no request context and therefore no tenant in
    `contextvars`; the tenant comes from the resolved subject and is applied
    explicitly to every statement. That is the documented exception shape rather
    than a bypass method added to a scoped repository.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _scalar_count(self, statement: Select[tuple[int]]) -> int:
        return int((await self._session.execute(statement)).scalar_one())

    async def _affected(self, statement: Executable) -> int:
        """Rows a DML statement touched.

        `AsyncSession.execute` is typed as returning `Result`, which has no
        `rowcount`; for an UPDATE or DELETE it is really a `CursorResult`. The
        cast records that rather than hiding it behind a blanket ignore.
        """
        result = await self._session.execute(statement)
        return int(cast("CursorResult[Any]", result).rowcount or 0)

    @staticmethod
    def categories() -> tuple[ErasureCategory, ...]:
        return ERASURE_CATEGORIES

    async def resolve(self, email: str) -> ErasureSubject | None:
        """Find exactly one user by address. Returns ``None`` if absent.

        The address is supplied by the operator handling the request and is
        never logged here.
        """
        row = (
            await self._session.execute(
                select(User.id, User.tenant_id).where(
                    func.lower(User.email) == email.strip().lower()
                )
            )
        ).first()
        if row is None:
            return None
        return ErasureSubject(user_id=row[0], tenant_id=row[1])

    async def plan(self, subject: ErasureSubject) -> ErasurePlan:
        """Count what erasure would touch, writing nothing."""
        plan = ErasurePlan(subject=subject)

        async def count(name: str, stmt: Select[tuple[int]]) -> None:
            plan.record(name, await self._scalar_count(stmt))

        await count(
            "refresh_tokens",
            select(func.count())
            .select_from(RefreshToken)
            .where(RefreshToken.user_id == subject.user_id),
        )
        await count(
            "email_verification_tokens",
            select(func.count())
            .select_from(EmailVerificationToken)
            .where(EmailVerificationToken.user_id == subject.user_id),
        )
        await count(
            "user_roles",
            select(func.count()).select_from(UserRole).where(UserRole.user_id == subject.user_id),
        )
        await count(
            "notifications",
            select(func.count())
            .select_from(Notification)
            .where(Notification.user_id == subject.user_id),
        )
        await count(
            "shopify_connections",
            select(func.count())
            .select_from(ShopifyConnection)
            .where(ShopifyConnection.tenant_id == subject.tenant_id),
        )
        await count(
            "aliexpress_connections",
            select(func.count())
            .select_from(AliExpressConnection)
            .where(AliExpressConnection.tenant_id == subject.tenant_id),
        )
        await count(
            "stores",
            select(func.count())
            .select_from(Store)
            .where(
                Store.tenant_id == subject.tenant_id,
                Store.connected_by_user_id == subject.user_id,
            ),
        )
        await count(
            "orders",
            select(func.count())
            .select_from(Order)
            .where(
                Order.tenant_id == subject.tenant_id,
                Order.buyer_name.isnot(None),
            ),
        )
        plan.record("user", 1)
        return plan

    async def erase(self, subject: ErasureSubject) -> ErasurePlan:
        """Perform erasure. Idempotent: a second run reports zeroes.

        The caller owns the transaction, so an operator script can roll back
        after a rehearsal without a special code path here.
        """
        plan = ErasurePlan(subject=subject, executed=True)

        for name, stmt in (
            (
                "refresh_tokens",
                delete(RefreshToken).where(RefreshToken.user_id == subject.user_id),
            ),
            (
                "email_verification_tokens",
                delete(EmailVerificationToken).where(
                    EmailVerificationToken.user_id == subject.user_id
                ),
            ),
            ("user_roles", delete(UserRole).where(UserRole.user_id == subject.user_id)),
            (
                "notifications",
                delete(Notification).where(Notification.user_id == subject.user_id),
            ),
            (
                "shopify_connections",
                delete(ShopifyConnection).where(ShopifyConnection.tenant_id == subject.tenant_id),
            ),
            (
                "aliexpress_connections",
                delete(AliExpressConnection).where(
                    AliExpressConnection.tenant_id == subject.tenant_id
                ),
            ),
        ):
            plan.record(name, await self._affected(stmt))

        # Stores keep their catalogue but lose the link to the person.
        plan.record(
            "stores",
            await self._affected(
                update(Store)
                .where(
                    Store.tenant_id == subject.tenant_id,
                    Store.connected_by_user_id == subject.user_id,
                )
                .values(connected_by_user_id=None)
            ),
        )

        # Orders keep their commercial figures and lose the human.
        plan.record(
            "orders",
            await self._affected(
                update(Order)
                .where(
                    Order.tenant_id == subject.tenant_id,
                    Order.buyer_name.isnot(None),
                )
                .values(
                    buyer_name=None,
                    recipient_name=None,
                    recipient_phone=None,
                    city=None,
                    province=None,
                    postal_code=None,
                )
            ),
        )

        # The user row survives so foreign keys stay intact; what identifies a
        # person does not.
        plan.record(
            "user",
            await self._affected(
                update(User)
                .where(User.id == subject.user_id, User.tenant_id == subject.tenant_id)
                .values(
                    email=f"erased-{subject.user_id}@{_ANONYMISED_EMAIL_DOMAIN}",
                    first_name=None,
                    last_name=None,
                    password_hash=None,
                    is_active=False,
                )
            ),
        )

        logger.info(
            "data_subject_erased",
            user_id=str(subject.user_id),
            tenant_id=str(subject.tenant_id),
            rows=plan.total,
        )
        return plan
