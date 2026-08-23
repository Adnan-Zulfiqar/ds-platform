"""GQL-2 — webhook reconciliation behaviour.

These tests drive the real ``WebhookReconciler`` against a counting Shopify
double. Every mutation the reconciler issues is counted, so "exactly one create"
and "never a blind replay" are asserted on observed traffic rather than on a
mock's call log.

No Shopify credential is used and no live request is made.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.core.config import Environment, settings
from app.integrations.shopify.exceptions import (
    ShopifyWebhookConfigError,
    ShopifyWebhookTopicError,
)
from app.integrations.shopify.gid import parse_gid
from app.integrations.shopify.graphql import ShopifyGraphQLClient
from app.integrations.shopify.graphql_operations import WebhookSubscriptionRecord
from app.integrations.shopify.webhook_reconciliation import (
    TOPIC_ENUM,
    DesiredWebhook,
    ReconcileStatus,
    WebhookReconciler,
    desired_subscriptions,
    normalise_delivery_uri,
    topic_enum,
    validate_delivery_uri,
)

pytestmark = pytest.mark.unit

SHOP = "demo-shop.myshopify.com"
TOKEN = "shpat_TEST_TOKEN_NEVER_REAL"
BASE = "https://app.example.test/api/v1/integrations/shopify/webhooks"


def uri_for(topic: str) -> str:
    return f"{BASE}/{topic.replace('/', '-')}"


def spec(topic: str = "products/create", **kwargs: Any) -> DesiredWebhook:
    return DesiredWebhook(topic=topic, uri=kwargs.pop("uri", uri_for(topic)), **kwargs)


def record(
    *,
    topic: str = "PRODUCTS_CREATE",
    uri: str | None = None,
    format_: str = "JSON",
    gid: str = "gid://shopify/WebhookSubscription/1",
    include: tuple[str, ...] = (),
    filter_: str | None = None,
) -> WebhookSubscriptionRecord:
    return WebhookSubscriptionRecord(
        gid=parse_gid(gid, expected_resource="WebhookSubscription"),
        topic=topic,
        uri=uri if uri is not None else uri_for("products/create"),
        format=format_,
        include_fields=include,
        filter=filter_,
    )


class FakeShopify:
    """A counting Shopify double at the HTTP boundary.

    Deliberately not a mock of the reconciler's collaborators: the reconciler
    talks to the real operation layer, the real client, the real parser. What is
    faked is only what Shopify would put on the wire — so a test asserting
    ``creates == 1`` is asserting that one mutation left the process.
    """

    def __init__(
        self,
        existing: list[dict[str, Any]] | None = None,
        *,
        create_behaviour: str = "ok",
    ) -> None:
        self.existing = list(existing or [])
        self.create_behaviour = create_behaviour
        self.creates: list[dict[str, Any]] = []
        self.lists = 0
        self.paths: list[str] = []
        self._next_id = 100

    def node(self, *, topic: str, uri: str, format_: str = "JSON") -> dict[str, Any]:
        self._next_id += 1
        return {
            "id": f"gid://shopify/WebhookSubscription/{self._next_id}",
            "topic": topic,
            "uri": uri,
            "format": format_,
            "includeFields": [],
            "filter": None,
        }

    def seed(self, *, topic: str, uri: str, format_: str = "JSON") -> dict[str, Any]:
        node = self.node(topic=topic, uri=uri, format_=format_)
        self.existing.append(node)
        return node

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        body = json.loads(request.content)
        name = body.get("operationName")
        if name == "WebhookSubscriptions":
            self.lists += 1
            return httpx.Response(
                200,
                json={
                    "data": {
                        "webhookSubscriptions": {
                            "nodes": list(self.existing),
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        }
                    }
                },
            )
        if name == "WebhookSubscriptionCreate":
            variables = body["variables"]
            self.creates.append(variables)
            if self.create_behaviour == "timeout":
                raise httpx.ReadTimeout("shopify went quiet", request=request)
            if self.create_behaviour == "user_error":
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "webhookSubscriptionCreate": {
                                "webhookSubscription": None,
                                "userErrors": [
                                    {"field": ["topic"], "message": "requires read_orders"}
                                ],
                            }
                        }
                    },
                )
            if self.create_behaviour == "throttled":
                return httpx.Response(429, json={})
            node = self.node(
                topic=variables["topic"],
                uri=variables["webhookSubscription"]["uri"],
                format_=variables["webhookSubscription"]["format"],
            )
            # Shopify's own state changes, so a later list sees it. This is what
            # makes "re-list before deciding" a real recovery mechanism here and
            # not a comment.
            self.existing.append(node)
            return httpx.Response(
                200,
                json={
                    "data": {
                        "webhookSubscriptionCreate": {
                            "webhookSubscription": node,
                            "userErrors": [],
                        }
                    }
                },
            )
        raise AssertionError(f"unexpected operation {name!r}")

    def client(self) -> ShopifyGraphQLClient:
        return ShopifyGraphQLClient(
            shop_domain=SHOP,
            access_token=TOKEN,
            transport=httpx.AsyncClient(transport=httpx.MockTransport(self.handler)),
        )

    def reconciler(self) -> WebhookReconciler:
        return WebhookReconciler(self.client())


# ------------------------------------------------------------- topic mapping
class TestTopicMapping:
    def test_every_registered_topic_is_mapped(self) -> None:
        from app.integrations.shopify.service import WEBHOOK_TOPICS

        assert set(WEBHOOK_TOPICS) <= set(TOPIC_ENUM), (
            "a topic the app registers with no GraphQL mapping would fail at runtime"
        )

    def test_an_unknown_topic_fails_locally(self) -> None:
        """Before a network call, not as a rejected mutation."""
        with pytest.raises(ShopifyWebhookTopicError):
            topic_enum("carts/create")

    def test_the_mapping_is_explicit_not_derived(self) -> None:
        for enum_member in TOPIC_ENUM.values():
            assert enum_member.isupper()
            assert "/" not in enum_member


# ------------------------------------------------------------ URI comparison
class TestUriComparison:
    @pytest.mark.parametrize(
        ("left", "right"),
        [
            ("https://a.test/hook", "https://A.TEST/hook"),
            ("HTTPS://a.test/hook", "https://a.test/hook"),
            ("https://a.test/hook/", "https://a.test/hook"),
        ],
    )
    def test_only_case_and_a_trailing_slash_are_insignificant(self, left: str, right: str) -> None:
        assert normalise_delivery_uri(left) == normalise_delivery_uri(right)

    @pytest.mark.parametrize(
        ("left", "right"),
        [
            ("https://a.test/webhooks/orders-create", "https://a.test/webhooks/orders-updated"),
            ("https://a.test/hook", "https://b.test/hook"),
            ("https://a.test/hook", "https://a.test:8443/hook"),
            ("https://a.test/hook", "http://a.test/hook"),
            ("https://a.test/hook?v=1", "https://a.test/hook"),
            ("https://a.test/hook", "https://a.test/hook/deeper"),
        ],
    )
    def test_every_other_difference_is_significant(self, left: str, right: str) -> None:
        """Collapsing these would declare a shop healthy while events misroute."""
        assert normalise_delivery_uri(left) != normalise_delivery_uri(right)

    def test_a_non_url_endpoint_is_not_parsed_as_a_url(self) -> None:
        arn = "arn:aws:events:us-east-1::event-source/aws.partner/shopify.com/1/x"
        assert normalise_delivery_uri(arn) == arn
        assert normalise_delivery_uri("pubsub://project:topic") == "pubsub://project:topic"


class TestDeliveryUriValidation:
    @pytest.mark.parametrize(
        "bad",
        [
            "ftp://a.test/hook",
            "://a.test/hook",
            "https:///hook",
            "https://user:pw@a.test/hook",
            "https://a.test/hook#frag",
            "not-a-uri",
        ],
    )
    def test_a_dangerous_shape_is_refused(self, bad: str) -> None:
        with pytest.raises(ShopifyWebhookConfigError):
            validate_delivery_uri(bad)

    def test_plaintext_is_allowed_only_outside_a_deployed_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        local = "http://localhost:8000/api/v1/integrations/shopify/webhooks/x"
        monkeypatch.setattr(settings, "environment", Environment.LOCAL)
        assert validate_delivery_uri(local) == local

        monkeypatch.setattr(settings, "environment", Environment.PRODUCTION)
        with pytest.raises(ShopifyWebhookConfigError):
            validate_delivery_uri(local)

    def test_https_is_accepted_in_production(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "environment", Environment.PRODUCTION)
        assert validate_delivery_uri(uri_for("products/create")) == uri_for("products/create")


class TestDesiredSubscriptions:
    def test_the_uris_match_what_the_receiver_already_serves(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GQL-2 changed the transport, not where a merchant's events land."""
        from app.integrations.shopify.service import WEBHOOK_TOPICS, webhook_delivery_address

        monkeypatch.setattr(settings.shopify, "webhook_callback_base", BASE)
        desired = desired_subscriptions(WEBHOOK_TOPICS)
        assert len(desired) == len(WEBHOOK_TOPICS)
        for want in desired:
            assert want.uri == webhook_delivery_address(base=BASE, topic=want.topic)
            assert want.format == "JSON"

    def test_a_misconfigured_base_fails_before_any_network_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings.shopify, "webhook_callback_base", "   ")
        with pytest.raises(ShopifyWebhookConfigError):
            desired_subscriptions(("products/create",))


