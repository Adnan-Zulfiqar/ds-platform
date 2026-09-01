"""Confirm which database a destructive operation is about to touch.

An erasure script reads its connection from whichever `.env` happened to load.
That is fine until someone runs it from the wrong directory, or a deployment
leaves a production `.env` beside a staging checkout, and a rehearsal becomes a
live deletion. Configuration is not identity.

So the operator states the database they *expect*, and this asks the server
which one it is actually connected to. If the two disagree, nothing runs.

`current_database()` is deliberately a query rather than a parse of the URL: the
URL is the same string that was already wrong, and re-reading it proves nothing.
The answer comes from the server that would receive the DELETE.
"""

from __future__ import annotations

from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

__all__ = [
    "PRODUCTION_DATABASE_NAMES",
    "DatabaseIdentityError",
    "current_database",
    "require_database",
    "require_database_name",
]

#: Names that must never be touched without the explicit production flag. This
#: is a backstop, not the primary control — the expected-name check is.
PRODUCTION_DATABASE_NAMES: Final[frozenset[str]] = frozenset({"droppilot"})


class DatabaseIdentityError(RuntimeError):
    """The connected database is not the one the operator expected."""


async def current_database(session: AsyncSession) -> str:
    """Ask the server for its own name."""
    return str((await session.execute(text("SELECT current_database()"))).scalar_one())


def require_database_name(actual: str, *, expected: str, allow_production: bool = False) -> str:
    """The decision itself, with no opinion about how the name was obtained.

    Split out from `require_database` for the backup tooling, which reads
    `current_database()` over a synchronous psycopg connection because it is
    about to hand the same connection details to `pg_dump`. Two copies of this
    reasoning would be two places for the production-name backstop to be
    forgotten, and the one that gets forgotten is always the newer one.
    """
    if actual != expected:
        raise DatabaseIdentityError(
            f"Connected to {actual!r} but {expected!r} was expected. "
            "Nothing was changed. Check which environment file is loaded."
        )

    if actual in PRODUCTION_DATABASE_NAMES and not allow_production:
        raise DatabaseIdentityError(
            f"{actual!r} is a production database. Re-run with the explicit "
            "production flag if that is genuinely intended."
        )

    return actual


async def require_database(
    session: AsyncSession, *, expected: str, allow_production: bool = False
) -> str:
    """Refuse unless the connected database is exactly ``expected``.

    Returns the confirmed name so a caller can show it in a confirmation
    prompt — the operator should see the database they are about to change,
    named by the server rather than by their own shell.
    """
    return require_database_name(
        await current_database(session), expected=expected, allow_production=allow_production
    )
