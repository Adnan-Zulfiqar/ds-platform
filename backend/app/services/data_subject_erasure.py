"""Erasure of personal data, scoped to one explicit kind of data subject.

The first version of this module treated "the person", "the workspace" and "the
merchant's customer" as one subject resolved from an email address. Independent
review rejected it, correctly. Erasing a single employee should not delete the
workspace's eBay credentials, and it certainly should not blank the buyer
details on every order the company has ever received.

**There are three subjects here, and they are deliberately kept apart.**

* **`PLATFORM_USER`** — identified by tenant *and* user. Touches that
  person's credentials, grants and notifications, and clears every column
  naming them.
* **`WORKSPACE`** — identified by tenant. Everything above for every member,
  plus the workspace's marketplace connections and their encrypted credentials.
* **Marketplace buyer** — *not implemented*, for the reason below.

### Why buyer erasure is absent rather than approximated

`orders` has `buyer_name`, `recipient_name`, `recipient_phone` and an address,
and **no buyer identifier at all** — no email, no external buyer id, no customer
id. The only way to "find" a buyer is to match on a low-entropy name, which
would erase a different customer who happens to share it.

The previous version compensated by clearing the buyer fields on *every* order
in the tenant. That destroyed uninvolved customers' records to satisfy a request
about one person, and it is exactly the shortcut this module now refuses to
take. Buyer erasure is a launch blocker recorded in
`docs/governance/DATA_SUBJECT_REQUESTS.md`, not a feature that half-works.

### Safety properties

* **Resolution fails closed.** A tenant is always required. Zero matches and
  multiple matches both raise; neither reveals whether a foreign tenant holds
  the address.
* **Nothing commits itself.** Both services mutate within the caller's
  transaction. The operator script owns the commit and rolls back on any
  exception, so a failure halfway through leaves nothing behind.
* **The subject's address is never logged.** Log lines carry ids and counts.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final, cast

from sqlalchemy import CursorResult, Executable, Select, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.ai_prompt import AIPrompt, PromptExecution
from app.models.ebay import EbayConnection
from app.models.email_verification import EmailVerificationToken
from app.models.integration import AliExpressConnection
from app.models.inventory import InventorySyncRun
from app.models.notification import Notification
from app.models.order import OrderSyncRun
from app.models.pricing import GlobalRuleVersion, PriceChange
from app.models.product import ProductImport, ProductVersion
from app.models.refresh_token import RefreshToken
from app.models.role import UserRole
from app.models.rule_application import RuleApplication
from app.models.shopify import ShopifyConnection
from app.models.store import Store
from app.models.user import User

logger = get_logger(__name__)

__all__ = [
    "USER_REFERENCES",
    "ErasureOutcome",
    "ErasureScope",
    "PlatformUserErasureService",
    "SubjectRef",
    "SubjectResolutionError",
    "WorkspaceClosureService",
    "login_throttle_keys",
]

_ANONYMISED_EMAIL_DOMAIN: Final[str] = "erased.invalid"


class ErasureScope(StrEnum):
    """Which subject an operation concerns. Never inferred, always stated."""

    PLATFORM_USER = "platform-user"
    WORKSPACE = "workspace"


class SubjectResolutionError(RuntimeError):
    """Resolution did not produce exactly one subject. Always fails closed."""


@dataclass(frozen=True, slots=True)
class SubjectRef:
    """A resolved subject: one user inside one tenant, both known."""

    tenant_id: uuid.UUID
    user_id: uuid.UUID

    def __str__(self) -> str:
        # Reaches log lines. No email address, deliberately.
        return f"tenant={self.tenant_id} user={self.user_id}"


@dataclass
class ErasureOutcome:
    """Counts per category, for a dry run or a real one."""

    scope: ErasureScope
    counts: dict[str, int] = field(default_factory=dict)
    executed: bool = False

    def record(self, name: str, count: int) -> None:
        self.counts[name] = self.counts.get(name, 0) + count

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def describe(self) -> str:
        rows = ", ".join(f"{k}={v}" for k, v in sorted(self.counts.items()) if v)
        verb = "erased" if self.executed else "would erase"
        return f"[{self.scope}] {verb} {self.total} row(s): {rows or 'nothing'}"


@dataclass(frozen=True, slots=True)
class UserReference:
    """One column somewhere that names a platform user.

    ``action`` is ``"delete"`` when the row exists only because of that person
    (their session, their grant, their notification), and ``"clear"`` when the
    row belongs to the workspace and merely records who acted. Clearing keeps
    the workspace's history intact while removing the link to the person.
    """

    label: str
    model: Any
    column: Any
    action: str
    tenant_column: Any | None


#: **Every** column in the schema that references ``users.id``, enumerated.
#:
#: Derived by walking every model for `ForeignKey("users.id")` rather than by
#: memory — the previous version cleared exactly one of these seventeen and left
#: the other sixteen pointing at the erased person.
USER_REFERENCES: Final[tuple[UserReference, ...]] = (
    # Rows that exist only for that person.
    UserReference("refresh_tokens", RefreshToken, RefreshToken.user_id, "delete", None),
    UserReference(
        "email_verification_tokens",
        EmailVerificationToken,
        EmailVerificationToken.user_id,
        "delete",
        EmailVerificationToken.tenant_id,
    ),
    UserReference("user_roles", UserRole, UserRole.user_id, "delete", None),
    UserReference(
        "notifications", Notification, Notification.user_id, "delete", Notification.tenant_id
    ),
    # Workspace-owned rows that merely name who acted.
    # `ai_prompts` carries no tenant column — the user id is the only scope
    # available, and a user belongs to exactly one tenant, so it is sufficient.
    UserReference("ai_prompts", AIPrompt, AIPrompt.created_by_user_id, "clear", None),
    UserReference(
        "prompt_executions",
        PromptExecution,
        PromptExecution.executed_by_user_id,
        "clear",
        PromptExecution.tenant_id,
    ),
    UserReference(
        "inventory_sync_runs",
        InventorySyncRun,
        InventorySyncRun.requested_by_user_id,
        "clear",
        InventorySyncRun.tenant_id,
    ),
    UserReference(
        "order_sync_runs",
        OrderSyncRun,
        OrderSyncRun.requested_by_user_id,
        "clear",
        OrderSyncRun.tenant_id,
    ),
    UserReference(
        "price_changes", PriceChange, PriceChange.applied_by_user_id, "clear", PriceChange.tenant_id
    ),
    UserReference(
        "global_rule_versions",
        GlobalRuleVersion,
        GlobalRuleVersion.changed_by_user_id,
        "clear",
        GlobalRuleVersion.tenant_id,
    ),
    UserReference(
        "product_imports",
        ProductImport,
        ProductImport.requested_by_user_id,
        "clear",
        ProductImport.tenant_id,
    ),
    UserReference(
        "product_versions",
        ProductVersion,
        ProductVersion.created_by_user_id,
        "clear",
        ProductVersion.tenant_id,
    ),
    UserReference(
        "rule_applications",
        RuleApplication,
        RuleApplication.requested_by_user_id,
        "clear",
        RuleApplication.tenant_id,
    ),
    UserReference("stores", Store, Store.connected_by_user_id, "clear", Store.tenant_id),
    # Marketplace connections belong to the *workspace*, not the person who
    # happened to click Connect. Platform-user erasure clears the name; only
    # workspace closure deletes the connection and its credentials.
    UserReference(
        "shopify_connections",
        ShopifyConnection,
        ShopifyConnection.user_id,
        "clear",
        ShopifyConnection.tenant_id,
    ),
    UserReference(
        "aliexpress_connections",
        AliExpressConnection,
        AliExpressConnection.user_id,
        "clear",
        AliExpressConnection.tenant_id,
    ),
    UserReference(
        "ebay_connections",
        EbayConnection,
        EbayConnection.user_id,
        "clear",
        EbayConnection.tenant_id,
    ),
)


def login_throttle_keys(email: str) -> tuple[str, ...]:
    """Redis keys the login throttle holds for one address.

    `app/services/login_throttle.py` hashes the address, so these are exactly
    addressable without scanning and without a pattern that could cross tenants.
    """
    digest = hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()[:32]
    return (f"login:email:{digest}",)


class _CountingService:
    """Shared statement plumbing. Not a repository — see the subclass notes."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _count(self, statement: Select[tuple[int]]) -> int:
        return int((await self._session.execute(statement)).scalar_one())

    async def _affected(self, statement: Executable) -> int:
        """Rows a DML statement touched.

        `AsyncSession.execute` is typed as returning `Result`, which has no
        `rowcount`; for UPDATE and DELETE it is really a `CursorResult`.
        """
        result = await self._session.execute(statement)
        return int(cast("CursorResult[Any]", result).rowcount or 0)