# ------------------------------------------------------------- reconciliation
class TestReconcile:
    async def test_an_exact_match_issues_no_mutation(self) -> None:
        shopify = FakeShopify()
        shopify.seed(topic="PRODUCTS_CREATE", uri=uri_for("products/create"))

        report = await shopify.reconciler().reconcile([spec()])

        assert shopify.creates == [], "reconciliation must be idempotent"
        assert report.created_count == 0
        assert report.status_for("products/create") is ReconcileStatus.ALREADY_PRESENT
        assert report.healthy is True

    async def test_a_match_differing_only_in_case_and_trailing_slash_is_a_match(self) -> None:
        shopify = FakeShopify()
        shopify.seed(
            topic="PRODUCTS_CREATE",
            uri=uri_for("products/create").replace("app.example.test", "APP.EXAMPLE.TEST") + "/",
        )

        report = await shopify.reconciler().reconcile([spec()])

        assert shopify.creates == []
        assert report.healthy is True

    async def test_a_missing_subscription_is_created_exactly_once(self) -> None:
        shopify = FakeShopify()

        report = await shopify.reconciler().reconcile([spec()])

        assert len(shopify.creates) == 1
        assert shopify.creates[0]["topic"] == "PRODUCTS_CREATE"
        assert shopify.creates[0]["webhookSubscription"]["uri"] == uri_for("products/create")
        assert report.created_count == 1
        assert report.status_for("products/create") is ReconcileStatus.CREATED
        assert report.healthy is True

    async def test_only_the_missing_topics_are_created(self) -> None:
        shopify = FakeShopify()
        shopify.seed(topic="PRODUCTS_CREATE", uri=uri_for("products/create"))
        shopify.seed(topic="ORDERS_CREATE", uri=uri_for("orders/create"))
        desired = [spec("products/create"), spec("orders/create"), spec("orders/updated")]

        report = await shopify.reconciler().reconcile(desired)

        assert len(shopify.creates) == 1
        assert shopify.creates[0]["topic"] == "ORDERS_UPDATED"
        assert report.created_count == 1

    async def test_a_second_run_after_a_successful_create_creates_nothing(self) -> None:
        """Idempotence across runs, against Shopify's own changed state."""
        shopify = FakeShopify()
        await shopify.reconciler().reconcile([spec()])
        assert len(shopify.creates) == 1

        second = await shopify.reconciler().reconcile([spec()])

        assert len(shopify.creates) == 1, "the second run must not duplicate"
        assert second.status_for("products/create") is ReconcileStatus.ALREADY_PRESENT

    async def test_the_whole_desired_set_reconciles_from_empty(self) -> None:
        from app.integrations.shopify.service import WEBHOOK_TOPICS

        shopify = FakeShopify()
        desired = [spec(topic) for topic in WEBHOOK_TOPICS]

        report = await shopify.reconciler().reconcile(desired)

        assert len(shopify.creates) == len(WEBHOOK_TOPICS)
        assert report.created_count == len(WEBHOOK_TOPICS)
        assert report.healthy is True
        assert shopify.lists == 1, "one list per reconciliation, not one per topic"


