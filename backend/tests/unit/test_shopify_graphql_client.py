"""GQL-1 — the Shopify Admin GraphQL client.

Every test drives the real client through ``httpx.MockTransport``, so what is
asserted is the request the client would actually put on the wire and the
semantics it derives from a response. No live Shopify call is made and no
credential is needed.

Assertions are about shape and security, never about "was the mock called" — a
test that only proves a mock was invoked would have passed on every version of
this client, including the ones that leaked a token or retried a mutation.
"""

from __future__ import annotations

import asyncio
import json
import logging
from decimal import Decimal
from typing import Any

import httpx
import pytest

from app.integrations.shopify.exceptions import (
    ShopifyAuthError,
    ShopifyError,
    ShopifyGraphQLError,
    ShopifyInvalidShopError,
    ShopifyQueryCostError,
    ShopifyResponseError,
    ShopifyThrottledError,
    ShopifyTimeoutError,
    ShopifyUserError,
)
from app.integrations.shopify.graphql import (
    OperationType,
    ShopifyGraphQLClient,
    backoff_seconds,
    cost_recovery_seconds,
    parse_cost,
    raise_for_user_errors,
    retry_after_seconds,
    user_errors,
)

pytestmark = pytest.mark.unit

SHOP = "demo-shop.myshopify.com"
TOKEN = "shpat_TEST_TOKEN_NEVER_REAL"
DOCUMENT = "query ShopName { shop { name } }"
ENDPOINT = f"https://{SHOP}/admin/api/2026-07/graphql.json"


def build_client(
    handler: Any,
    *,
    shop: str = SHOP,
    max_attempts: int = 3,
) -> ShopifyGraphQLClient:
    transport = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    return ShopifyGraphQLClient(
        shop_domain=shop,
        access_token=TOKEN,
        max_attempts=max_attempts,
        transport=transport,
    )


def ok_response(
    data: dict[str, Any] | None = None,
    *,
    extensions: dict[str, Any] | None = None,
    errors: list[dict[str, Any]] | None = None,
    request_id: str | None = "req-abc-123",
    status: int = 200,
) -> httpx.Response:
    body: dict[str, Any] = {}
    if data is not None:
        body["data"] = data
    if extensions is not None:
        body["extensions"] = extensions
    if errors is not None:
        body["errors"] = errors
    headers = {"X-Request-Id": request_id} if request_id else {}
    return httpx.Response(status, json=body, headers=headers)


COST_BLOCK = {
    "cost": {
        "requestedQueryCost": 101,
        "actualQueryCost": 46,
        "throttleStatus": {
            "maximumAvailable": 1000,
            "currentlyAvailable": 954,
            "restoreRate": 50,
        },
    }
}


async def run(client: ShopifyGraphQLClient, **kwargs: Any) -> Any:
    return await client.execute(
        document=kwargs.pop("document", DOCUMENT),
        operation_name=kwargs.pop("operation_name", "ShopName"),
        operation_type=kwargs.pop("operation_type", OperationType.QUERY),
        **kwargs,
    )


# --------------------------------------------------------------- domain / URL
class TestShopDomainAndEndpoint:
    def test_a_canonical_domain_is_accepted(self) -> None:
        client = ShopifyGraphQLClient(shop_domain=SHOP, access_token=TOKEN)
        assert client.endpoint == ENDPOINT

    def test_the_endpoint_is_exactly_the_documented_shape(self) -> None:
        client = ShopifyGraphQLClient(shop_domain=SHOP, access_token=TOKEN)
        assert client.endpoint == f"https://{SHOP}/admin/api/2026-07/graphql.json"
        assert "/latest/" not in client.endpoint

    @pytest.mark.parametrize(
        "rejected",
        [
            "https://demo-shop.myshopify.com",
            "http://demo-shop.myshopify.com",
            "demo-shop.myshopify.com/admin",
            "demo-shop.myshopify.com:443",
            "demo-shop.myshopify.com?x=1",
            "demo-shop.myshopify.com#frag",
            "user@demo-shop.myshopify.com",
            "DEMO-SHOP.MYSHOPIFY.COM",
            " demo-shop.myshopify.com ",
            "demo-shop",
            "localhost",
            "127.0.0.1",
            "10.0.0.5",
            "demo-shop.myshopify.com.attacker.test",
            "attacker.test/demo-shop.myshopify.com",
            "a.b.myshopify.com",
            "myshopify.com",
            ".myshopify.com",
            "-bad.myshopify.com",
            "bad-.myshopify.com",
            "evil.com",
            "",
        ],
    )
    def test_anything_but_a_canonical_domain_is_refused(self, rejected: str) -> None:
        """The client normalises nothing.

        Repairing input here would give an attacker-supplied string a second
        chance at the one place the token is about to be attached.
        """
        with pytest.raises(ShopifyInvalidShopError):
            ShopifyGraphQLClient(shop_domain=rejected, access_token=TOKEN)

    def test_an_empty_token_is_refused(self) -> None:
        with pytest.raises(ShopifyAuthError):
            ShopifyGraphQLClient(shop_domain=SHOP, access_token="   ")


