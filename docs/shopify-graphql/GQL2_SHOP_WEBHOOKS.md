# GQL-2 — shop authority and webhook reconciliation

Migrates the first three inventory rows onto the GQL-1 foundation:

| Call | Was | Is |
|---|---|---|
| `GQL-000` | `ShopifyClient.fetch_shop_currency_code` (legacy GraphQL bolted onto the REST client) | `graphql_operations.fetch_shop_authority` |
| `REST-008` | `GET /admin/api/{version}/webhooks.json` | `WebhookSubscriptions` query |
| `REST-009` | `POST /admin/api/{version}/webhooks.json` | `webhookSubscriptionCreate` mutation |

Nothing else changed. Same topics, same delivery URIs, same shop-scoped model,
same `Store.currency` semantics. **Ten of twelve versioned Admin REST calls
remain** — this phase is two of them.

---

## Official sources

Verified against the Shopify Admin GraphQL **2026-07** reference on
22 August 2026. Nothing here was written from memory of the schema.

| Fact | Where |
|---|---|
| `Shop.currencyCode: CurrencyCode!` | `Shop` object reference |
| `webhookSubscriptions(first, after, …): WebhookSubscriptionConnection!` | `QueryRoot` reference |
| `webhookSubscriptions` *"returns only shop-scoped subscriptions, not app-scoped subscriptions configured in TOML files"* | `QueryRoot.webhookSubscriptions` |
| `WebhookSubscription.uri: URI!` is current; `callbackUrl` and the `endpoint` union are **deprecated** | `WebhookSubscription` object reference |
| `webhookSubscriptionCreate(topic: WebhookSubscriptionTopic!, webhookSubscription: WebhookSubscriptionInput!)` returns `{ webhookSubscription, userErrors }` | mutation reference |
| `WebhookSubscriptionTopic` members and their required access scopes | enum reference |

### The correction that justifies checking rather than recalling

GQL-1 proposed `nodes { id topic endpoint { __typename } }` for `REST-008`, and
recorded it as `unverified` precisely because it had not been read. Reading it
showed the field is deprecated in 2026-07. Had the inventory been trusted as
written, this phase would have shipped a document selecting a deprecated union
and would have compared endpoints by `__typename` — which never matches a URL at
all. The row is now `verified` and the correction is recorded in
[`rest-inventory.json`](rest-inventory.json) under `migrationLog`.

---

## Decision: shop-scoped subscriptions are retained

Shopify now recommends declaring webhooks in `shopify.app.toml` when the topics
and delivery URIs are the same for every shop — which, here, they are. GQL-2
deliberately does **not** do that.

The reason is in the reference sentence quoted above: `webhookSubscriptions`
returns only shop-scoped subscriptions. So if the same topics were declared
app-specifically while per-shop subscriptions still existed, every event would
be delivered **twice**, and the query this codebase uses to detect problems
could not see the app-scoped half at all. It would be an undetectable
duplication.

Removing the per-shop subscriptions first requires `webhookSubscriptionDelete`,
which belongs to **GQL-6** along with the rest of the disconnect path. Until
then:

- there is no `shopify.app.toml` in this repository, and a drift test fails if
  one appears (`test_no_app_scoped_toml_subscriptions_were_introduced`);
- GQL-2 deletes nothing — a mismatched or duplicate subscription is reported as
  a warning, never repaired, because repairing it here would mean deleting a
  subscription something else may rely on;
- **mixed mode is never described as safe.** It is a duplication bug with no
  detection mechanism.

---

## Concurrency: one create, or none

`register_webhooks` is called from OAuth completion, from reconnect, and from
recovery. Two of those overlapping is ordinary, and the naive shape —
list, decide, create — duplicates under it: both list an empty shop, both decide
the topic is missing, both create it.

The fix is a `SELECT … FOR UPDATE` on the `ShopifyConnection` row for the
duration of list-decide-create:

```python
client, connection = await self.graphql_client_for_store(store_id)
locked = await self.connections.lock_for_update(connection.id)
report = await WebhookReconciler(client).reconcile(desired)
```

Why that row: it is the row the reconciliation is about, it already exists, it
is one per store, and it is already tenant-scoped by
`ShopifyConnectionRepository._base_query`. No new table, no new queue, and no
second locking architecture — this is the same coordination mechanism the bulk
pricing work uses.

**Not an `asyncio.Lock`.** The racing callers are two web workers, or a worker
and a Celery task, often on different machines. An in-process mutex protects
neither. `populate_existing=True` is on the query for the same reason it is on
the pricing fence: a guard that reads its own session's cached copy is not a
guard.

### How that claim is tested

