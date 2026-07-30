"""Shared pytest fixtures.

Environment variables are set *before* any application module is imported.
``app.core.config`` builds its settings singleton at import time, so importing
it first would bake in whatever configuration the developer's shell happened to
have — a classic source of tests that pass locally and fail in CI.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator

import pytest

# --- Must run before application imports -----------------------------------
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("POSTGRES_DB", "droppilot_test")
os.environ.setdefault("SECURITY_SECRET_KEY", "test-only-secret-key")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("LOG_JSON_OUTPUT", "false")
# ---------------------------------------------------------------------------

from app.core import context as ctx
from app.core.config import Environment, get_settings


@pytest.fixture(scope="session", autouse=True)
def _verify_test_environment() -> None:
    """Guard against running the suite against a non-test configuration.

    Cheap insurance: the integration tests truncate tables, and doing that to a
    developer's local development database — or worse — is a bad afternoon.
    """
    settings = get_settings()
    if settings.environment is not Environment.TEST:
        pytest.exit(
            f"Refusing to run: ENVIRONMENT is {settings.environment!r}, expected 'test'.",
            returncode=1,
        )


@pytest.fixture
def tenant_id() -> uuid.UUID:
    """A stable tenant identifier for a single test."""
    return uuid.uuid4()


@pytest.fixture
def other_tenant_id() -> uuid.UUID:
    """A second tenant, used to prove isolation between tenants."""
    return uuid.uuid4()


@pytest.fixture
def bound_tenant(tenant_id: uuid.UUID) -> Generator[uuid.UUID]:
    """Bind tenant context for the duration of a test, then unbind.

    Resetting via the token rather than setting ``None`` restores whatever was
    previously bound, so a nested use cannot clobber an outer one.
    """
    token = ctx.set_tenant_id(tenant_id)
    try:
        yield tenant_id
    finally:
        ctx._tenant_id.reset(token)


@pytest.fixture(autouse=True)
def _clear_context_between_tests() -> Generator[None]:
    """Guarantee no context leaks from one test into the next.

    Context leakage produces order-dependent tests, which are among the most
    expensive kinds of flake to diagnose.
    """
    yield
    ctx.clear_context()
