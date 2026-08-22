"""Idempotent shop-scoped webhook reconciliation over GraphQL (GQL-2).

Replaces the REST list-and-create pair (`REST-008`, `REST-009`) without
changing what the shop ends up subscribed to.

## Shop-scoped, deliberately

Shopify now recommends declaring webhooks in ``shopify.app.toml`` when the
topics and delivery URIs are the same for every shop — which, here, they are.
That is not what this app does today: it creates **shop-scoped** subscriptions
through the API, there is no ``shopify.app.toml`` in this repository, and the
2026-07 reference is explicit that ``webhookSubscriptions`` *"returns only
shop-scoped subscriptions, not app-scoped subscriptions configured in TOML
files"*.

Those two facts together mean a half-migration is worse than either end state.
Declaring the same topics app-specifically while per-shop subscriptions still
exist would deliver **every event twice**, and the query that would let this
code notice cannot see the app-scoped half at all. Removing the per-shop
subscriptions first requires ``webhookSubscriptionDelete``, which belongs to
GQL-6.

So GQL-2 migrates the transport and nothing else: still shop-scoped, still the
same topics, same URIs, same delivery. The move to config-managed subscriptions
is recorded as a GQL-6 decision, and mixed mode is never presented as safe.

## What reconciliation does *not* do

It never deletes or edits an existing subscription — that is GQL-6's to own.
Anything that does not match the desired set is surfaced as a warning rather
than repaired, because repairing it here would mean deleting a subscription
some other part of the system might be relying on.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from urllib.parse import urlsplit

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.shopify.exceptions import (
    ShopifyWebhookConfigError,
    ShopifyWebhookTopicError,
)
from app.integrations.shopify.graphql import ShopifyGraphQLClient
from app.integrations.shopify.graphql_operations import (
    CreatedWebhook,
    WebhookSubscriptionRecord,
    create_webhook_subscription,
    is_http_endpoint,
    list_webhook_subscriptions,
)

logger = get_logger(__name__)

#: REST slash-topic to ``WebhookSubscriptionTopic`` enum member.
#:
#: Written out rather than derived with ``upper().replace("/", "_")``. The
#: transformation happens to be right for all six topics today, and would be
#: wrong the moment Shopify names an enum member that does not follow it — with
#: no failure until a shop stopped receiving events. An explicit table also
#: makes an unknown topic a local error instead of a plausible-looking string
#: sent to Shopify.
#:
#: Every member verified against the 2026-07 ``WebhookSubscriptionTopic``
#: reference on 22 August 2026, together with its required scope.
TOPIC_ENUM: Final[dict[str, str]] = {
    "products/create": "PRODUCTS_CREATE",  # read_products
    "products/update": "PRODUCTS_UPDATE",  # read_products
    "inventory_levels/update": "INVENTORY_LEVELS_UPDATE",  # read_inventory
    "orders/create": "ORDERS_CREATE",  # read_orders
    "orders/updated": "ORDERS_UPDATED",  # read_orders
    "app/uninstalled": "APP_UNINSTALLED",  # no scope required
}

#: The only format this app's receiver parses.
WEBHOOK_FORMAT: Final = "JSON"


def topic_enum(topic: str) -> str:
    """Map a REST slash topic to its GraphQL enum member, or fail locally."""
    try:
        return TOPIC_ENUM[topic]
    except KeyError:
        raise ShopifyWebhookTopicError(
            f"No Shopify GraphQL topic is mapped for {topic!r}.",
            details={"topic": topic},
        ) from None


@dataclass(frozen=True, slots=True)
class DesiredWebhook:
    """One subscription this app requires the shop to have.

    Immutable and carrying only what current behaviour actually needs: topic,
    URI and format. ``include_fields`` and ``filter`` are represented because
    the *comparison* must account for them — a subscription matching on topic
    and URI but carrying an unexpected field filter is not the same
    subscription — but this app configures neither, so both stay empty.
    """

    topic: str
    uri: str
    format: str = WEBHOOK_FORMAT
    include_fields: tuple[str, ...] = ()
    filter: str | None = None

    @property
    def enum_topic(self) -> str:
        return topic_enum(self.topic)

    def matches(self, record: WebhookSubscriptionRecord) -> bool:
        """Whether an existing subscription is exactly this one.

        The comparison contract, stated once and used everywhere:

        * **topic** compared as the GraphQL enum, since that is what Shopify
          returns;
        * **uri** compared after the documented normalisation below — and only
          that. Two URIs that differ in path, query or host are different
          destinations and must never be collapsed;
        * **format**, **includeFields** (order-insensitive) and **filter**
          compared exactly.
        """
        return (
            record.topic == self.enum_topic
            and normalise_delivery_uri(record.uri) == normalise_delivery_uri(self.uri)
            and record.format == self.format
            and tuple(sorted(record.include_fields)) == tuple(sorted(self.include_fields))
            and (record.filter or None) == (self.filter or None)
        )


def normalise_delivery_uri(uri: str) -> str:
    """The documented URI comparison contract.

    Deliberately minimal. Only two differences are treated as insignificant,
    because only these two are guaranteed not to change where a webhook lands:

    * **scheme and host case** — DNS is case-insensitive;
    * **a single trailing slash on the path**.

    Everything else is significant. Query strings, ports, and any path
    difference are preserved: ``…/webhooks/orders-create`` and
    ``…/webhooks/orders-updated`` differ by one path segment and deliver to
    different handlers, so a normaliser generous enough to collapse them would
    make the reconciler declare a shop healthy while half its topics went to the
    wrong endpoint.
    """
    parts = urlsplit(uri)
    if not parts.scheme or not parts.netloc:
        # Not an HTTP(S) URL at all -- a Pub/Sub URI or an EventBridge ARN.
        # Returned untouched: parsing it as a URL is exactly the coercion that
        # would make it silently never match.
        return uri.strip()
    path = parts.path
    if path.endswith("/") and len(path) > 1:
        path = path[:-1]
    rebuilt = f"{parts.scheme.lower()}://{parts.netloc.lower()}{path}"
    if parts.query:
        rebuilt = f"{rebuilt}?{parts.query}"
    return rebuilt


def validate_delivery_uri(uri: str) -> str:
    """Check a callback URI before it is ever sent to Shopify.

    The URI comes from ``settings.shopify.webhook_callback_base`` — trusted
    application configuration, never tenant or user input — so this guards
    against a misconfigured deployment rather than an attacker. It still refuses
    the shapes that would send a merchant's events somewhere unintended:
    userinfo, a fragment, a missing host, or a scheme that is not HTTP(S).

    HTTPS is required outside local development. The exception is explicit and
    environment-gated rather than a bare ``if "localhost"`` — a check keyed on a
    hostname would quietly permit plaintext delivery in production the first
    time somebody pointed a tunnel at it.
    """
    candidate = uri.strip()
    parts = urlsplit(candidate)
    if parts.scheme not in {"http", "https"}:
        raise ShopifyWebhookConfigError(
            "A webhook delivery URI must use http or https.",
            details={"scheme": parts.scheme or "(none)"},
        )
    if not parts.hostname:
        raise ShopifyWebhookConfigError("A webhook delivery URI must name a host.")
    if parts.username or parts.password:
        raise ShopifyWebhookConfigError("A webhook delivery URI must not carry userinfo.")
    if parts.fragment:
        raise ShopifyWebhookConfigError("A webhook delivery URI must not carry a fragment.")
    if parts.scheme != "https" and settings.environment.is_deployed:
        raise ShopifyWebhookConfigError(
            "Webhook delivery must use https outside local development.",
            details={"environment": settings.environment.value},
        )
    return candidate


class ReconcileStatus(StrEnum):
    """What happened to one desired subscription."""

    #: An exact match already existed. No mutation was issued.
    ALREADY_PRESENT = "already_present"
    #: Exactly one create mutation succeeded.
    CREATED = "created"
    #: The mutation timed out or otherwise ended with an unknown outcome. The
    #: subscription may or may not exist; the next run re-lists before deciding.
    UNKNOWN = "unknown"
    #: The create was definitively refused by Shopify.
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ReconcileItem:
    topic: str
    status: ReconcileStatus
    webhook_gid: str | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    """The whole outcome, including what was found but not touched."""

    items: tuple[ReconcileItem, ...] = ()
    #: Subscriptions that exist for a topic this app wants but do not match the
    #: desired specification, and subscriptions for topics it does not manage.
    #: Reported, never deleted -- deletion is GQL-6's.
    warnings: tuple[str, ...] = ()
    listed_count: int = 0
    created_count: int = 0

    def status_for(self, topic: str) -> ReconcileStatus | None:
        """The outcome for one topic, or ``None`` if it was not in the desired set."""
        for item in self.items:
            if item.topic == topic:
                return item.status
        return None

    @property
    def healthy(self) -> bool:
        """Every desired subscription confirmed present, and nothing conflicting.

        A warning is enough to withhold this. Reporting a shop healthy while a
        duplicate or mismatched subscription sits alongside the desired one
        would be exactly the false reassurance this phase is meant to remove.
        """
        return (
            not self.warnings
            and bool(self.items)
            and all(
                item.status in (ReconcileStatus.ALREADY_PRESENT, ReconcileStatus.CREATED)
                for item in self.items
            )
        )


def desired_subscriptions(topics: Sequence[str]) -> tuple[DesiredWebhook, ...]:
    """Build the desired specification from application configuration.

    Uses the existing ``webhook_delivery_address`` so the URI a subscription is
    created against stays byte-identical to the one the REST path used and the
    one the receiver already serves. GQL-2 changes the transport, not where a
    merchant's events land.
    """
    from app.integrations.shopify.service import webhook_delivery_address

    base = settings.shopify.webhook_callback_base
    desired: list[DesiredWebhook] = []
    for topic in topics:
        uri = validate_delivery_uri(webhook_delivery_address(base=base, topic=topic))
        # Raises locally for an unmapped topic, before any network call.
        topic_enum(topic)
        desired.append(DesiredWebhook(topic=topic, uri=uri))
    return tuple(desired)


class WebhookReconciler:
    """List, compare, and create only what is missing.

    Stateless with respect to the database: no webhook identifier is persisted,
    because Shopify's own list is the authority and a stored id would be a
    second source of truth that could go stale without anyone noticing. That is
    also why GQL-2 needs no migration.
    """

    def __init__(self, client: ShopifyGraphQLClient) -> None:
        self._client = client

    async def reconcile(self, desired: Sequence[DesiredWebhook]) -> ReconcileReport:
        existing = await list_webhook_subscriptions(self._client)
        items: list[ReconcileItem] = []
        warnings: list[str] = []
        created = 0

        managed_enums = {d.enum_topic for d in desired}
        http_existing = [record for record in existing if is_http_endpoint(record)]
        for record in existing:
            if not is_http_endpoint(record):
                # Represented explicitly. A Pub/Sub or EventBridge destination is
                # not something this app configured, and pretending it is an
                # HTTP endpoint would let it match a desired subscription.
                warnings.append(
                    f"{record.topic}: a non-HTTP delivery endpoint exists and was left untouched"
                )

        for spec in desired:
            matches = [record for record in http_existing if spec.matches(record)]
            same_topic = [record for record in http_existing if record.topic == spec.enum_topic]

            if len(matches) > 1:
                warnings.append(
                    f"{spec.topic}: {len(matches)} identical subscriptions exist; "
                    "duplicates deliver every event more than once"
                )
            mismatched = [record for record in same_topic if record not in matches]
            if mismatched:
                warnings.append(
                    f"{spec.topic}: {len(mismatched)} subscription(s) for this topic "
                    "point somewhere else and were left untouched"
                )

            if matches:
                items.append(
                    ReconcileItem(
                        topic=spec.topic,
                        status=ReconcileStatus.ALREADY_PRESENT,
                        webhook_gid=matches[0].id,
                    )
                )
                continue

            item = await self._create(spec)
            if item.status is ReconcileStatus.CREATED:
                created += 1
            items.append(item)

        for record in http_existing:
            if record.topic not in managed_enums:
                warnings.append(
                    f"{record.topic}: an unmanaged subscription exists and was left untouched"
                )

        report = ReconcileReport(
            items=tuple(items),
            warnings=tuple(warnings),
            listed_count=len(existing),
            created_count=created,
        )
        logger.info(
            "shopify_webhook_reconciled",
            listed=report.listed_count,
            created=report.created_count,
            warnings=len(report.warnings),
            healthy=report.healthy,
            api_version=self._client.api_version,
        )
        return report

    async def _create(self, spec: DesiredWebhook) -> ReconcileItem:
        """One create attempt. Never more than one, and never a blind replay."""
        from app.integrations.shopify.exceptions import (
            ShopifyError,
            ShopifyTimeoutError,
            ShopifyUserError,
        )

        try:
            created: CreatedWebhook = await create_webhook_subscription(
                self._client,
                topic_enum=spec.enum_topic,
                uri=spec.uri,
                format_=spec.format,
                include_fields=spec.include_fields,
                filter_=spec.filter,
            )
        except ShopifyUserError as exc:
            # Definitive: Shopify ran the mutation and refused it.
            return ReconcileItem(topic=spec.topic, status=ReconcileStatus.FAILED, detail=str(exc))
        except ShopifyTimeoutError:
            # The outcome is genuinely unknown -- the subscription may exist.
            # Replaying now is how a shop gets two. The next reconciliation
            # re-lists first, which is the whole recovery mechanism.
            logger.warning(
                "shopify_webhook_create_outcome_unknown",
                topic=spec.topic,
                detail="timeout; the next reconciliation re-lists before deciding",
            )
            return ReconcileItem(
                topic=spec.topic,
                status=ReconcileStatus.UNKNOWN,
                detail="Shopify did not respond; the outcome is unknown and will be "
                "resolved by re-listing on the next reconciliation.",
            )
        except ShopifyError as exc:
            # Throttling, transport failure, scope/auth refusal. The client has
            # already classified it; whether the mutation ran is not knowable
            # from here, so it is reported as unknown rather than failed.
            return ReconcileItem(topic=spec.topic, status=ReconcileStatus.UNKNOWN, detail=str(exc))

        return ReconcileItem(
            topic=spec.topic,
            status=ReconcileStatus.CREATED,
            webhook_gid=created.record.id,
        )


__all__ = [
    "TOPIC_ENUM",
    "WEBHOOK_FORMAT",
    "DesiredWebhook",
    "ReconcileItem",
    "ReconcileReport",
    "ReconcileStatus",
    "WebhookReconciler",
    "desired_subscriptions",
    "normalise_delivery_uri",
    "topic_enum",
    "validate_delivery_uri",
]