`backend/tests/integration/test_shopify_gql2_webhook_concurrency.py` runs two
reconcilers on **two real PostgreSQL connections**, each on its own engine. The
winner is held inside its listing call — after it has taken the row — and is
released only once `pg_blocking_pids()` confirms the loser is genuinely blocked
on that row. Contention is observed, never timed; the 30-second bound exists so
a hang fails the suite rather than stalling it.

The file also carries the **control**: the same race driven without the row lock,
asserting that it *does* produce two subscriptions per topic. Without that, a
green concurrency test could just mean the two reconcilers never overlapped.

Red-before evidence: with `lock_for_update` removed, the locked test fails with
`backend <pid> never blocked — no lock contention occurred`.

---

## Idempotency and unknown outcomes

`webhookSubscriptionCreate` is parsed as a mutation by `ShopifyGraphQLClient`,
so it is **never** retried automatically — not on timeout, not on 429, not on
5xx. The old REST path inherited `ShopifyClient`'s generic retry, which meant a
lost response could create a second subscription.

Outcomes are represented explicitly rather than collapsed into success/failure:

| `ReconcileStatus` | Meaning |
|---|---|
| `already_present` | An exact match existed. No mutation was issued. |
| `created` | Exactly one create succeeded and was validated against the request. |
| `unknown` | Timeout, throttle or transport failure. The subscription may or may not exist. |
| `failed` | Shopify ran the mutation and definitively refused it (`userErrors`). |

**A lost response never triggers a replay.** Recovery is to re-list, which is
what the next reconciliation does first. The test
`test_recovery_after_an_uncertain_outcome_lists_before_deciding` exercises the
worst case: Shopify applies the change and *then* the response is lost. The
second run finds the subscription and creates nothing.

`webhooks_registered_at` is stamped only when the report is `healthy` — every
desired subscription confirmed present and no warnings. Stamping it after an
unknown or failed create is what would make a half-registered shop look
finished.

---

## The comparison contract

Two subscriptions are the same when topic, URI, format, `includeFields` and
`filter` all agree. The only URI differences treated as insignificant are:

- **scheme and host case** — DNS is case-insensitive;
- **a single trailing slash on the path**.

Everything else is significant, including query strings, ports and any path
difference. `…/webhooks/orders-create` and `…/webhooks/orders-updated` differ by
one segment and deliver to different handlers, so a normaliser generous enough
to collapse them would declare a shop healthy while half its topics went to the
wrong endpoint.

### Non-HTTP endpoints are represented, not coerced

Shopify delivers to Google Pub/Sub (`pubsub://…`) and Amazon EventBridge ARNs
through the same `uri` field. `is_http_endpoint` names them explicitly. Parsing
an EventBridge ARN as a URL would make it silently never match, so the
reconciler would create an HTTPS duplicate alongside it. Instead it is reported
as a warning and left untouched.

---

## The topic table

`TOPIC_ENUM` is written out rather than derived with
`upper().replace("/", "_")`. The transformation happens to be right for all six
topics today and would be wrong the moment Shopify names a member that does not
follow it — with no failure until a shop stopped receiving events. An explicit
table also makes an unknown topic a **local** error, raised before any network
call, instead of a plausible-looking string sent to Shopify.

| REST topic | 2026-07 enum member | Required scope |
|---|---|---|
| `products/create` | `PRODUCTS_CREATE` | `read_products` |
| `products/update` | `PRODUCTS_UPDATE` | `read_products` |
| `inventory_levels/update` | `INVENTORY_LEVELS_UPDATE` | `read_inventory` |
| `orders/create` | `ORDERS_CREATE` | `read_orders` |
| `orders/updated` | `ORDERS_UPDATED` | `read_orders` |
| `app/uninstalled` | `APP_UNINSTALLED` | none |

---

## Shop currency

`fetch_shop_authority` reads `shop.currencyCode` and normalises it through the
application's own money layer. It **fails closed on every ambiguity** — a
missing `shop`, a null or non-string `currencyCode`, or a code the money layer
refuses all raise.

**There is deliberately no default.** A store silently assumed to sell in USD
would misprice a whole catalogue and the mistake would stay invisible until a
customer was charged.

Note that `Store.currency` carries a column default of `USD`; what makes it
*trusted* is `currency_last_synced_at`, which is set only after a successful
read. So the invariant the tests assert is that a failed refresh leaves that
timestamp NULL — the value stays where the pricing layer still refuses to price
against it — and that a previously trusted currency is retained rather than
cleared.

---

## Why no migration was needed

An audit of `ShopifyConnection` and `StoreListing` found that **no webhook
identifier is persisted anywhere**. The only webhook state in the database is
`webhooks_registered_at`, a timestamp. Shopify's own list is the authority, and
a stored id would be a second source of truth that could go stale without anyone
noticing.