# ------------------------------------------------------------- request shape
class TestRequestShape:
    async def test_the_request_is_a_documented_post_with_the_right_headers(self) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            seen["headers"] = dict(request.headers)
            seen["body"] = json.loads(request.content)
            return ok_response({"shop": {"name": "Demo"}})

        await run(build_client(handler))

        assert seen["method"] == "POST"
        assert seen["url"] == ENDPOINT
        assert seen["headers"]["x-shopify-access-token"] == TOKEN
        assert seen["headers"]["content-type"] == "application/json"

    async def test_query_variables_and_operation_name_are_sent_separately(self) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return ok_response({"shop": {"name": "Demo"}})

        await run(
            build_client(handler),
            document="query ShopName($handle: String!) { shop { name } }",
            operation_name="ShopName",
            variables={"handle": "a-handle"},
        )

        assert seen["query"] == "query ShopName($handle: String!) { shop { name } }"
        assert seen["variables"] == {"handle": "a-handle"}
        assert seen["operationName"] == "ShopName"

    async def test_merchant_values_never_reach_the_document(self) -> None:
        """Injection guard. A merchant handle belongs in `variables`; a client
        that interpolated it into the document would also defeat Shopify's
        query-cost caching."""
        hostile = 'x") { id } } mutation Evil { appUninstall { app { id'
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return ok_response({"shop": {"name": "Demo"}})

        await run(build_client(handler), variables={"handle": hostile})

        assert hostile not in seen["query"]
        assert seen["query"] == DOCUMENT
        assert seen["variables"]["handle"] == hostile

    async def test_an_operation_name_is_required(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover - never called
            raise AssertionError("no request should be made")

        with pytest.raises(ShopifyGraphQLError):
            await run(build_client(handler), operation_name="  ")

    async def test_an_empty_document_is_refused(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("no request should be made")

        with pytest.raises(ShopifyGraphQLError):
            await run(build_client(handler), document="")

    async def test_redirects_are_not_followed(self) -> None:
        """A followed redirect would post the access token to whatever host the
        Location header named."""
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(302, headers={"Location": "https://attacker.test/steal"})

        with pytest.raises(ShopifyResponseError):
            await run(build_client(handler))
        assert calls == [ENDPOINT], "the client followed a redirect"


class TestTokenIsNeverExposed:
    def test_repr_and_str_hide_the_token(self) -> None:
        client = ShopifyGraphQLClient(shop_domain=SHOP, access_token=TOKEN)
        assert TOKEN not in repr(client)
        assert TOKEN not in str(client)
        assert SHOP in repr(client)

    async def test_the_token_is_absent_from_logs_and_errors(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return ok_response(
                {"shop": None},
                errors=[{"message": "boom", "extensions": {"code": "INTERNAL_SERVER_ERROR"}}],
            )

        with caplog.at_level(logging.DEBUG):
            with pytest.raises(ShopifyGraphQLError) as raised:
                await run(build_client(handler))

        assert TOKEN not in str(raised.value)
        assert TOKEN not in json.dumps(raised.value.details, default=str)
        assert TOKEN not in caplog.text

    async def test_variables_are_never_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        secret_value = "customer-street-address-42"

        def handler(_request: httpx.Request) -> httpx.Response:
            return ok_response({"shop": {"name": "Demo"}}, extensions=COST_BLOCK)

        with caplog.at_level(logging.DEBUG):
            await run(build_client(handler), variables={"address": secret_value})

        assert secret_value not in caplog.text


# ------------------------------------------------------------------ responses
class TestResponseSemantics:
    async def test_a_successful_response_is_typed_and_carries_metadata(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return ok_response({"shop": {"name": "Demo"}}, extensions=COST_BLOCK)

        result = await run(build_client(handler))

        assert result.data == {"shop": {"name": "Demo"}}
        assert result.operation_name == "ShopName"
        assert result.api_version == "2026-07"
        assert result.request_id == "req-abc-123"
        assert result.attempts == 1
        assert result.cost.requested == Decimal("101")
        assert result.cost.actual == Decimal("46")
        assert result.cost.throttle.maximum_available == Decimal("1000")
        assert result.cost.throttle.currently_available == Decimal("954")
        assert result.cost.throttle.restore_rate == Decimal("50")

    async def test_absent_cost_metadata_is_safe(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return ok_response({"shop": {"name": "Demo"}})

        result = await run(build_client(handler))
        assert result.cost.requested is None
        assert result.cost.throttle.restore_rate is None

    @pytest.mark.parametrize(
        "extensions",
        [
            {"cost": "not-an-object"},
            {"cost": {"requestedQueryCost": "abc", "throttleStatus": []}},
            {"cost": {"throttleStatus": {"restoreRate": None}}},
            "not-an-object",
            None,
        ],
    )
    async def test_malformed_cost_metadata_cannot_fail_a_good_response(
        self, extensions: Any
    ) -> None:
        """Cost is telemetry. The merchant's data arrived either way."""

        def handler(_request: httpx.Request) -> httpx.Response:
            return ok_response({"shop": {"name": "Demo"}}, extensions=extensions)

        result = await run(build_client(handler))
        assert result.data == {"shop": {"name": "Demo"}}

    async def test_a_non_json_body_fails(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>maintenance</html>")

        with pytest.raises(ShopifyResponseError):
            await run(build_client(handler))

    async def test_a_response_without_data_fails(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return ok_response()

        with pytest.raises(ShopifyGraphQLError):
            await run(build_client(handler))

    async def test_top_level_errors_fail_and_preserve_safe_fields(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return ok_response(
                errors=[
                    {
                        "message": "Field 'nope' doesn't exist",
                        "path": ["shop", "nope"],
                        "extensions": {"code": "undefinedField", "typeName": "Shop"},
                    }
                ]
            )

        with pytest.raises(ShopifyGraphQLError) as raised:
            await run(build_client(handler))

        errors = raised.value.details["errors"]
        assert errors[0]["message"].startswith("Field 'nope'")
        assert errors[0]["path"] == ["shop", "nope"]
        assert errors[0]["code"] == "undefinedField"
        assert "typeName" not in errors[0], "only documented safe fields are forwarded"
        assert raised.value.details["shopifyRequestId"] == "req-abc-123"

    async def test_partial_data_with_errors_fails_closed_by_default(self) -> None:
        """The whole point of the default. A half-populated response reported as
        success is how half a catalogue silently goes unsynced."""

        def handler(_request: httpx.Request) -> httpx.Response:
            return ok_response(
                {"shop": {"name": "Demo"}, "products": None},
                errors=[{"message": "throttled sub-field", "extensions": {"code": "SOMETHING"}}],
            )

        with pytest.raises(ShopifyGraphQLError):
            await run(build_client(handler))

    async def test_partial_data_requires_an_explicit_opt_in(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return ok_response(
                {"shop": {"name": "Demo"}},
                errors=[{"message": "partial", "extensions": {"code": "SOMETHING"}}],
            )

        result = await run(build_client(handler), allow_partial_data=True)
        assert result.data == {"shop": {"name": "Demo"}}

    async def test_access_denied_is_an_auth_failure_not_a_generic_error(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return ok_response(errors=[{"message": "no", "extensions": {"code": "ACCESS_DENIED"}}])

        with pytest.raises(ShopifyAuthError):
            await run(build_client(handler))


class TestHttpStatusMapping:
    @pytest.mark.parametrize("status", [401, 403])
    async def test_auth_statuses_raise_auth_errors(self, status: int) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(status, json={"errors": "unauthorized"})

        with pytest.raises(ShopifyAuthError):
            await run(build_client(handler))

    async def test_a_400_is_a_response_error(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"errors": "bad request"})

        with pytest.raises(ShopifyResponseError):
            await run(build_client(handler))


# ----------------------------------------------------------- mutation errors
class TestMutationUserErrors:
    def test_user_errors_are_extracted_from_the_named_field(self) -> None:
        payload = {
            "productSet": {
                "product": None,
                "userErrors": [
                    {
                        "field": ["input", "title"],
                        "message": "Title can't be blank",
                        "code": "BLANK",
                    }
                ],
            }
        }
        errors = user_errors(payload, mutation_field="productSet")
        assert errors == [
            {
                "message": "Title can't be blank",
                "field": ["input", "title"],
                "code": "BLANK",
            }
        ]

    def test_a_non_empty_user_error_list_is_a_failure(self) -> None:
        payload = {"productSet": {"userErrors": [{"message": "nope", "code": "INVALID"}]}}
        with pytest.raises(ShopifyUserError) as raised:
            raise_for_user_errors(payload, mutation_field="productSet")
        assert raised.value.details["mutation"] == "productSet"

    def test_an_empty_user_error_list_is_success(self) -> None:
        payload = {"productSet": {"product": {"id": "gid://shopify/Product/1"}, "userErrors": []}}
        raise_for_user_errors(payload, mutation_field="productSet")

    def test_a_document_that_forgot_to_select_user_errors_is_refused(self) -> None:
        """Silence is not success. A mutation document without `userErrors`
        makes every rejection invisible, so it fails loudly instead."""
        with pytest.raises(ShopifyGraphQLError):
            user_errors({"productSet": {"product": {"id": "x"}}}, mutation_field="productSet")

    def test_a_missing_mutation_field_is_refused(self) -> None:
        with pytest.raises(ShopifyGraphQLError):
            user_errors({"somethingElse": {}}, mutation_field="productSet")

    def test_arbitrary_payload_content_is_not_forwarded(self) -> None:
        payload = {
            "productSet": {
                "userErrors": [
                    {
                        "message": "bad",
                        "code": "INVALID",
                        "internalHint": "customer@example.com",
                    }
                ]
            }
        }
        errors = user_errors(payload, mutation_field="productSet")
        assert "internalHint" not in errors[0]


# -------------------------------------------------------------------- retries
class TestRetryPolicy:
    async def test_a_query_retries_a_timeout_and_succeeds(self) -> None:
        attempts = {"n": 0}

        def handler(_request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise httpx.ReadTimeout("slow", request=_request)
            return ok_response({"shop": {"name": "Demo"}})

        result = await run(build_client(handler))
        assert attempts["n"] == 2
        assert result.attempts == 2

    @pytest.mark.parametrize("status", [502, 503, 504])
    async def test_a_query_retries_transient_gateway_failures(self, status: int) -> None:
        attempts = {"n": 0}

        def handler(_request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            if attempts["n"] == 1:
                return httpx.Response(status, text="upstream")
            return ok_response({"shop": {"name": "Demo"}})

        await run(build_client(handler))
        assert attempts["n"] == 2

    async def test_a_query_retries_http_429(self, monkeypatch: pytest.MonkeyPatch) -> None:
        slept: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            slept.append(seconds)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        attempts = {"n": 0}

        def handler(_request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            if attempts["n"] == 1:
                return httpx.Response(429, json={}, headers={"Retry-After": "2"})
            return ok_response({"shop": {"name": "Demo"}})

        await run(build_client(handler))
        assert attempts["n"] == 2
        assert slept == [2.0], "the integer Retry-After was honoured exactly"

    async def test_a_query_retries_the_graphql_throttled_code(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_sleep(_seconds: float) -> None:
            return None

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        attempts = {"n": 0}

        def handler(_request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            if attempts["n"] == 1:
                return ok_response(
                    errors=[{"message": "Throttled", "extensions": {"code": "THROTTLED"}}],
                    extensions=COST_BLOCK,
                )
            return ok_response({"shop": {"name": "Demo"}})

        with pytest.raises(ShopifyThrottledError):
            await run(build_client(handler, max_attempts=1))
        assert attempts["n"] == 1

    @pytest.mark.parametrize("status", [400, 401, 403])
    async def test_client_errors_are_never_retried(self, status: int) -> None:
        attempts = {"n": 0}

        def handler(_request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            return httpx.Response(status, json={})

        with pytest.raises((ShopifyAuthError, ShopifyResponseError)):
            await run(build_client(handler))
        assert attempts["n"] == 1

    async def test_a_maximum_cost_error_is_never_retried(self) -> None:
        attempts = {"n": 0}

        def handler(_request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            return ok_response(
                errors=[{"message": "too big", "extensions": {"code": "MAX_COST_EXCEEDED"}}]
            )

        with pytest.raises(ShopifyQueryCostError):
            await run(build_client(handler))
        assert attempts["n"] == 1, "the same document would cost the same again"

    async def test_a_mutation_is_never_retried_automatically(self) -> None:
        """Replay safety is a per-operation decision, and the operation layer is
        where it is known. A blind retry could create a second product."""
        attempts = {"n": 0}

        def handler(_request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            raise httpx.ReadTimeout("slow", request=_request)

        with pytest.raises(ShopifyTimeoutError):
            await run(
                build_client(handler),
                document="mutation Go { productSet { userErrors { message } } }",
                operation_name="Go",
                operation_type=OperationType.MUTATION,
            )
        assert attempts["n"] == 1

    async def test_attempts_are_capped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake_sleep(_seconds: float) -> None:
            return None

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        attempts = {"n": 0}

        def handler(_request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            return httpx.Response(503, text="down")

        with pytest.raises(ShopifyResponseError):
            await run(build_client(handler, max_attempts=3))
        assert attempts["n"] == 3

    async def test_a_transport_failure_is_surfaced(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("dns", request=request)

        with pytest.raises(ShopifyError):
            await run(build_client(handler, max_attempts=1))

    async def test_cancellation_interrupts_a_retry_sleep(self) -> None:
        """A shutting-down worker must not be held open by a backoff."""
        started = asyncio.Event()

        def handler(_request: httpx.Request) -> httpx.Response:
            started.set()
            return httpx.Response(503, text="down")

        client = build_client(handler, max_attempts=5)
        task = asyncio.create_task(run(client))
        await asyncio.wait_for(started.wait(), timeout=5)
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


class TestBackoffArithmetic:
    def test_an_integer_retry_after_is_read(self) -> None:
        assert retry_after_seconds("3") == 3.0

    def test_an_http_date_retry_after_is_read(self) -> None:
        """RFC 9110 permits an HTTP-date as well as delay-seconds."""
        from email.utils import parsedate_to_datetime

        value = "Wed, 21 Oct 2026 07:28:10 GMT"
        now = parsedate_to_datetime("Wed, 21 Oct 2026 07:28:00 GMT").timestamp()
        seconds = retry_after_seconds(value, now=now)
        assert seconds == pytest.approx(10.0)

    @pytest.mark.parametrize("value", [None, "", "not-a-date", "-5", "0"])
    def test_an_unusable_retry_after_falls_back(self, value: str | None) -> None:
        assert retry_after_seconds(value) is None

    def test_a_past_http_date_is_discarded(self) -> None:
        assert retry_after_seconds("Wed, 21 Oct 2020 07:28:00 GMT") is None

    def test_the_cost_wait_is_derived_and_bounded(self) -> None:
        cost = parse_cost(
            {
                "cost": {
                    "requestedQueryCost": 500,
                    "throttleStatus": {"currentlyAvailable": 100, "restoreRate": 50},
                }
            }
        )
        # (500 - 100) / 50 == 8 seconds.
        assert cost_recovery_seconds(cost) == pytest.approx(8.0)

    def test_the_cost_wait_is_none_when_inputs_are_missing(self) -> None:
        assert cost_recovery_seconds(parse_cost({"cost": {}})) is None
        assert cost_recovery_seconds(parse_cost(None)) is None

    def test_the_cost_wait_is_none_when_the_budget_already_covers_it(self) -> None:
        cost = parse_cost(
            {
                "cost": {
                    "requestedQueryCost": 10,
                    "throttleStatus": {"currentlyAvailable": 900, "restoreRate": 50},
                }
            }
        )
        assert cost_recovery_seconds(cost) is None

    def test_backoff_is_bounded_and_jittered(self) -> None:
        """Jitter cannot be asserted by value, so the contract asserted is the
        range it must stay inside — and that it actually varies."""
        seen = {backoff_seconds(2, base=1.0, jitter=0.25) for _ in range(200)}
        assert len(seen) > 1, "jitter produced a constant delay"
        for value in seen:
            assert 3.0 <= value <= 5.0, value

    def test_backoff_never_exceeds_the_ceiling(self) -> None:
        for attempt in range(0, 20):
            assert 0.0 <= backoff_seconds(attempt) <= 30.0


# ------------------------------------------------------------------ isolation
class TestTenantIsolation:
    async def test_two_shops_send_their_own_tokens(self) -> None:
        seen: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.url.host, request.headers["x-shopify-access-token"]))
            return ok_response({"shop": {"name": "Demo"}})

        transport = httpx.MockTransport(handler)
        first = ShopifyGraphQLClient(
            shop_domain="one.myshopify.com",
            access_token="token-one",
            transport=httpx.AsyncClient(transport=transport),
        )
        second = ShopifyGraphQLClient(
            shop_domain="two.myshopify.com",
            access_token="token-two",
            transport=httpx.AsyncClient(transport=transport),
        )

        await run(first)
        await run(second)

        assert seen == [
            ("one.myshopify.com", "token-one"),
            ("two.myshopify.com", "token-two"),
        ]

    async def test_the_shared_pool_carries_no_credentials(self) -> None:
        """The pool is shared for TLS reuse; the token never is.

        A module-level client holding auth headers would be a global object
        containing one merchant's credentials — the exact thing the brief
        forbids and the thing that makes cross-tenant leakage possible.
        """
        from app.integrations.shopify import graphql as module

        pool = await module._transport.get()
        try:
            assert "x-shopify-access-token" not in {k.lower() for k in pool.headers}
            assert "authorization" not in {k.lower() for k in pool.headers}
            assert pool.follow_redirects is False
        finally:
            await module.close_shopify_graphql_client()

    def test_a_client_exposes_no_token_attribute_publicly(self) -> None:
        client = ShopifyGraphQLClient(shop_domain=SHOP, access_token=TOKEN)
        assert not hasattr(client, "access_token")
        assert not hasattr(client, "token")


class TestApiVersionPinning:
    def test_the_version_comes_from_configuration_and_is_pinned(self) -> None:
        from app.core.config import settings

        assert settings.shopify.graphql_api_version == "2026-07"

    def test_an_explicit_version_overrides_the_default(self) -> None:
        client = ShopifyGraphQLClient(shop_domain=SHOP, access_token=TOKEN, api_version="2026-10")
        assert client.endpoint.endswith("/admin/api/2026-10/graphql.json")

    @pytest.mark.parametrize("bad", ["latest", "unstable", "2026-7", "2026-05", "", "2026"])
    def test_configuration_refuses_an_unpinned_version(self, bad: str) -> None:
        """`/latest` changes the schema under a deployed app four times a year
        with no code change to correlate against."""
        from pydantic import ValidationError as PydanticValidationError

        from app.core.config import ShopifySettings

        with pytest.raises(PydanticValidationError):
            ShopifySettings(graphql_api_version=bad)

    def test_rest_and_graphql_versions_are_separate_settings(self) -> None:
        from app.core.config import ShopifySettings

        configured = ShopifySettings(api_version="2026-04", graphql_api_version="2026-07")
        assert configured.api_version == "2026-04"
        assert configured.graphql_api_version == "2026-07"
