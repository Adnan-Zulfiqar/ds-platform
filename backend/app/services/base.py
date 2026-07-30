"""Base service class.

Services hold business logic. They orchestrate repositories, enforce rules that
span more than one entity, and emit domain events. They do not build SQL and
they do not know that HTTP exists.

Dependencies arrive through the constructor rather than being imported or
constructed internally, which is what allows a unit test to pass a fake
repository and exercise a rule without a database.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import get_tenant_id, get_user_id
from app.core.logging import get_logger

if TYPE_CHECKING:
    import uuid

    from structlog.stdlib import BoundLogger


class BaseService:
    """Common behaviour for services.

    Holds the session so that a service can flush a unit of work spanning
    several repositories. It deliberately does not commit: the transaction
    boundary belongs to the request or task, so that a handler calling three
    services still produces one atomic change.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._logger: BoundLogger | None = None

    @property
    def logger(self) -> BoundLogger:
        """Logger bound to this service's name.

        Built lazily so that constructing a service in a tight loop costs
        nothing when it never logs.
        """
        if self._logger is None:
            self._logger = get_logger(type(self).__module__)
        return self._logger

    @property
    def tenant_id(self) -> uuid.UUID | None:
        return get_tenant_id()

    @property
    def current_user_id(self) -> uuid.UUID | None:
        return get_user_id()

    async def flush(self) -> None:
        """Push pending changes to the database without committing.

        Use when subsequent logic needs database-assigned values, or to surface
        a constraint violation at the point it is caused rather than at commit,
        where the offending statement is much harder to identify.
        """
        await self.session.flush()


__all__ = ["BaseService"]