So GQL-2 introduces **no Alembic migration**; `alembic heads` remains `0028`.
This is recorded because the phase brief required stopping and reporting if one
appeared necessary — it did not.

---

## Known limitations

1. **`ShopifyClient.fetch_shop_currency_code` and `ShopifyClient.graphql` still
   exist**, with zero production callers.
   `backend/tests/unit/test_m24a_currency_fx.py` drives the former directly, and
   the phase brief forbids weakening an existing test to make a migration look
   tidier. `test_the_legacy_rest_currency_read_has_no_caller_at_all` pins the
   caller count at zero so it cannot quietly come back. Deleting both, and that
   test, belongs to GQL-6 with the rest of the legacy client.

2. **`REST-010` still uses `GET /webhooks.json`** — the disconnect path in
   `client.py`. It is GQL-6's, and the drift test permits the path string only in
   that module.

3. **The three mandatory privacy webhooks are not implemented.**
   `customers/data_request`, `customers/redact` and `shop/redact` are required
   for App Store submission, and a sweep of `backend/app/` found no handler,
   route or topic mapping for any of them. They are configured in the Partner
   Dashboard or `shopify.app.toml` rather than through this API, so they are not
   a REST-migration item and are outside this phase's stated scope
   (`GQL-000`, `REST-008`, `REST-009`). **This is a submission blocker and is
   recorded here rather than left to be discovered at review.** The roadmap
   carried "compliance-webhook audit" under GQL-2; the audit is done and its
   result is this paragraph — the implementation is not.

4. **No live Shopify request has ever been made by this code.** Every test drives
   `httpx.MockTransport`. Reconciliation against a real shop is unverified — see
   below.

5. **Duplicates and mismatches are reported, never repaired.** A shop that
   already has two identical subscriptions stays that way, and its report is
   `healthy = False` until GQL-6 can delete one.

6. **`filter` and `includeFields` are compared but never configured.** This app
   sets neither. The comparison accounts for them so a subscription carrying an
   unexpected field filter is not mistaken for the desired one.

---

## Live verification

**Not run.** No Shopify development store is identified for this worktree, and
the phase brief is explicit that credentials must not be fabricated and no live
provider request may be made without one. Every assertion in this document is
backed by the automated suite against `httpx.MockTransport`, not by traffic to a
real shop.

What a live run would need to confirm, in order:

1. `ShopAuthority` returns the dev store's real `currencyCode`;
2. a first `register_webhooks` creates six subscriptions and reports `healthy`;
3. a second immediately after creates nothing and reports six
   `already_present`;
4. the shop's subscription list in the Partner Dashboard shows exactly six, each
   pointing at the deployed callback base.

---

## Files

**Added**

- `backend/app/integrations/shopify/graphql_operations.py`
- `backend/app/integrations/shopify/webhook_reconciliation.py`
- `backend/tests/unit/test_shopify_gql2_operations.py`
- `backend/tests/unit/test_shopify_gql2_reconciliation.py`
- `backend/tests/integration/shopify_gql2_live.py`
- `backend/tests/integration/test_shopify_gql2_webhook_concurrency.py`
- `backend/tests/integration/test_shopify_gql2_currency.py`
- `docs/shopify-graphql/GQL2_SHOP_WEBHOOKS.md`

**Modified**

- `backend/app/integrations/shopify/service.py` — `graphql_client_for_store`,
  currency and webhooks migrated, store-row lock
- `backend/app/integrations/shopify/exceptions.py` — `ShopifyWebhookTopicError`
- `backend/app/repositories/shopify.py` — `lock_for_update`
- `backend/tests/unit/test_rest_inventory.py` — shipped-phase rules and the
  code-agreement drift guard
- `docs/shopify-graphql/REST_INVENTORY.md`,
  `docs/shopify-graphql/rest-inventory.json`,
  `docs/shopify-graphql/MASTER_MIGRATION_ROADMAP.md`

---

## Verification

| Gate | Result |
|---|---|
| `ruff check .` | clean |
| `ruff format --check .` | 329 files already formatted |
| `mypy app` (strict) | no issues in 191 source files |
| Full backend suite | **1700 passed** (baseline 1573, +127) |
| `alembic heads` | `0028 (head)` — no migration added |
| Live Shopify request | **none made** |

Test delta: 56 (operations) + 45 (reconciliation) + 6 (concurrency) + 9
(currency) + 11 (inventory drift guard) = 127.

A pre-existing defect surfaced while adding the guard: both `OAUTH-*` rows cited
`backend/tests/unit/test_shopify_oauth.py`, which has never existed in this
repository. `OAUTH-001`'s real coverage is now named and `OAUTH-002` is recorded
as having none, rather than pointed at the nearest plausible file.