class TestUncertainOutcomes:
    async def test_a_timeout_is_unknown_and_is_never_replayed(self) -> None:
        shopify = FakeShopify(create_behaviour="timeout")

        report = await shopify.reconciler().reconcile([spec()])

        assert len(shopify.creates) == 1, "a replay is how a shop ends up with two"
        assert report.status_for("products/create") is ReconcileStatus.UNKNOWN
        assert report.created_count == 0
        assert report.healthy is False

    async def test_a_shop_left_unknown_is_not_reported_healthy(self) -> None:
        shopify = FakeShopify(create_behaviour="timeout")
        report = await shopify.reconciler().reconcile([spec(), spec("orders/create")])
        assert report.healthy is False
        assert all(i.status is ReconcileStatus.UNKNOWN for i in report.items)

    async def test_recovery_after_an_uncertain_outcome_lists_before_deciding(self) -> None:
        """The whole recovery mechanism, exercised rather than asserted in prose.

        The first attempt times out *after* Shopify already applied it — the
        worst case, and the one a blind replay would turn into a duplicate. The
        second run must re-list, find it, and create nothing.
        """
        shopify = FakeShopify()
        applied: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            if body.get("operationName") == "WebhookSubscriptionCreate" and not applied:
                # Shopify applies the change, then the response is lost.
                variables = body["variables"]
                shopify.creates.append(variables)
                shopify.seed(
                    topic=variables["topic"],
                    uri=variables["webhookSubscription"]["uri"],
                )
                applied.append("yes")
                raise httpx.ReadTimeout("response lost", request=request)
            return shopify.handler(request)

        client = ShopifyGraphQLClient(
            shop_domain=SHOP,
            access_token=TOKEN,
            transport=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        first = await WebhookReconciler(client).reconcile([spec()])
        assert first.status_for("products/create") is ReconcileStatus.UNKNOWN
        assert first.healthy is False

        second = await WebhookReconciler(client).reconcile([spec()])

        assert second.status_for("products/create") is ReconcileStatus.ALREADY_PRESENT
        assert len(shopify.creates) == 1, "the recovery run must not create a second"
        assert second.healthy is True

    async def test_a_definitive_rejection_is_failed_not_unknown(self) -> None:
        shopify = FakeShopify(create_behaviour="user_error")

        report = await shopify.reconciler().reconcile([spec("orders/create")])

        assert len(shopify.creates) == 1
        assert report.status_for("orders/create") is ReconcileStatus.FAILED
        assert report.healthy is False

    async def test_throttling_is_unknown_and_not_retried(self) -> None:
        shopify = FakeShopify(create_behaviour="throttled")

        report = await shopify.reconciler().reconcile([spec()])

        assert len(shopify.creates) == 1
        assert report.status_for("products/create") is ReconcileStatus.UNKNOWN
        assert report.healthy is False


class TestWarnings:
    async def test_a_duplicate_is_reported_never_deleted(self) -> None:
        shopify = FakeShopify()
        shopify.seed(topic="PRODUCTS_CREATE", uri=uri_for("products/create"))
        shopify.seed(topic="PRODUCTS_CREATE", uri=uri_for("products/create"))

        report = await shopify.reconciler().reconcile([spec()])

        assert shopify.creates == []
        assert len(shopify.existing) == 2, "GQL-2 deletes nothing"
        assert any("duplicate" in w or "identical" in w for w in report.warnings)
        assert report.healthy is False, "a duplicate delivers every event twice"

    async def test_a_subscription_pointing_elsewhere_is_reported_and_the_desired_one_created(
        self,
    ) -> None:
        shopify = FakeShopify()
        shopify.seed(topic="PRODUCTS_CREATE", uri="https://old-app.example.test/hook")

        report = await shopify.reconciler().reconcile([spec()])

        assert len(shopify.creates) == 1
        assert any("point somewhere else" in w for w in report.warnings)
        assert len(shopify.existing) == 2
        assert report.healthy is False

    async def test_an_unmanaged_topic_is_reported_and_left_alone(self) -> None:
        shopify = FakeShopify()
        shopify.seed(topic="PRODUCTS_CREATE", uri=uri_for("products/create"))
        shopify.seed(topic="CARTS_CREATE", uri="https://other.example.test/hook")

        report = await shopify.reconciler().reconcile([spec()])

        assert shopify.creates == []
        assert any("unmanaged" in w for w in report.warnings)
        assert report.healthy is False

    async def test_a_non_http_endpoint_is_reported_and_never_matched(self) -> None:
        """A Pub/Sub destination must not satisfy an HTTPS requirement."""
        shopify = FakeShopify()
        shopify.seed(topic="PRODUCTS_CREATE", uri="pubsub://my-project:my-topic")

        report = await shopify.reconciler().reconcile([spec()])

        assert len(shopify.creates) == 1, "the HTTPS subscription is still missing"
        assert any("non-HTTP" in w for w in report.warnings)
        assert report.healthy is False

    async def test_a_format_mismatch_does_not_count_as_a_match(self) -> None:
        shopify = FakeShopify()
        shopify.seed(topic="PRODUCTS_CREATE", uri=uri_for("products/create"), format_="XML")

        report = await shopify.reconciler().reconcile([spec()])

        assert len(shopify.creates) == 1
        assert report.healthy is False


class TestMatchContract:
    def test_a_field_filter_this_app_did_not_ask_for_is_not_a_match(self) -> None:
        assert spec().matches(record(include=("id", "title"))) is False

    def test_a_payload_filter_this_app_did_not_ask_for_is_not_a_match(self) -> None:
        assert spec().matches(record(filter_="tag:sale")) is False

    def test_include_fields_compare_order_insensitively(self) -> None:
        want = spec(include_fields=("title", "id"))
        assert want.matches(record(include=("id", "title"))) is True

    def test_an_empty_filter_string_and_none_are_the_same_absence(self) -> None:
        assert spec().matches(record(filter_="")) is True

    def test_a_different_topic_is_not_a_match(self) -> None:
        assert spec().matches(record(topic="PRODUCTS_UPDATE")) is False


class TestNoRestTraffic:
    async def test_reconciliation_only_ever_hits_the_graphql_endpoint(self) -> None:
        """REST-008 and REST-009 are gone, not merely unused by default."""
        shopify = FakeShopify()
        await shopify.reconciler().reconcile([spec(), spec("orders/create")])

        assert shopify.paths, "the reconciler made no request at all"
        for path in shopify.paths:
            assert path == "/admin/api/2026-07/graphql.json"
            assert "webhooks.json" not in path
