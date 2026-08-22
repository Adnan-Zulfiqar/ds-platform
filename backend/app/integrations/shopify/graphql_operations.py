"""Typed Shopify Admin GraphQL operations (GQL-2).

The operation layer GQL-1 deliberately deferred. Every document here is static
and owned by this codebase; merchant and shop values travel in variables, and
every call goes through ``ShopifyGraphQLClient`` so retry policy, cost handling,
error classification and token safety are decided in exactly one place.

**Verified against the official Admin GraphQL 2026-07 reference on 22 August
2026** — see ``docs/shopify-graphql/GQL2_SHOP_WEBHOOKS.md`` for the links. Two
findings are load-bearing and are the reason this was checked rather than
recalled:

* ``WebhookSubscription.uri: String!`` is the current endpoint field.
  ``callbackUrl`` and the ``endpoint`` union are **deprecated** in 2026-07, so a
  document written from an older example would be selecting deprecated fields.
* ``webhookSubscriptions`` returns *"only shop-scoped subscriptions, not
  app-scoped subscriptions configured in TOML files"*. That sentence decides the
  whole migration strategy — see the module docstring of
  ``webhook_reconciliation``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from app.core.logging import get_logger
from app.domain.money import normalise_currency
from app.integrations.shopify.exceptions import (
    ShopifyGraphQLError,
    ShopifyResponseError,
)
from app.integrations.shopify.gid import ShopifyGid, parse_gid
from app.integrations.shopify.graphql import (
    GraphQLResponse,
    OperationType,
    ShopifyGraphQLClient,
    raise_for_user_errors,
)
from app.integrations.shopify.pagination import (
    PageWalker,
    connection_nodes,
    parse_page_info,
)

logger = get_logger(__name__)

# ---------------------------------------------------------------- documents

#: GQL-000's replacement. Deliberately minimal: ``currencyCode`` is the only
#: field the store-currency authority needs, and every extra field is protected
#: merchant data this app would then be accountable for holding.
SHOP_AUTHORITY_QUERY: Final = """
query ShopAuthority {
  shop {
    currencyCode
  }
}
""".strip()

#: REST-008's replacement. ``uri`` rather than the deprecated ``callbackUrl``.
#: ``includeFields`` and ``filter`` are selected because the reconciler compares
#: them -- a subscription that matches on topic and URI but differs on either is
#: not the same subscription, and treating it as one would leave the shop
#: silently subscribed to the wrong payload.
WEBHOOK_SUBSCRIPTIONS_QUERY: Final = """
query WebhookSubscriptions($first: Int!, $after: String) {
  webhookSubscriptions(first: $first, after: $after) {
    nodes {
      id
      topic
      uri
      format
      includeFields
      filter
    }
    pageInfo {
      hasNextPage
      endCursor
    }
  }
}
""".strip()

#: REST-009's replacement. ``userErrors`` is selected because a document that
#: omits it makes every rejection invisible -- the client refuses such a
#: document outright, which is the behaviour that guarantees this stays here.
WEBHOOK_SUBSCRIPTION_CREATE_MUTATION: Final = """
mutation WebhookSubscriptionCreate(
  $topic: WebhookSubscriptionTopic!
  $webhookSubscription: WebhookSubscriptionInput!
) {
  webhookSubscriptionCreate(topic: $topic, webhookSubscription: $webhookSubscription) {
    webhookSubscription {
      id
      topic
      uri
      format
      includeFields
      filter
    }
    userErrors {
      field
      message
    }
  }
}
""".strip()

#: One page of subscriptions. Shopify's connection maximum is 250; 100 keeps a
#: single request's query cost modest while making more than one page unusual.
WEBHOOK_PAGE_SIZE: Final = 100

#: Bounds for the whole traversal. A shop with more than 2,000 subscriptions for
#: one app is not a shop this code should keep walking -- it is a signal that
#: something has been creating duplicates.
MAX_WEBHOOK_PAGES: Final = 20
MAX_WEBHOOK_NODES: Final = 2_000


# ------------------------------------------------------------ shop authority


@dataclass(frozen=True, slots=True)
class ShopAuthority:
    """What Shopify says about the shop itself.

    ``currency_code`` is already normalised through the application's own money
    layer, so a caller receives either a code this system can price in or an
    exception -- never a plausible-looking string it will choke on later.
    """

    currency_code: str


async def fetch_shop_authority(client: ShopifyGraphQLClient) -> ShopAuthority:
    """Read ``shop.currencyCode`` -- the store's selling-currency authority.

    Fails closed on every ambiguity. A missing ``shop``, a missing or null
    ``currencyCode``, or a code the money layer refuses all raise rather than
    returning something usable. **There is deliberately no default**: a store
    silently assumed to sell in USD would misprice a whole catalogue, and the
    mistake would be invisible until a customer was charged.
    """
    response = await client.execute(
        document=SHOP_AUTHORITY_QUERY,
        operation_name="ShopAuthority",
        operation_type=OperationType.QUERY,
    )
    shop = response.data.get("shop")
    if not isinstance(shop, Mapping):
        raise ShopifyResponseError(
            "Shopify returned no shop object for the currency authority query."
        )
    raw = shop.get("currencyCode")
    if not isinstance(raw, str) or not raw.strip():
        raise ShopifyResponseError(
            "Shopify returned no shop.currencyCode.",
            details={"present": raw is not None},
        )
    # `normalise_currency` raises ValidationError for anything the money layer
    # cannot price in. Surfacing that as-is is the point: it names the offending
    # code and is actionable, where a swallowed error would leave the store
    # holding a currency nothing downstream can use.
    return ShopAuthority(currency_code=normalise_currency(raw))


# --------------------------------------------------------- webhook listing


@dataclass(frozen=True, slots=True)
class WebhookSubscriptionRecord:
    """One existing shop-scoped subscription, as Shopify reports it.

    ``gid`` keeps the complete Shopify identifier. Identity never comes from a
    position in the response -- the same guard GQL-1 built ``ShopifyGid`` for.
    """

    gid: ShopifyGid
    topic: str
    uri: str
    format: str
    include_fields: tuple[str, ...] = ()
    filter: str | None = None

    @property
    def id(self) -> str:
        return self.gid.value


def _parse_subscription(node: object) -> WebhookSubscriptionRecord:
    """Read one node, refusing anything incomplete.

    Fails closed rather than skipping. A subscription this code cannot read is
    one it cannot compare, and quietly dropping it would make the reconciler
    believe a topic is unregistered and create a duplicate.
    """
    if not isinstance(node, Mapping):
        raise ShopifyResponseError("Shopify returned a webhook subscription that is not an object.")
    gid = parse_gid(node.get("id"), expected_resource="WebhookSubscription")
    topic = node.get("topic")
    uri = node.get("uri")
    format_ = node.get("format")
    if not isinstance(topic, str) or not topic:
        raise ShopifyResponseError("Shopify webhook subscription is missing its topic.")
    if not isinstance(uri, str) or not uri:
        # `uri` is `String!` in 2026-07. A non-HTTPS destination (Pub/Sub, an
        # EventBridge ARN) still arrives as a string here and is preserved
        # verbatim rather than coerced into a URL -- see `is_http_endpoint`.
        raise ShopifyResponseError("Shopify webhook subscription is missing its uri.")
    if not isinstance(format_, str) or not format_:
        raise ShopifyResponseError("Shopify webhook subscription is missing its format.")
    include_raw = node.get("includeFields")
    include: tuple[str, ...] = ()
    if isinstance(include_raw, list):
        include = tuple(str(item) for item in include_raw)
    filter_raw = node.get("filter")
    return WebhookSubscriptionRecord(
        gid=gid,
        topic=topic,
        uri=uri,
        format=format_,
        include_fields=include,
        filter=filter_raw if isinstance(filter_raw, str) else None,
    )


def is_http_endpoint(record: WebhookSubscriptionRecord) -> bool:
    """Whether this subscription delivers over HTTP(S).

    Shopify also supports Google Pub/Sub (``pubsub://…``) and Amazon EventBridge
    ARNs (``arn:aws:events:…``) through the same ``uri`` field. Those are
    represented explicitly rather than parsed as URLs: an EventBridge ARN
    coerced into a URL comparison would silently never match, and the reconciler
    would create an HTTPS duplicate alongside it.
    """
    return record.uri.startswith("https://") or record.uri.startswith("http://")


async def list_webhook_subscriptions(
    client: ShopifyGraphQLClient,
    *,
    page_size: int = WEBHOOK_PAGE_SIZE,
    max_pages: int = MAX_WEBHOOK_PAGES,
    max_nodes: int = MAX_WEBHOOK_NODES,
) -> list[WebhookSubscriptionRecord]:
    """Every shop-scoped subscription for this app, across all pages.

    First-page-only is the bug this signature exists to prevent: a shop with
    more subscriptions than one page would look like it was missing topics, and
    the reconciler would create duplicates for the ones it could not see.
    """
    walker = PageWalker(max_pages=max_pages, max_nodes=max_nodes)
    records: list[WebhookSubscriptionRecord] = []
    while True:
        response = await client.execute(
            document=WEBHOOK_SUBSCRIPTIONS_QUERY,
            operation_name="WebhookSubscriptions",
            operation_type=OperationType.QUERY,
            variables={"first": page_size, "after": walker.cursor},
        )
        connection = response.data.get("webhookSubscriptions")
        if not isinstance(connection, Mapping):
            raise ShopifyResponseError("Shopify returned no webhookSubscriptions connection.")
        nodes = connection_nodes(connection)
        records.extend(_parse_subscription(node) for node in nodes)
        page_info = parse_page_info(connection.get("pageInfo"))
        if not walker.record(page_info=page_info, node_count=len(nodes)):
            break
    logger.info(
        "shopify_webhook_subscriptions_listed",
        count=len(records),
        pages=walker.pages_fetched,
        api_version=client.api_version,
    )
    return records


# -------------------------------------------------------- webhook creation


@dataclass(frozen=True, slots=True)
class CreatedWebhook:
    """A subscription Shopify confirms it created, validated against the request."""

    record: WebhookSubscriptionRecord
    request_id: str | None


async def create_webhook_subscription(
    client: ShopifyGraphQLClient,
    *,
    topic_enum: str,
    uri: str,
    format_: str = "JSON",
    include_fields: tuple[str, ...] = (),
    filter_: str | None = None,
) -> CreatedWebhook:
    """Create one shop-scoped subscription.

    Parsed as a mutation by the shared client, so it is **never** retried
    automatically -- on a timeout the outcome is genuinely unknown, and a blind
    replay is how a shop ends up with two subscriptions delivering every event
    twice. Recovery is to re-list, which is what the reconciler does.

    The returned subscription is validated against what was asked for. Shopify
    confirming *a* subscription is not the same as confirming *this* one, and
    the difference matters when the reconciler is about to record success.
    """
    subscription_input: dict[str, Any] = {"uri": uri, "format": format_}
    if include_fields:
        subscription_input["includeFields"] = list(include_fields)
    if filter_:
        subscription_input["filter"] = filter_

    response: GraphQLResponse = await client.execute(
        document=WEBHOOK_SUBSCRIPTION_CREATE_MUTATION,
        operation_name="WebhookSubscriptionCreate",
        operation_type=OperationType.MUTATION,
        variables={"topic": topic_enum, "webhookSubscription": subscription_input},
    )
    payload = response.data
    # Raises ShopifyUserError on any non-empty userErrors, and raises outright
    # if the document did not select them -- so "no errors reported" can never
    # mean "errors were never asked for".
    raise_for_user_errors(payload, mutation_field="webhookSubscriptionCreate")

    field = payload.get("webhookSubscriptionCreate")
    if not isinstance(field, Mapping):
        raise ShopifyGraphQLError("Shopify returned no webhookSubscriptionCreate payload.")
    created = field.get("webhookSubscription")
    if created is None:
        raise ShopifyGraphQLError(
            "Shopify reported no userErrors but returned no webhook subscription.",
            request_id=response.request_id,
        )
    record = _parse_subscription(created)

    if record.topic != topic_enum:
        raise ShopifyGraphQLError(
            "Shopify created a subscription for a different topic than requested.",
            details={"requested": topic_enum, "returned": record.topic},
            request_id=response.request_id,
        )
    if record.uri != uri:
        raise ShopifyGraphQLError(
            "Shopify created a subscription with a different delivery URI than requested.",
            details={"requested": uri, "returned": record.uri},
            request_id=response.request_id,
        )
    if record.format != format_:
        raise ShopifyGraphQLError(
            "Shopify created a subscription with a different format than requested.",
            details={"requested": format_, "returned": record.format},
            request_id=response.request_id,
        )

    logger.info(
        "shopify_webhook_subscription_created",
        topic=record.topic,
        webhook_gid=record.id,
        api_version=client.api_version,
        shopify_request_id=response.request_id,
    )
    return CreatedWebhook(record=record, request_id=response.request_id)


__all__ = [
    "MAX_WEBHOOK_NODES",
    "MAX_WEBHOOK_PAGES",
    "SHOP_AUTHORITY_QUERY",
    "WEBHOOK_PAGE_SIZE",
    "WEBHOOK_SUBSCRIPTIONS_QUERY",
    "WEBHOOK_SUBSCRIPTION_CREATE_MUTATION",
    "CreatedWebhook",
    "ShopAuthority",
    "WebhookSubscriptionRecord",
    "create_webhook_subscription",
    "fetch_shop_authority",
    "is_http_endpoint",
    "list_webhook_subscriptions",
]
