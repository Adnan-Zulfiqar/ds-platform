"""Tests for the AliExpress client and signing.

No network and no credentials. Responses are supplied through
``httpx.MockTransport``, which exercises the real client code — including
parsing, error mapping, and retries — against a controlled server.

The behaviour under test is the client's contract with the rest of the
application: every failure arrives as a typed exception, and only failures that
could plausibly resolve themselves are retried.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.integrations.aliexpress import client as client_module
from app.integrations.aliexpress.auth import (
    build_signed_params,
    resolve_token_expiry,
    sign_request,
)
from app.integrations.aliexpress.client import AliExpressClient
from app.integrations.aliexpress.exceptions import (
    AliExpressAuthError,
    AliExpressRateLimitError,
    AliExpressResponseError,
    AliExpressTimeoutError,
    AliExpressTokenExpiredError,
    AliExpressUnavailableError,
)
from app.integrations.rate_limiter import RateLimitDecision, compute_backoff

pytestmark = pytest.mark.unit

APP_KEY = "test-app-key"
APP_SECRET = "test-app-secret-never-real"
TENANT = "11111111-1111-4111-8111-111111111111"


@pytest.fixture(autouse=True)
def _allow_all_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bypass the outbound rate limiter, which needs Redis.

    The limiter has its own tests; here it would only add a dependency and
    obscure what is being exercised.
    """

    async def _allow(self: Any, tenant_id: str) -> RateLimitDecision:
        return RateLimitDecision(allowed=True, remaining=99, retry_after_seconds=0)

    monkeypatch.setattr("app.integrations.rate_limiter.OutboundRateLimiter.acquire", _allow)


