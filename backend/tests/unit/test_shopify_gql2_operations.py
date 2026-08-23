"""GQL-2 — shop authority and webhook operations over the shared client.

Every test drives the real operation layer and the real ``ShopifyGraphQLClient``
through ``httpx.MockTransport``. Only Shopify's network boundary is mocked; the
document, the parser, the retry policy, the pagination guards and the GID
validation are all production code.

No Shopify credential is used and no live request is made.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.core.exceptions import ValidationError
from app.integrations.shopify.exceptions import (
    ShopifyError,
    ShopifyGidError,
    ShopifyGraphQLError,
    ShopifyPaginationError,
    ShopifyResponseError,
    ShopifyThrottledError,
    ShopifyTimeoutError,
    ShopifyUserError,
)
from app.integrations.shopify.graphql import ShopifyGraphQLClient
from app.integrations.shopify.graphql_operations import (
    WEBHOOK_SUBSCRIPTION_CREATE_MUTATION,
    WEBHOOK_SUBSCRIPTIONS_QUERY,
    create_webhook_subscription,
    fetch_shop_authority,
    is_http_endpoint,
    list_webhook_subscriptions,
)

pytestmark = pytest.mark.unit

SHOP = "demo-shop.myshopify.com"
TOKEN = "shpat_TEST_TOKEN_NEVER_REAL"
URI = "https://app.example.test/api/v1/integrations/shopify/webhooks/products-create"


def build(handler: Any, *, max_attempts: int = 3, shop: str = SHOP) -> ShopifyGraphQLClient:
    return ShopifyGraphQLClient(
        shop_domain=shop,
        access_token=TOKEN,
        max_attempts=max_attempts,
        transport=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def gql(data: dict[str, Any], *, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json={"data": data}, headers={"X-Request-Id": "req-1"})


def subscription_node(
    *,
    gid: str = "gid://shopify/WebhookSubscription/1",
    topic: str = "PRODUCTS_CREATE",
    uri: str = URI,
    format_: str = "JSON",
    include: list[str] | None = None,
    filter_: str | None = None,
) -> dict[str, Any]:
    return {
        "id": gid,
        "topic": topic,
        "uri": uri,
        "format": format_,
        "includeFields": include or [],
        "filter": filter_,
    }


def page(nodes: list[dict[str, Any]], *, has_next: bool = False, cursor: str | None = None):
    return {
        "webhookSubscriptions": {
            "nodes": nodes,
            "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
        }
    }


# ------------------------------------------------------------ shop authority
class TestShopAuthority:
    async def test_a_valid_currency_code_is_returned_normalised(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            assert body["operationName"] == "ShopAuthority"
            assert "currencyCode" in body["query"]
            return gql({"shop": {"currencyCode": "gbp"}})

        authority = await fetch_shop_authority(build(handler))
        assert authority.currency_code == "GBP"

    async def test_the_query_is_sent_to_the_pinned_version(self) -> None:
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return gql({"shop": {"currencyCode": "USD"}})

        await fetch_shop_authority(build(handler))
        assert seen["url"] == f"https://{SHOP}/admin/api/2026-07/graphql.json"

    @pytest.mark.parametrize(
        "data",
        [
            {},
            {"shop": None},
            {"shop": "not-an-object"},
            {"shop": []},
        ],
    )
    async def test_a_missing_or_malformed_shop_fails_closed(self, data: dict[str, Any]) -> None:
        with pytest.raises(ShopifyResponseError):
            await fetch_shop_authority(build(lambda _r: gql(data)))

    @pytest.mark.parametrize("code", [None, "", "   ", 840, ["GBP"]])
    async def test_a_missing_or_malformed_currency_fails_closed(self, code: Any) -> None:
        with pytest.raises(ShopifyResponseError):
            await fetch_shop_authority(build(lambda _r: gql({"shop": {"currencyCode": code}})))

    @pytest.mark.parametrize("code", ["GB", "GBPP", "12£", "G8P"])
    async def test_a_currency_the_money_layer_refuses_is_an_explicit_failure(
        self, code: str
    ) -> None:
        """Syntactically a string, but not something this system can price in.

        The failure names the offending code rather than storing it, because a
        currency nothing downstream understands would surface much later as an
        unexplained pricing error.
        """
        with pytest.raises(ValidationError) as raised:
            await fetch_shop_authority(build(lambda _r: gql({"shop": {"currencyCode": code}})))
        assert code in str(raised.value) or "currency" in str(raised.value).lower()

    async def test_there_is_no_usd_fallback(self) -> None:
        """A store silently assumed to sell in USD would misprice a catalogue."""
        for data in ({}, {"shop": {}}, {"shop": {"currencyCode": None}}):
            with pytest.raises((ShopifyResponseError, ValidationError)):
                await fetch_shop_authority(build(lambda _r, d=data: gql(d)))

    async def test_top_level_errors_fail_closed(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "data": {"shop": None},
                    "errors": [{"message": "nope", "extensions": {"code": "X"}}],
                },
            )

        with pytest.raises(ShopifyGraphQLError):
            await fetch_shop_authority(build(handler))

    async def test_a_transport_error_surfaces(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("dns", request=request)

        with pytest.raises(ShopifyError):
            await fetch_shop_authority(build(handler, max_attempts=1))

    async def test_the_query_is_retried_when_throttled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """It is parsed as a query, so bounded transient retry applies."""
        import asyncio

        async def no_sleep(_s: float) -> None:
            return None

        monkeypatch.setattr(asyncio, "sleep", no_sleep)
        attempts = {"n": 0}

        def handler(_request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            if attempts["n"] == 1:
                return httpx.Response(429, json={}, headers={"Retry-After": "1"})
            return gql({"shop": {"currencyCode": "EUR"}})

        authority = await fetch_shop_authority(build(handler))
        assert attempts["n"] == 2
        assert authority.currency_code == "EUR"

    async def test_each_shop_sends_its_own_token(self) -> None:
        seen: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.url.host, request.headers["x-shopify-access-token"]))
            return gql({"shop": {"currencyCode": "USD"}})

        transport = httpx.MockTransport(handler)
        for domain, token in (("one.myshopify.com", "tok-1"), ("two.myshopify.com", "tok-2")):
            client = ShopifyGraphQLClient(
                shop_domain=domain,
                access_token=token,
                transport=httpx.AsyncClient(transport=transport),
            )
            await fetch_shop_authority(client)
        assert seen == [("one.myshopify.com", "tok-1"), ("two.myshopify.com", "tok-2")]


# ------------------------------------------------------------ webhook listing
class TestWebhookListing:
    async def test_an_empty_list_is_valid(self) -> None:
        records = await list_webhook_subscriptions(build(lambda _r: gql(page([]))))
        assert records == []

    async def test_a_single_page_parses(self) -> None:
        records = await list_webhook_subscriptions(
            build(lambda _r: gql(page([subscription_node()])))
        )
        assert len(records) == 1
        assert records[0].topic == "PRODUCTS_CREATE"
        assert records[0].uri == URI
        assert records[0].format == "JSON"

    async def test_the_complete_gid_round_trips(self) -> None:
        raw = "gid://shopify/WebhookSubscription/1234567890"
        records = await list_webhook_subscriptions(
            build(lambda _r: gql(page([subscription_node(gid=raw)])))
        )
        assert records[0].gid.value == raw
        assert records[0].id == raw
        assert records[0].gid.resource == "WebhookSubscription"

    async def test_every_page_is_walked(self) -> None:
        pages = [
            page(
                [subscription_node(gid=f"gid://shopify/WebhookSubscription/{i}")],
                has_next=True,
                cursor=f"c{i}",
            )
            for i in range(1, 4)
        ]
        pages.append(page([subscription_node(gid="gid://shopify/WebhookSubscription/4")]))
        calls = {"n": 0}
        cursors: list[Any] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            cursors.append(body["variables"]["after"])
            response = gql(pages[calls["n"]])
            calls["n"] += 1
            return response

        records = await list_webhook_subscriptions(build(handler))
        assert len(records) == 4, "first-page-only would have returned 1"
        assert cursors == [None, "c1", "c2", "c3"]

    async def test_more_than_one_full_page_is_handled(self) -> None:
        """250 is Shopify's connection ceiling; the walker must not stop at one page."""
        big = [
            subscription_node(gid=f"gid://shopify/WebhookSubscription/{i}") for i in range(1, 251)
        ]
        responses = [page(big, has_next=True, cursor="c1"), page(big)]
        calls = {"n": 0}

        def handler(_request: httpx.Request) -> httpx.Response:
            response = gql(responses[calls["n"]])
            calls["n"] += 1
            return response

        records = await list_webhook_subscriptions(build(handler), page_size=250)
        assert len(records) == 500

    async def test_a_repeated_cursor_is_refused(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return gql(page([subscription_node()], has_next=True, cursor="same"))

        with pytest.raises(ShopifyPaginationError):
            await list_webhook_subscriptions(build(handler))

    async def test_a_missing_end_cursor_is_refused(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return gql(page([subscription_node()], has_next=True, cursor=None))

        with pytest.raises(ShopifyPaginationError):
            await list_webhook_subscriptions(build(handler))

    async def test_the_page_budget_is_enforced(self) -> None:
        calls = {"n": 0}

        def handler(_request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return gql(page([subscription_node()], has_next=True, cursor=f"c{calls['n']}"))

        with pytest.raises(ShopifyPaginationError, match="page budget"):
            await list_webhook_subscriptions(build(handler), max_pages=3)

    async def test_the_node_budget_is_enforced(self) -> None:
        nodes = [
            subscription_node(gid=f"gid://shopify/WebhookSubscription/{i}") for i in range(1, 11)
        ]

        def handler(_request: httpx.Request) -> httpx.Response:
            return gql(page(nodes, has_next=True, cursor="c1"))

        with pytest.raises(ShopifyPaginationError, match="node budget"):
            await list_webhook_subscriptions(build(handler), max_nodes=5)

    @pytest.mark.parametrize(
        "broken",
        [
            {"id": "gid://shopify/WebhookSubscription/1", "topic": "T", "format": "JSON"},
            {"id": "gid://shopify/WebhookSubscription/1", "uri": URI, "format": "JSON"},
            {"id": "gid://shopify/WebhookSubscription/1", "topic": "T", "uri": URI},
            {"topic": "T", "uri": URI, "format": "JSON"},
            "not-an-object",
        ],
    )
    async def test_a_malformed_node_fails_closed(self, broken: Any) -> None:
        """Skipping it would make the reconciler think a topic is unregistered."""
        with pytest.raises((ShopifyResponseError, ShopifyGidError)):
            await list_webhook_subscriptions(build(lambda _r: gql(page([broken]))))

    async def test_a_wrong_resource_gid_is_refused(self) -> None:
        node = subscription_node(gid="gid://shopify/Product/1")
        with pytest.raises(ShopifyGidError):
            await list_webhook_subscriptions(build(lambda _r: gql(page([node]))))

    @pytest.mark.parametrize(
        ("uri", "http"),
        [
            (URI, True),
            ("http://localhost:8000/api/v1/integrations/shopify/webhooks/x", True),
            ("pubsub://my-project:my-topic", False),
            ("arn:aws:events:us-east-1::event-source/aws.partner/shopify.com/1/x", False),
        ],
    )
    async def test_non_http_endpoints_are_represented_not_coerced(
        self, uri: str, http: bool
    ) -> None:
        """A Pub/Sub URI or an EventBridge ARN is preserved verbatim.

        Parsing one as a URL is what would make it silently never match, so the
        reconciler would create an HTTPS duplicate beside it.
        """
        records = await list_webhook_subscriptions(
            build(lambda _r: gql(page([subscription_node(uri=uri)])))
        )
        assert records[0].uri == uri
        assert is_http_endpoint(records[0]) is http

    async def test_the_document_selects_uri_not_the_deprecated_fields(self) -> None:
        """2026-07 deprecates `callbackUrl` and the `endpoint` union."""
        assert "uri" in WEBHOOK_SUBSCRIPTIONS_QUERY
        assert "callbackUrl" not in WEBHOOK_SUBSCRIPTIONS_QUERY
        assert "endpoint" not in WEBHOOK_SUBSCRIPTIONS_QUERY

    async def test_no_merchant_or_customer_data_is_requested(self) -> None:
        for forbidden in ("customer", "order", "email", "address", "product("):
            assert forbidden not in WEBHOOK_SUBSCRIPTIONS_QUERY.lower()


# ----------------------------------------------------------- webhook creation
class TestWebhookCreation:
    def created(self, node: dict[str, Any] | None, errors: list[dict[str, Any]] | None = None):
        def handler(_request: httpx.Request) -> httpx.Response:
            return gql(
                {
                    "webhookSubscriptionCreate": {
                        "webhookSubscription": node,
                        "userErrors": errors or [],
                    }
                }
            )

        return handler

    async def test_a_successful_create_is_validated_against_the_request(self) -> None:
        result = await create_webhook_subscription(
            build(self.created(subscription_node())),
            topic_enum="PRODUCTS_CREATE",
            uri=URI,
        )
        assert result.record.topic == "PRODUCTS_CREATE"
        assert result.record.uri == URI
        assert result.record.gid.resource == "WebhookSubscription"
        assert result.request_id == "req-1"

    async def test_the_request_carries_topic_and_input_as_variables(self) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return gql(
                {
                    "webhookSubscriptionCreate": {
                        "webhookSubscription": subscription_node(),
                        "userErrors": [],
                    }
                }
            )

        await create_webhook_subscription(build(handler), topic_enum="PRODUCTS_CREATE", uri=URI)
        assert seen["operationName"] == "WebhookSubscriptionCreate"
        assert seen["variables"]["topic"] == "PRODUCTS_CREATE"
        assert seen["variables"]["webhookSubscription"] == {"uri": URI, "format": "JSON"}
        assert URI not in seen["query"], "the URI must travel as a variable"

    async def test_user_errors_are_a_typed_failure(self) -> None:
        handler = self.created(None, [{"field": ["uri"], "message": "is invalid"}])
        with pytest.raises(ShopifyUserError):
            await create_webhook_subscription(build(handler), topic_enum="PRODUCTS_CREATE", uri=URI)

    async def test_a_response_without_user_errors_selected_is_refused(self) -> None:
        """Silence cannot mean success."""

        def handler(_request: httpx.Request) -> httpx.Response:
            return gql({"webhookSubscriptionCreate": {"webhookSubscription": subscription_node()}})

        with pytest.raises(ShopifyGraphQLError):
            await create_webhook_subscription(build(handler), topic_enum="PRODUCTS_CREATE", uri=URI)

    async def test_no_errors_but_no_subscription_is_refused(self) -> None:
        with pytest.raises(ShopifyGraphQLError):
            await create_webhook_subscription(
                build(self.created(None)), topic_enum="PRODUCTS_CREATE", uri=URI
            )

    async def test_a_malformed_created_object_is_refused(self) -> None:
        with pytest.raises((ShopifyResponseError, ShopifyGidError)):
            broken = {"id": "not-a-gid", "topic": "T", "uri": URI, "format": "JSON"}
            await create_webhook_subscription(
                build(self.created(broken)),
                topic_enum="PRODUCTS_CREATE",
                uri=URI,
            )

    @pytest.mark.parametrize(
        ("field", "node"),
        [
            ("topic", subscription_node(topic="ORDERS_CREATE")),
            ("uri", subscription_node(uri="https://elsewhere.test/hook")),
            ("format", subscription_node(format_="XML")),
        ],
    )
    async def test_a_returned_mismatch_is_refused(self, field: str, node: dict[str, Any]) -> None:
        """Shopify confirming *a* subscription is not confirming *this* one."""
        with pytest.raises(ShopifyGraphQLError) as raised:
            await create_webhook_subscription(
                build(self.created(node)), topic_enum="PRODUCTS_CREATE", uri=URI
            )
        assert field in str(raised.value).lower() or field in json.dumps(
            raised.value.details, default=str
        )

    async def test_the_mutation_is_never_retried_on_timeout(self) -> None:
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            raise httpx.ReadTimeout("slow", request=request)

        with pytest.raises(ShopifyTimeoutError):
            await create_webhook_subscription(
                build(handler, max_attempts=5), topic_enum="PRODUCTS_CREATE", uri=URI
            )
        assert attempts["n"] == 1, "a replay could create a duplicate subscription"

    @pytest.mark.parametrize("status", [429, 502, 503, 504])
    async def test_the_mutation_is_never_retried_on_a_transient_status(self, status: int) -> None:
        """No sleep is patched out here on purpose.

        If the mutation were ever made retryable, this test would both fail its
        assertion and visibly slow down -- two independent signals.
        """
        attempts = {"n": 0}

        def handler(_request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            return httpx.Response(status, json={})

        with pytest.raises((ShopifyThrottledError, ShopifyResponseError)):
            await create_webhook_subscription(
                build(handler, max_attempts=5), topic_enum="PRODUCTS_CREATE", uri=URI
            )
        assert attempts["n"] == 1

    async def test_the_mutation_document_selects_user_errors(self) -> None:
        assert "userErrors" in WEBHOOK_SUBSCRIPTION_CREATE_MUTATION
        assert "field" in WEBHOOK_SUBSCRIPTION_CREATE_MUTATION
        assert "message" in WEBHOOK_SUBSCRIPTION_CREATE_MUTATION
