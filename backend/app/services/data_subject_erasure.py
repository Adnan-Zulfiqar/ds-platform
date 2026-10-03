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

from redis.exceptions import RedisError
from sqlalchemy import CursorResult, Executable, Select, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.redis import CacheClient, CacheError, RedisPurpose, get_redis
from app.models.ai_prompt import AIPrompt, PromptExecution
from app.models.ebay import EbayConnection
from app.models.email_verification import EmailVerificationToken
from app.models.identity import UserIdentity
from app.models.integration import AliExpressConnection
from app.models.inventory import InventorySyncRun
from app.models.invitation import UserInvitation
from app.models.notification import Notification, NotificationEmailPreference
from app.models.order import OrderSyncRun
from app.models.pipeline_bulk import PipelineBulkRun, PipelineBulkRunCancelRequest
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
    "CacheCleanupResult",
    "ErasureCacheCleanup",
    "ErasureExecution",
    "ErasureOutcome",
    "ErasureScope",
    "PlatformUserErasureService",
    "SubjectRef",
    "SubjectResolutionError",
    "WorkspaceClosureService",
    "execute_platform_user_erasure",
    "execute_workspace_closure",
    "login_email_key",
    "login_ip_key",
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
    # Track E3: which kinds this person wanted by email. Personal, and
    # meaningless without them.
    UserReference(
        "notification_email_preferences",
        NotificationEmailPreference,
        NotificationEmailPreference.user_id,
        "delete",
        NotificationEmailPreference.tenant_id,
    ),
    # A federated identity exists only so one person can sign in. Leaving it
    # behind would keep a Google subject pointing at an erased account — and
    # would let that Google account sign back into it.
    UserReference("user_identities", UserIdentity, UserIdentity.user_id, "delete", None),
    # Workspace-owned rows that merely name who acted.
    # Track E4: an invitation names who sent it and whom it became; the
    # workspace keeps the record, the person's link to it is cleared.
    UserReference(
        "user_invitations",
        UserInvitation,
        UserInvitation.invited_by_user_id,
        "clear",
        UserInvitation.tenant_id,
    ),
    UserReference(
        "user_invitations",
        UserInvitation,
        UserInvitation.accepted_user_id,
        "clear",
        UserInvitation.tenant_id,
    ),
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
    UserReference(
        "pipeline_bulk_runs",
        PipelineBulkRun,
        PipelineBulkRun.requested_by_user_id,
        "clear",
        PipelineBulkRun.tenant_id,
    ),
    UserReference(
        "pipeline_bulk_run_cancel_requests",
        PipelineBulkRunCancelRequest,
        PipelineBulkRunCancelRequest.requested_by_user_id,
        "clear",
        PipelineBulkRunCancelRequest.tenant_id,
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


def _throttle_digest(value: str) -> str:
    """The digest `app/services/login_throttle.py` builds its keys from."""
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()[:32]


def login_email_key(email: str) -> str:
    """`login:email:{sha256(normalised_email)[:32]}` — exactly addressable.

    The throttle hashes the address before using it as a key, so an erasure can
    target the subject's counter precisely without a scan, a pattern, or an
    address anywhere in the key space.
    """
    return f"login:email:{_throttle_digest(email)}"


def login_ip_key(client_ip: str) -> str:
    """`login:ip:{sha256(client_ip)[:32]}` — documented, never erased.

    The throttle keeps a second counter per source address. It is **not**
    deleted during erasure and deliberately so: an address is not a person. It
    is shared by everyone behind a NAT, reassigned by ISPs, and a subject may
    have signed in from many. Deleting "their" IP counter would mean guessing
    which addresses were theirs and clearing throttling for whoever else is
    behind them.

    It needs no erasure step because it expires on its own: 300 seconds
    (`login_attempt_window_seconds`), extended to 900 (`login_lockout_seconds`)
    once the attempt limit is reached. This function exists so the inventory is
    complete and testable, not because anything calls it to delete.
    """
    return f"login:ip:{_throttle_digest(client_ip)}"


@dataclass(frozen=True, slots=True)
class CacheCleanupResult:
    """What the post-commit Redis step managed to do.

    Separate from `ErasureOutcome` on purpose. The database erasure either
    committed or it did not; the cache cleanup is a second, independent store
    with no shared transaction, and conflating the two would let a Redis outage
    be reported as a failed erasure — or, worse, let a failed erasure be
    reported as done because the cache cleared.
    """

    attempted: bool
    succeeded: bool
    keys_deleted: int
    detail: str

    @property
    def pending(self) -> bool:
        return self.attempted and not self.succeeded


class ErasureCacheCleanup:
    """Redis cleanup, run **only after** the database transaction has committed.

    **There is no cross-store atomicity here and the code does not pretend
    otherwise.** PostgreSQL and Redis cannot commit together. Calling Redis
    inside the database transaction would mean a rolled-back erasure had already
    cleared the cache; calling it before the commit would mean the same. So it
    runs afterwards, once the database outcome is known and durable.

    If Redis then fails, the erasure has still happened and is still correct.
    The caller reports `database erasure complete; cache cleanup pending` and
    exits non-zero, and a retry finishes the job — the operations are idempotent
    and a second run deletes whatever the first could not.

    Cached values are copies of data that has just been deleted, all carrying a
    300-second default TTL, so the exposure from a delayed cleanup is bounded
    even before the retry.
    """

    def __init__(
        self, cache: CacheClient | None = None, throttle_client: Any | None = None
    ) -> None:
        self._cache = cache or CacheClient()
        # `login:*` and `ratelimit:*` live in the RATE_LIMIT database; the tenant
        # namespace lives in CACHE. Deleting from the wrong index silently
        # succeeds and removes nothing.
        self._throttle = throttle_client if throttle_client is not None else None

    def _throttle_or_default(self) -> Any:
        return self._throttle if self._throttle is not None else get_redis(RedisPurpose.RATE_LIMIT)

    async def invalidate_workspace(self, tenant_id: uuid.UUID) -> CacheCleanupResult:
        """Clear `t:{tenant}:*`, and only that tenant's keys.

        Delegates to the existing `CacheClient.invalidate_tenant`, which walks
        the namespace with `SCAN` rather than `KEYS` — `KEYS` blocks Redis for a
        full keyspace walk. `SCAN` with `MATCH` has been available since Redis
        2.8, so this works on the deployed 3.0.504. Nothing here uses `FLUSHDB`,
        which would destroy every tenant's cache to clean up one.
        """
        try:
            deleted = await self._cache.invalidate_tenant(str(tenant_id))
        except (CacheError, RedisError) as exc:
            logger.warning("workspace_cache_invalidate_failed", tenant_id=str(tenant_id))
            return CacheCleanupResult(
                attempted=True,
                succeeded=False,
                keys_deleted=0,
                detail=f"tenant cache invalidation failed: {type(exc).__name__}",
            )
        logger.info("workspace_cache_invalidated", tenant_id=str(tenant_id), keys=deleted)
        return CacheCleanupResult(
            attempted=True, succeeded=True, keys_deleted=deleted, detail="tenant cache cleared"
        )

    async def forget_login_attempts(self, email: str) -> CacheCleanupResult:
        """Delete the subject's exact `login:email:` counter. Nothing else.

        The address never reaches a log line, and neither does the key — a key
        is a hash of the address, and publishing hashes of low-entropy values is
        how you build a lookup table for them.
        """
        key = login_email_key(email)
        try:
            deleted = int(await self._throttle_or_default().delete(key))
        except RedisError as exc:
            logger.warning("login_counter_delete_failed", error=type(exc).__name__)
            return CacheCleanupResult(
                attempted=True,
                succeeded=False,
                keys_deleted=0,
                detail=f"login counter delete failed: {type(exc).__name__}",
            )
        logger.info("login_counter_deleted", keys=deleted)
        return CacheCleanupResult(
            attempted=True, succeeded=True, keys_deleted=deleted, detail="login counter cleared"
        )


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


@dataclass(frozen=True, slots=True)
class ErasureExecution:
    """The two-store result: what the database did, and what Redis then did."""

    outcome: ErasureOutcome
    cache: CacheCleanupResult

    @property
    def fully_complete(self) -> bool:
        return not self.cache.pending

    def describe(self) -> str:
        if self.cache.pending:
            return (
                f"{self.outcome.describe()}\n"
                "database erasure complete; cache cleanup pending — "
                f"{self.cache.detail}"
            )
        return f"{self.outcome.describe()}\n{self.cache.detail} ({self.cache.keys_deleted} key(s))"


async def execute_workspace_closure(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    cleanup: ErasureCacheCleanup | None = None,
) -> ErasureExecution:
    """Close a workspace: erase, **commit**, and only then clear the cache.

    The ordering is the whole point of this function existing rather than the
    caller doing it inline.

    * Redis is never touched while the transaction is open. A rolled-back
      erasure that had already cleared the cache would be a silent
      inconsistency in the direction that matters least, but the reverse — a
      committed erasure whose cache still serves the deleted rows — is the one
      that leaks data, and neither is acceptable.
    * If the database fails, the transaction rolls back and **Redis is not
      called at all**. There is nothing to clean up.
    * If Redis fails after the commit, the erasure stands. The result says so
      plainly, and the caller exits non-zero so nobody records the request as
      finished.

    The cache step runs **unconditionally** once the commit succeeds, including
    when the erasure deleted nothing. A retry after a Redis outage is exactly
    that case: the database work is already done, the counts are all zero, and
    the cache still needs clearing.
    """
    outcome = await WorkspaceClosureService(session).erase(tenant_id)
    await session.commit()

    # Past this line the database work is durable. Nothing below may change it.
    cache = await (cleanup or ErasureCacheCleanup()).invalidate_workspace(tenant_id)
    return ErasureExecution(outcome=outcome, cache=cache)


async def execute_platform_user_erasure(
    session: AsyncSession,
    subject: SubjectRef,
    *,
    email: str | None = None,
    cleanup: ErasureCacheCleanup | None = None,
) -> ErasureExecution:
    """Erase one user, commit, then delete their exact login counter.

    Same ordering rule as workspace closure. When the subject was resolved by id
    rather than by address there is no address to derive the key from, so the
    cache step is skipped and reported as not attempted — the counter expires on
    its own within 900 seconds.
    """
    outcome = await PlatformUserErasureService(session).erase(subject)
    await session.commit()

    if email is None:
        return ErasureExecution(
            outcome=outcome,
            cache=CacheCleanupResult(
                attempted=False,
                succeeded=True,
                keys_deleted=0,
                detail="no address supplied; login counter left to expire",
            ),
        )
    cache = await (cleanup or ErasureCacheCleanup()).forget_login_attempts(email)
    return ErasureExecution(outcome=outcome, cache=cache)