@pytest.fixture(autouse=True)
def _no_retry_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove backoff sleeps so retry paths run instantly."""

    async def _instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr(client_module.asyncio, "sleep", _instant)


def mock_client(handler: Any) -> Any:
    """Patch httpx.AsyncClient so the real client talks to a mock transport."""

    class _Patched(httpx.AsyncClient):
        def __init__(self, **kwargs: Any) -> None:
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(**kwargs)

    return _Patched


def build(access_token: str | None = "token") -> AliExpressClient:
    return AliExpressClient(
        app_key=APP_KEY, app_secret=APP_SECRET, tenant_id=TENANT, access_token=access_token
    )


class TestSigning:
    def test_signature_is_deterministic(self) -> None:
        params = {"method": "test", "app_key": APP_KEY, "timestamp": "1700000000000"}
        assert sign_request(params, app_secret=APP_SECRET) == sign_request(
            params, app_secret=APP_SECRET
        )

    def test_signature_changes_with_any_parameter(self) -> None:
        base = {"method": "test", "app_key": APP_KEY}
        assert sign_request(base, app_secret=APP_SECRET) != sign_request(
            {**base, "extra": "1"}, app_secret=APP_SECRET
        )

    def test_signature_changes_with_the_secret(self) -> None:
        """Otherwise the signature would authenticate nothing."""
        params = {"method": "test"}
        assert sign_request(params, app_secret="secret-a") != sign_request(
            params, app_secret="secret-b"
        )

    def test_parameter_order_does_not_matter(self) -> None:
        """Parameters are sorted before signing, so dict order is irrelevant."""
        assert sign_request({"b": "2", "a": "1"}, app_secret=APP_SECRET) == sign_request(
            {"a": "1", "b": "2"}, app_secret=APP_SECRET
        )

    def test_sign_and_sign_method_are_excluded(self) -> None:
        """`sign` cannot sign itself, and the gateway consumes `sign_method`."""
        base = {"method": "test"}
        assert sign_request(base, app_secret=APP_SECRET) == sign_request(
            {**base, "sign": "ignored", "sign_method": "sha256"}, app_secret=APP_SECRET
        )

    def test_signature_is_upper_hex(self) -> None:
        signature = sign_request({"a": "1"}, app_secret=APP_SECRET)
        assert signature == signature.upper()
        assert all(c in "0123456789ABCDEF" for c in signature)

    def test_the_secret_never_appears_in_the_signed_parameters(self) -> None:
        """The secret is an HMAC key, never a transmitted value."""
        signed = build_signed_params({"method": "test"}, app_key=APP_KEY, app_secret=APP_SECRET)
        assert APP_SECRET not in str(signed)

    def test_envelope_adds_the_required_fields(self) -> None:
        signed = build_signed_params({"method": "test"}, app_key=APP_KEY, app_secret=APP_SECRET)
        assert signed["app_key"] == APP_KEY
        assert signed["sign_method"] == "sha256"
        assert signed["timestamp"].isdigit()
        assert signed["sign"]


class TestTokenExpiryResolution:
    def test_expires_in_is_treated_as_a_duration(self) -> None:
        expiry = resolve_token_expiry(expires_in=3600, expire_time=None)
        assert expiry is not None

    def test_millisecond_timestamps_are_detected(self) -> None:
        """Treating milliseconds as seconds yields a date in 1970.

        A connection would then look permanently expired and refresh on every
        single request.
        """
        expiry = resolve_token_expiry(expires_in=None, expire_time=1_900_000_000_000)
        assert expiry is not None
        assert expiry.year > 2020

    def test_second_timestamps_still_work(self) -> None:
        expiry = resolve_token_expiry(expires_in=None, expire_time=1_900_000_000)
        assert expiry is not None
        assert expiry.year > 2020

    def test_missing_expiry_returns_none(self) -> None:
        """Callers must treat this as expired rather than as no expiry."""
        assert resolve_token_expiry(expires_in=None, expire_time=None) is None


class TestErrorMapping:
    async def test_expired_token_code_maps_to_token_expired(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"code": "27", "msg": "Invalid access token"})

        monkeypatch.setattr(client_module.httpx, "AsyncClient", mock_client(handler))

        with pytest.raises(AliExpressTokenExpiredError):
            await build().call("aliexpress.test")

    async def test_signature_error_maps_to_auth_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"code": "25", "msg": "Invalid signature"})

        monkeypatch.setattr(client_module.httpx, "AsyncClient", mock_client(handler))

        with pytest.raises(AliExpressAuthError):
            await build().call("aliexpress.test")

    async def test_quota_code_maps_to_rate_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"code": "7", "msg": "App call limit"})

        monkeypatch.setattr(client_module.httpx, "AsyncClient", mock_client(handler))

        with pytest.raises(AliExpressRateLimitError):
            await build().call("aliexpress.test")

    async def test_an_error_inside_http_200_is_still_an_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AliExpress reports failure inside 200 as often as via a status code.

        Trusting the status alone would mean storing a token that does not
        exist.
        """

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"code": "999", "msg": "Something failed"})

        monkeypatch.setattr(client_module.httpx, "AsyncClient", mock_client(handler))

        with pytest.raises(AliExpressResponseError):
            await build().call("aliexpress.test")

    async def test_code_zero_is_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Some endpoints return code "0" to mean success."""

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"code": "0", "result": {"ok": True}})

        monkeypatch.setattr(client_module.httpx, "AsyncClient", mock_client(handler))

        assert await build().call("aliexpress.test") == {
            "code": "0",
            "result": {"ok": True},
        }

    async def test_unparseable_body_maps_to_response_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>gateway error</html>")

        monkeypatch.setattr(client_module.httpx, "AsyncClient", mock_client(handler))

        with pytest.raises(AliExpressResponseError):
            await build().call("aliexpress.test")

    async def test_timeout_maps_to_timeout_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("too slow")

        monkeypatch.setattr(client_module.httpx, "AsyncClient", mock_client(handler))

        with pytest.raises(AliExpressTimeoutError):
            await build().call("aliexpress.test")

    async def test_missing_token_fails_before_any_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        called = False

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal called
            called = True
            return httpx.Response(200, json={})

        monkeypatch.setattr(client_module.httpx, "AsyncClient", mock_client(handler))

        with pytest.raises(AliExpressAuthError):
            await build(access_token=None).call("aliexpress.test")

        assert not called


class TestRetries:
    async def test_a_server_error_is_retried_then_raised(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        attempts = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(503)

        monkeypatch.setattr(client_module.httpx, "AsyncClient", mock_client(handler))

        with pytest.raises(AliExpressUnavailableError):
            await build().call("aliexpress.test")

        # One initial attempt plus the configured retries.
        assert attempts > 1

    async def test_a_transient_failure_that_recovers_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        attempts = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(503)
            return httpx.Response(200, json={"result": "ok"})

        monkeypatch.setattr(client_module.httpx, "AsyncClient", mock_client(handler))

        assert await build().call("aliexpress.test") == {"result": "ok"}
        assert attempts == 2

    async def test_an_auth_failure_is_not_retried(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A wrong secret is wrong every time.

        Retrying wastes quota and delays telling the user what to fix.
        """
        attempts = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(200, json={"code": "25", "msg": "Invalid signature"})

        monkeypatch.setattr(client_module.httpx, "AsyncClient", mock_client(handler))

        with pytest.raises(AliExpressAuthError):
            await build().call("aliexpress.test")

        assert attempts == 1


class TestBackoff:
    def test_backoff_grows_with_each_attempt(self) -> None:
        # Jittered, so compare ceilings across many samples rather than one pair.
        early = max(compute_backoff(0, base_seconds=1, max_seconds=60) for _ in range(200))
        late = max(compute_backoff(4, base_seconds=1, max_seconds=60) for _ in range(200))
        assert late > early

    def test_backoff_respects_the_ceiling(self) -> None:
        for _ in range(200):
            assert compute_backoff(20, base_seconds=1, max_seconds=30) <= 30

    def test_backoff_is_jittered(self) -> None:
        """Without jitter, every client retries in lockstep and re-floods a
        recovering provider."""
        samples = {compute_backoff(3, base_seconds=1, max_seconds=60) for _ in range(50)}
        assert len(samples) > 1
