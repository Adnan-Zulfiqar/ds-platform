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

from tests.environment import TEST_OTP_HMAC_KEY

# --- Must run before application imports -----------------------------------
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("POSTGRES_DB", "droppilot_test")
# At least 32 characters — the application refuses to start below the RFC 7518
# minimum for HS256, and that rule must be exercised by the tests, not bypassed.
os.environ.setdefault("SECURITY_SECRET_KEY", "test-only-secret-key-padded-to-satisfy-length-rule")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("LOG_JSON_OUTPUT", "false")
# The test client speaks plain HTTP, and a Secure cookie is not sent over an
# insecure connection — correct browser behaviour, but it would make every
# refresh-token test fail for a reason unrelated to what it is testing. This
# mirrors local HTTP development; deployed environments keep the default of
# true, which `Settings` does not allow to be weakened silently.
os.environ.setdefault("SECURITY_COOKIE_SECURE", "false")
# Two fixed Fernet keys so credential encryption works in tests, and so key
# rotation is exercised rather than assumed. Fixed rather than generated per run
# because a value encrypted in one test must be readable in another.
# These decode to the literal ASCII "test-key-N-NEVER-USE-IN-PROD-!!!", so a
# grep of a config file makes their nature obvious.
os.environ.setdefault(
    "SECURITY_ENCRYPTION_KEYS",
    "dGVzdC1rZXktMS1ORVZFUi1VU0UtSU4tUFJPRC0hISE=,dGVzdC1rZXktMi1ORVZFUi1VU0UtSU4tUFJPRC0hISE=",
)
# The OTP key is fixed by the harness rather than inherited from the shell —
# see `tests.environment` for why that mattered.
os.environ.setdefault("SECURITY_OTP_HMAC_KEY", TEST_OTP_HMAC_KEY)
# Isolated worktrees do not inherit a root `.env`. Google AUTH-G1 tests mock
# Google's verifier but still require a configured client id so the app does
# not short-circuit with "not configured". Synthetic only — not a production
# credential.
os.environ.setdefault(
    "GOOGLE_OAUTH_CLIENT_ID",
    "1000000000000-uxl2b-test.apps.googleusercontent.com",
)
# Isolated shells sometimes inherit a raised general quota from developer
# tooling. eBay C0 asserts the compliance budget is strictly above the general
# quota; pin the test default so that assertion is about product constants.
os.environ["SECURITY_RATE_LIMIT_REQUESTS"] = "100"
# Prompt mutation is off by default (audit A-03). The suite exercises create /
# version / activate, so tests opt in explicitly without weakening production.
os.environ.setdefault("AI_ALLOW_PROMPT_MUTATION", "true")
# ---------------------------------------------------------------------------

from app.core import context as ctx
from app.core.config import Environment, get_settings


# --- The suite must not read the developer's .env --------------------------
# Every settings group reads `.env` so that a real deployment picks up its
# configuration (see `_EnvFileSettings`). That is right for the application and
# wrong for the tests: the suite would then assert against whatever credentials
# happen to sit in one machine's file, and pass or fail accordingly. Two tests
# caught this immediately — a real `ALIEXPRESS_APP_KEY` made the
# "defaults are empty" assertions fail.
#
# So the file is switched off here, leaving `os.environ` above as the single
# source of test configuration. This is deliberately done in the harness rather
# than by teaching the application to detect pytest; production code should not
# know that tests exist.
#
# Individual tests may still pass `_env_file=` explicitly to exercise file
# loading — that argument overrides this.
def _disable_env_file_reads() -> None:
    from pydantic_settings import BaseSettings

    from app.core import config as config_module

    for obj in vars(config_module).values():
        if isinstance(obj, type) and issubclass(obj, BaseSettings):
            obj.model_config["env_file"] = None


_disable_env_file_reads()
# ---------------------------------------------------------------------------


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
