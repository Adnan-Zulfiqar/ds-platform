"""Tests for request-scoped context.

Context is the mechanism tenant isolation depends on, so its failure modes are
worth testing directly — particularly that a *missing* tenant raises rather than
returning ``None``, since a silent ``None`` reaching a repository would remove
the tenant filter entirely.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from app.core.context import (
    MissingTenantContextError,
    RequestContext,
    clear_context,
    get_request_id,
    get_tenant_id,
    require_tenant_id,
    set_request_id,
    set_tenant_id,
    set_user_id,
)

pytestmark = pytest.mark.unit


def test_tenant_id_round_trips() -> None:
    tenant = uuid.uuid4()
    set_tenant_id(tenant)
    assert get_tenant_id() == tenant


def test_require_tenant_id_raises_when_unbound() -> None:
    clear_context()
    with pytest.raises(MissingTenantContextError):
        require_tenant_id()


def test_require_tenant_id_returns_bound_value() -> None:
    tenant = uuid.uuid4()
    set_tenant_id(tenant)
    assert require_tenant_id() == tenant


def test_clear_context_removes_all_values() -> None:
    set_tenant_id(uuid.uuid4())
    set_user_id(uuid.uuid4())
    set_request_id("abc123")

    clear_context()

    assert get_tenant_id() is None
    assert get_request_id() is None


def test_request_context_serialises_and_restores() -> None:
    """A context must survive the round trip into a Celery task payload."""
    original = RequestContext(
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        request_id="req-1",
    )

    restored = RequestContext.from_dict(original.to_dict())

    assert restored == original


def test_request_context_handles_absent_values() -> None:
    empty = RequestContext(tenant_id=None, user_id=None, request_id=None)
    assert RequestContext.from_dict(empty.to_dict()) == empty


def test_bind_applies_snapshot_to_current_context() -> None:
    snapshot = RequestContext(tenant_id=uuid.uuid4(), user_id=uuid.uuid4(), request_id="req-2")
    snapshot.bind()

    assert get_tenant_id() == snapshot.tenant_id
    assert get_request_id() == "req-2"


async def test_context_is_isolated_between_concurrent_tasks() -> None:
    """Concurrent requests must not observe each other's tenant.

    This is the property that makes contextvars safe to use for a security
    boundary — a module-level global would fail this test, and the failure would
    be a cross-tenant data leak under concurrent load.
    """
    observed: dict[str, uuid.UUID | None] = {}

    async def worker(name: str, tenant: uuid.UUID) -> None:
        set_tenant_id(tenant)
        # Yield control so the other task interleaves here. Without the await
        # the tasks would run to completion sequentially and the test would
        # pass even with a broken implementation.
        await asyncio.sleep(0)
        observed[name] = get_tenant_id()

    first, second = uuid.uuid4(), uuid.uuid4()
    await asyncio.gather(worker("a", first), worker("b", second))

    assert observed == {"a": first, "b": second}