class PlatformUserErasureService(_CountingService):
    """Erase one platform user, leaving their colleagues and workspace intact.

    Deliberately **not** a `TenantScopedRepository` subclass: this runs from an
    operator script with no request context, so there is no tenant in
    `contextvars` to inherit. The tenant is supplied explicitly and applied to
    every statement that has a tenant column, which is the documented exception
    shape rather than a bypass method added to a scoped repository.
    """

    @staticmethod
    def references() -> Sequence[UserReference]:
        return USER_REFERENCES

    async def resolve_by_email(self, *, tenant_id: uuid.UUID, email: str) -> SubjectRef:
        """One user, inside one named tenant. Anything else raises.

        The tenant is mandatory. The same address can legitimately exist in two
        workspaces — a consultant with accounts at two clients — and the
        previous `.first()` across the whole table picked an arbitrary one.
        """
        rows = (
            (
                await self._session.execute(
                    select(User.id).where(
                        User.tenant_id == tenant_id,
                        func.lower(User.email) == email.strip().lower(),
                    )
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            # Says nothing about whether another tenant holds the address.
            raise SubjectResolutionError("No user with that address exists in that workspace.")
        if len(rows) > 1:
            raise SubjectResolutionError(
                f"{len(rows)} users share that address in that workspace. "
                "Resolve by user id instead."
            )
        return SubjectRef(tenant_id=tenant_id, user_id=rows[0])

    async def resolve_by_id(self, *, tenant_id: uuid.UUID, user_id: uuid.UUID) -> SubjectRef:
        """Confirm the user really belongs to the named tenant."""
        found = (
            await self._session.execute(
                select(User.id).where(User.id == user_id, User.tenant_id == tenant_id)
            )
        ).scalar_one_or_none()
        if found is None:
            # Identical message whether the user is absent or in another
            # tenant: a distinction here is a cross-tenant oracle.
            raise SubjectResolutionError("No such user in that workspace.")
        return SubjectRef(tenant_id=tenant_id, user_id=user_id)

    def _scoped(self, reference: UserReference, subject: SubjectRef) -> Any:
        """Predicate for one reference: always the user, plus the tenant if present."""
        predicate = reference.column == subject.user_id
        if reference.tenant_column is not None:
            predicate = predicate & (reference.tenant_column == subject.tenant_id)
        return predicate

    async def plan(self, subject: SubjectRef) -> ErasureOutcome:
        """Count what erasure would touch. Writes nothing."""
        outcome = ErasureOutcome(scope=ErasureScope.PLATFORM_USER)
        for reference in USER_REFERENCES:
            outcome.record(
                reference.label,
                await self._count(
                    select(func.count())
                    .select_from(reference.model)
                    .where(self._scoped(reference, subject))
                ),
            )
        outcome.record("user", 1)
        return outcome

    async def erase(self, subject: SubjectRef) -> ErasureOutcome:
        """Erase the user. Idempotent. The caller owns the transaction."""
        outcome = ErasureOutcome(scope=ErasureScope.PLATFORM_USER, executed=True)

        for reference in USER_REFERENCES:
            predicate = self._scoped(reference, subject)
            if reference.action == "delete":
                statement: Executable = delete(reference.model).where(predicate)
            else:
                statement = (
                    update(reference.model).where(predicate).values({reference.column.key: None})
                )
            outcome.record(reference.label, await self._affected(statement))

        # The row survives so orders, products and audit references stay intact;
        # what identifies a person does not. A cascade here would destroy a
        # business's records to satisfy a request about one employee.
        outcome.record(
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
            "platform_user_erased",
            tenant_id=str(subject.tenant_id),
            user_id=str(subject.user_id),
            rows=outcome.total,
        )
        return outcome


class WorkspaceClosureService(_CountingService):
    """Close a whole workspace: every member, plus its marketplace credentials.

    Separate from platform-user erasure on purpose. This is the only scope
    permitted to delete a connection and its encrypted tokens, because those
    belong to the workspace rather than to whoever authorised them.
    """

    #: Connections deleted outright, ciphertext included. eBay is here — its
    #: omission from the previous version was the review's headline finding.
    CONNECTION_MODELS: Final[tuple[tuple[str, Any, Any], ...]] = (
        ("shopify_connections", ShopifyConnection, ShopifyConnection.tenant_id),
        ("aliexpress_connections", AliExpressConnection, AliExpressConnection.tenant_id),
        ("ebay_connections", EbayConnection, EbayConnection.tenant_id),
    )

    async def members(self, tenant_id: uuid.UUID) -> Sequence[uuid.UUID]:
        return (
            (await self._session.execute(select(User.id).where(User.tenant_id == tenant_id)))
            .scalars()
            .all()
        )

    async def plan(self, tenant_id: uuid.UUID) -> ErasureOutcome:
        outcome = ErasureOutcome(scope=ErasureScope.WORKSPACE)
        outcome.record("members", len(await self.members(tenant_id)))
        for label, model, tenant_column in self.CONNECTION_MODELS:
            outcome.record(
                label,
                await self._count(
                    select(func.count()).select_from(model).where(tenant_column == tenant_id)
                ),
            )
        return outcome

    async def erase(self, tenant_id: uuid.UUID) -> ErasureOutcome:
        """Erase every member, then delete the workspace's connections."""
        outcome = ErasureOutcome(scope=ErasureScope.WORKSPACE, executed=True)

        users = PlatformUserErasureService(self._session)
        for user_id in await self.members(tenant_id):
            member = await users.erase(SubjectRef(tenant_id=tenant_id, user_id=user_id))
            for name, count in member.counts.items():
                outcome.record(name, count)
            outcome.record("members", 1)

        # Only this scope destroys credentials.
        for label, model, tenant_column in self.CONNECTION_MODELS:
            outcome.record(
                f"{label}_deleted",
                await self._affected(delete(model).where(tenant_column == tenant_id)),
            )

        logger.info("workspace_closed", tenant_id=str(tenant_id), rows=outcome.total)
        return outcome
