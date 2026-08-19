"""Generic repository base classes.

The repository layer is the only place in the application that builds SQL.
Services orchestrate business rules and call repositories; they never touch a
``Session``. That boundary is what makes services testable with a fake
repository instead of a live database.

**This module is the multi-tenancy security boundary.** Every tenant-scoped read
and write goes through :class:`TenantScopedRepository`, which injects a
``tenant_id`` predicate that subclasses cannot bypass by accident. The isolation
guarantee is only as good as the rule that nothing else constructs queries
against tenant-owned tables — enforce that in code review.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Generic, TypeVar, cast

from sqlalchemy import CursorResult, Select, func, or_, select
from sqlalchemy import update as sa_update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import require_tenant_id
from app.core.exceptions import ConflictError, DatabaseError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.models.base import IdentifiedBase
from app.schemas.common import ListQueryParams, SortDirection

logger = get_logger(__name__)

#: Bound to ``IdentifiedBase`` rather than ``Base`` so that ``model.id`` is a
#: known column to the type checker.
#:
#: A tighter bound for the tenant-scoped subclass was considered and rejected:
#: tenant ownership is expressed either by inheriting ``TenantMixin`` or by
#: declaring the column directly (``User`` does the latter, so that the
#: ``(tenant_id, email)`` uniqueness constraint can live on the model). No
#: single nominal base covers both, and Python has no intersection type to
#: express "identified *and* tenant-owned". The tenant column is therefore
#: resolved dynamically, and its presence is enforced by the test suite instead
#: of by the type checker.
ModelType = TypeVar("ModelType", bound=IdentifiedBase)


class BaseRepository(Generic[ModelType]):
    """Data access for a single model.

    Not tenant-aware. Use this only for platform-global tables such as
    ``tenants`` itself or reference data. Anything a customer owns must use
    :class:`TenantScopedRepository`.
    """

    #: Fields a client is permitted to sort by. An allowlist, not a denylist:
    #: ``sort_by`` arrives from the query string, and resolving an arbitrary
    #: client string to a column is how injection happens.
    sortable_fields: frozenset[str] = frozenset({"created_at", "updated_at"})

    #: Fields included in free-text search.
    searchable_fields: frozenset[str] = frozenset()

    #: Default ordering when the client specifies none. Stable ordering matters:
    #: without it, pagination can show the same row twice across pages.
    default_sort_field: str = "created_at"

    def __init__(self, session: AsyncSession, model: type[ModelType]) -> None:
        self.session = session
        self.model = model

    # -- Query construction -------------------------------------------------

    def _base_query(self) -> Select[tuple[ModelType]]:
        """Return the query every read starts from.

        Subclasses override this to add mandatory predicates. Overriding is the
        extension point that makes tenant scoping impossible to forget.
        """
        query = select(self.model)
        # Not every model is soft-deletable — reference tables are not — so the
        # column is resolved dynamically. ``getattr`` rather than an attribute
        # access because the type bound does not guarantee the column exists.
        deleted_at = getattr(self.model, "deleted_at", None)
        if deleted_at is not None:
            query = query.where(deleted_at.is_(None))
        return query

    def _apply_sorting(
        self, query: Select[tuple[ModelType]], params: ListQueryParams
    ) -> Select[tuple[ModelType]]:
        field_name = params.sort_by or self.default_sort_field

        if field_name not in self.sortable_fields:
            raise ValidationError(
                f"Cannot sort by {field_name!r}.",
                details={"allowed": sorted(self.sortable_fields)},
            )

        column = getattr(self.model, field_name)
        ordering = column.desc() if params.sort_dir is SortDirection.DESC else column.asc()

        # Tie-break on the primary key. Two rows sharing a created_at would
        # otherwise be ordered arbitrarily, and an arbitrary order across two
        # separate page queries can drop or duplicate rows.
        return query.order_by(ordering, self.model.id.desc())

    def _apply_search(
        self, query: Select[tuple[ModelType]], params: ListQueryParams
    ) -> Select[tuple[ModelType]]:
        """Apply free-text search across ``searchable_fields``.

        Uses case-insensitive ``LIKE``. This is adequate for the modest field
        counts in Phase 0 but does not use an index for leading-wildcard
        patterns. When product search becomes a primary workflow, move to a
        Postgres ``tsvector`` column with a GIN index; the call site does not
        change.
        """
        if not params.q or not self.searchable_fields:
            return query

        # Escape LIKE metacharacters so a literal % from the user does not
        # become a wildcard.
        escaped = params.q.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
        pattern = f"%{escaped}%"

        clauses = [
            getattr(self.model, field).ilike(pattern, escape="\\")
            for field in sorted(self.searchable_fields)
            if hasattr(self.model, field)
        ]
        return query.where(or_(*clauses)) if clauses else query

    def _apply_filters(
        self, query: Select[tuple[ModelType]], filters: dict[str, Any] | None
    ) -> Select[tuple[ModelType]]:
        """Apply exact-match filters.

        Only attributes that exist as mapped columns are honoured; unknown keys
        raise rather than being ignored, so a typo in a caller surfaces
        immediately instead of silently widening the result set.
        """
        if not filters:
            return query

        for field, value in filters.items():
            column = getattr(self.model, field, None)
            if column is None:
                raise ValidationError(f"Unknown filter field {field!r}.")
            # A list or tuple becomes an IN clause; a scalar an equality test.
            if isinstance(value, list | tuple):
                query = query.where(column.in_(value))
            else:
                query = query.where(column == value)
        return query

    # -- Reads --------------------------------------------------------------

    async def get_by_id(self, entity_id: uuid.UUID) -> ModelType | None:
        query = self._base_query().where(self.model.id == entity_id)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_by_id_or_raise(self, entity_id: uuid.UUID) -> ModelType:
        """Fetch by id, raising :class:`NotFoundError` when absent.

        For a tenant-scoped repository, a row belonging to another tenant is
        invisible and therefore reported as not found. That is deliberate — a
        403 would confirm the id exists and enable enumeration.
        """
        entity = await self.get_by_id(entity_id)
        if entity is None:
            raise NotFoundError.for_resource(self.model.__name__, entity_id)
        return entity

    async def list(
        self,
        params: ListQueryParams,
        *,
        filters: dict[str, Any] | None = None,
    ) -> tuple[Sequence[ModelType], int]:
        """Return one page of rows and the total count.

        The count runs as a separate statement against the same predicates. It
        is the expensive half on large tables; if it becomes a bottleneck,
        switch the UI to "load more" and drop the count rather than caching a
        number that goes stale immediately.
        """
        query = self._base_query()
        query = self._apply_filters(query, filters)
        query = self._apply_search(query, params)

        count_query = select(func.count()).select_from(query.subquery())
        total = (await self.session.execute(count_query)).scalar_one()

        query = self._apply_sorting(query, params)
        query = query.offset(params.offset).limit(params.limit)

        rows = (await self.session.execute(query)).scalars().all()
        return rows, total

    async def exists(self, entity_id: uuid.UUID) -> bool:
        query = select(func.count()).select_from(
            self._base_query().where(self.model.id == entity_id).subquery()
        )
        return bool((await self.session.execute(query)).scalar_one())

    async def count(self, *, filters: dict[str, Any] | None = None) -> int:
        query = self._apply_filters(self._base_query(), filters)
        count_query = select(func.count()).select_from(query.subquery())
        return int((await self.session.execute(count_query)).scalar_one())

    # -- Writes -------------------------------------------------------------

    async def create(self, **values: Any) -> ModelType:
        """Insert a row.

        Flushes but does not commit. The transaction is owned by the request or
        task, so that several repository calls compose into one atomic unit.
        """
        entity = self.model(**values)
        self.session.add(entity)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            await self.session.rollback()
            raise self._translate_integrity_error(exc) from exc
        except SQLAlchemyError as exc:
            await self.session.rollback()
            logger.exception("repository_create_failed", model=self.model.__name__)
            raise DatabaseError() from exc
        return entity

    async def update(self, entity: ModelType, **values: Any) -> ModelType:
        """Apply field updates to an already-fetched entity.

        Taking the entity rather than an id is deliberate: the caller must have
        fetched it through this repository, which means it already passed the
        tenant filter. An id-based update would have to re-derive that check and
        could be called with an id the caller never verified.
        """
        for field, value in values.items():
            if not hasattr(entity, field):
                raise ValidationError(f"Unknown field {field!r} on {self.model.__name__}.")
            setattr(entity, field, value)

        try:
            await self.session.flush()
        except IntegrityError as exc:
            await self.session.rollback()
            raise self._translate_integrity_error(exc) from exc
        except SQLAlchemyError as exc:
            await self.session.rollback()
            logger.exception("repository_update_failed", model=self.model.__name__)
            raise DatabaseError() from exc
        return entity

    async def soft_delete(self, entity: ModelType) -> ModelType:
        """Mark a row deleted without removing it."""
        if not hasattr(entity, "deleted_at"):
            raise TypeError(f"{self.model.__name__} does not support soft deletion.")
        entity.deleted_at = datetime.now(UTC)
        await self.session.flush()
        return entity

    async def restore(self, entity: ModelType) -> ModelType:
        if not hasattr(entity, "deleted_at"):
            raise TypeError(f"{self.model.__name__} does not support soft deletion.")
        entity.deleted_at = None
        await self.session.flush()
        return entity

    async def hard_delete(self, entity: ModelType) -> None:
        """Physically remove a row.

        Reserved for GDPR erasure and for tests. Ordinary deletion must use
        :meth:`soft_delete`, or restore requests and audit trails become
        impossible to satisfy.
        """
        await self.session.delete(entity)
        await self.session.flush()

    # -- Error translation --------------------------------------------------

    def _translate_integrity_error(self, exc: IntegrityError) -> Exception:
        """Convert a driver integrity error into a domain exception.

        Constraint names are matched rather than parsed out of driver message
        text, which is why ``NAMING_CONVENTION`` in ``models.base`` matters —
        deterministic names make this mapping reliable.
        """
        message = str(getattr(exc, "orig", exc))
        lowered = message.lower()

        # The driver's text goes to the log, never to the caller. `details`
        # is serialised into the error envelope, so putting it there returned
        # index names, column names and the offending values to any client
        # that could provoke a duplicate -- a description of the schema handed
        # out on request. The correlation id in the response is what ties a
        # report back to this log line.
        if any(marker in lowered for marker in ("unique", "duplicate key", "foreign key")) or (
            "check constraint" in lowered
        ):
            logger.warning(
                "repository_integrity_error",
                model=self.model.__name__,
                constraint=message[:200],
            )

        if "unique" in lowered or "duplicate key" in lowered:
            # Deliberately not the model class name: `GlobalRuleVersion` means
            # nothing to a merchant and names an internal type. Callers that
            # can say something more useful override this -- see
            # `GlobalRuleService._translate_integrity`.
            return ConflictError("Another record with these values already exists.")
        if "foreign key" in lowered:
            return ValidationError("The request references a resource that does not exist.")
        if "check constraint" in lowered:
            return ValidationError("The request violates a data integrity rule.")

        logger.exception("repository_integrity_error", model=self.model.__name__)
        return DatabaseError()


class TenantScopedRepository(BaseRepository[ModelType]):
    """Repository that confines every query to the current tenant.

    Two guarantees, both enforced here rather than at call sites:

    1. Reads are filtered by ``tenant_id``, inherited by every query built from
       :meth:`_base_query`.
    2. Writes have ``tenant_id`` stamped from context, and an attempt to write a
       different tenant's id is rejected rather than honoured.

    The tenant is read from ``contextvars`` rather than passed as an argument.
    An argument can be forgotten or supplied wrongly at any of hundreds of call
    sites; context is established once per request by middleware and cannot be
    omitted without :func:`require_tenant_id` raising.
    """

    async def _current_tenant_id(self) -> uuid.UUID:
        return require_tenant_id()

    def _base_query(self) -> Select[tuple[ModelType]]:
        query = super()._base_query()
        tenant_column = getattr(self.model, "tenant_id", None)
        if tenant_column is None:
            # A tenant-scoped repository over a model with no tenant column is a
            # wiring mistake that would otherwise return every tenant's rows.
            # Fail loudly at first use rather than leak.
            raise TypeError(
                f"{self.model.__name__} has no tenant_id column and cannot be "
                "used with TenantScopedRepository."
            )
        return query.where(tenant_column == require_tenant_id())

    async def create(self, **values: Any) -> ModelType:
        tenant_id = require_tenant_id()

        supplied = values.get("tenant_id")
        if supplied is not None and supplied != tenant_id:
            # A caller trying to write into another tenant is either a bug or an
            # attack. Either way it must fail loudly, never be silently
            # corrected to the current tenant.
            logger.error(
                "cross_tenant_write_attempt",
                model=self.model.__name__,
                supplied_tenant_id=str(supplied),
                context_tenant_id=str(tenant_id),
            )
            raise ConflictError("Cannot create a record for a different tenant.")

        values["tenant_id"] = tenant_id
        return await super().create(**values)

    async def update(self, entity: ModelType, **values: Any) -> ModelType:
        # tenant_id is immutable. Moving a row between tenants is not an
        # operation this platform supports, and permitting it through the
        # generic update path would make isolation depend on every caller
        # remembering not to pass it.
        values.pop("tenant_id", None)
        return await super().update(entity, **values)

    async def update_if_unmodified_since(
        self,
        entity_id: uuid.UUID,
        *,
        expected_updated_at: datetime,
        **values: Any,
    ) -> bool:
        """Atomic compare-and-swap: apply ``values`` only if ``updated_at``
        still equals ``expected_updated_at``.

        The platform's minimal optimistic-concurrency mechanism (M2A — see
        ``docs/dsers-parity/M2_PREMIUM_EDITOR.md``). Reusing the
        database-generated ``updated_at`` every tenant-scoped table already
        carries (``TimestampMixin``) needs no migration and stays accurate
        for free — Postgres bumps it in the same statement that applies the
        write. The guard lives entirely in the ``UPDATE``'s ``WHERE``
        clause, so the compare-and-write is one atomic database operation:
        two requests racing this call cannot both succeed, which a
        read-then-compare-in-Python approach could not guarantee.

        Returns whether the row was updated. Existence/tenant ownership is
        deliberately **not** checked here — a caller that needs to tell
        "does not exist" apart from "was changed by someone else" should
        fetch the row first (e.g. ``get_by_id_or_raise``, which already
        gives the correct 404 for a missing or foreign id) and treat a
        ``False`` return from this method as a genuine conflict, not a
        404.

        **Limitation, stated plainly:** this cannot distinguish "another
        editor changed this row" from "a background sync refreshed it" —
        both bump ``updated_at`` identically. That is the intended,
        conservative behaviour (any concurrent write invalidates a stale
        save) rather than a gap, but it does mean a save can occasionally
        be rejected by a routine re-sync rather than only by a true
        editor-vs-editor conflict.
        """
        if not values:
            return True

        for field in values:
            if not hasattr(self.model, field):
                raise ValidationError(f"Unknown field {field!r} on {self.model.__name__}.")

        tenant_column = getattr(self.model, "tenant_id", None)
        if tenant_column is None:
            raise TypeError(
                f"{self.model.__name__} has no tenant_id column and cannot be "
                "used with TenantScopedRepository."
            )

        conditions = [
            self.model.id == entity_id,
            tenant_column == require_tenant_id(),
            self.model.updated_at == expected_updated_at,
        ]
        deleted_at = getattr(self.model, "deleted_at", None)
        if deleted_at is not None:
            conditions.append(deleted_at.is_(None))

        # `updated_at` is set explicitly here rather than left for the
        # column's `onupdate=func.now()` to fire implicitly. That default
        # is reliable for an ORM-tracked `flush()`, but this statement is a
        # Core-style bulk UPDATE that never goes through unit-of-work
        # flush -- relying on the implicit path here left `updated_at`
        # unmoved after a successful write in testing, which would have
        # silently defeated the entire compare-and-swap (a "stale" second
        # write would find the version unchanged and succeed). Stating it
        # explicitly makes the version bump a guaranteed part of this exact
        # statement, not a hoped-for side effect. Popped from `values`
        # first so this method is always the sole authority over it, even
        # if a caller's dict happened to carry a same-named key.
        values.pop("updated_at", None)
        stmt = sa_update(self.model).where(*conditions).values(updated_at=func.now(), **values)
        result = await self.session.execute(stmt)
        # `rowcount` exists on the cursor result returned by UPDATE and DELETE,
        # but `execute` is typed as returning the general Result (same pattern
        # as `RefreshTokenRepository.revoke_all_for_user`).
        rowcount = cast("CursorResult[Any]", result).rowcount
        return bool(rowcount and rowcount > 0)


__all__ = ["BaseRepository", "ModelType", "TenantScopedRepository"]
