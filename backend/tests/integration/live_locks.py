"""Observing PostgreSQL lock contention, rather than timing it.

``pg_blocking_pids()`` is PostgreSQL's own answer to "is this backend waiting on
somebody else's lock", so a test can wait for that *fact* instead of for a sleep
that is generous enough today and flaky on a loaded machine tomorrow.

Extracted from the GQL-2 harness when the eBay token-refresh tests needed the
same rendezvous: neither helper knows anything about Shopify or eBay, and two
copies of a concurrency primitive is how one of them quietly stops matching the
other. ``shopify_gql2_live`` re-exports both, so existing imports still work.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

#: Upper bound on how long a helper waits for a state that should arrive in
#: milliseconds. Reaching it means something is genuinely stuck — a failure
#: worth seeing rather than a slow machine to accommodate.
HANG_GUARD_SECONDS = 30.0


async def backend_pid(session: AsyncSession) -> int:
    """The PostgreSQL backend serving this session.

    Reading it also forces the session to check out a real connection, which is
    what makes it meaningful to ask whether that connection is blocked.
    """
    return int((await session.execute(sa.text("SELECT pg_backend_pid()"))).scalar_one())


async def wait_until_blocked(factory: Callable[[], AsyncSession], pid: int) -> list[int]:
    """Wait for PostgreSQL to report ``pid`` waiting on somebody else's lock.

    Polling this turns a race into a rendezvous: the test moves on the instant
    contention is real, and fails outright if contention never happens — which
    is the failure mode that would otherwise let a serialisation test pass
    because the two callers never actually overlapped.
    """
    deadline = time.monotonic() + HANG_GUARD_SECONDS
    while time.monotonic() < deadline:
        async with factory() as observer:
            blockers = (
                await observer.execute(sa.text("SELECT pg_blocking_pids(:p)"), {"p": pid})
            ).scalar_one()
        if blockers:
            return list(blockers)
        await asyncio.sleep(0.01)
    raise AssertionError(f"backend {pid} never blocked — no lock contention occurred")


__all__ = ["HANG_GUARD_SECONDS", "backend_pid", "wait_until_blocked"]
