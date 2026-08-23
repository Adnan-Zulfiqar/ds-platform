# Shopify Admin REST inventory

Taken at `bfb21e38aaf39f0b982b9a9f5e01062c28befd1e` (develop at the start of GQL-1).

Every call this platform makes to Shopify, found by sweeping the whole
repository rather than by reading the integration package and hoping it was
complete. The point is not the list — it is that nothing migrates in a later
phase without first appearing here, so "we thought that one was already
gone" cannot happen.

**Its twin is [`rest-inventory.json`](rest-inventory.json).** They are checked
against each other by `backend/tests/unit/test_rest_inventory.py`, which fails
if an id, a phase or a verification status exists in one and not the other.
Two documents that can disagree are worse than one, so they are not allowed to.

## What Shopify requires

Shopify's changelog is unambiguous: *"all new public apps submitted to the App
Store after this date must only use GraphQL"*, effective 1 April 2025. Every
`REST-*` row below is therefore **not permitted** in a new public app and must
be gone before submission. The `OAUTH-*` rows are not Admin API calls — OAuth
has no GraphQL equivalent — and are retained.

## Totals

| Group | Count |
|---|---|
| Versioned Admin REST calls (must be migrated) | **12** |
| Unversioned OAuth endpoints (retained) | 2 |
| Existing GraphQL calls (move onto the new client) | 1 |
| **Total inventoried** | **15** |

By target phase:

| Phase | Calls |
|---|---|
| GQL-2 | GQL-000, REST-008, REST-009 |
| GQL-3 | REST-001, REST-002, REST-003, REST-006 |
| GQL-4 | REST-004, REST-005 |
| GQL-5 | REST-007 |
| GQL-6 | REST-010, REST-011, REST-012 |
| none | OAUTH-001, OAUTH-002 |

Schema verification status — a replacement is `verified` only when the exact
2026-07 mutation or field was read in the official reference during this phase.
Everything else is `unverified` **on purpose**: guessing a field name here is
how a migration ships a mutation that silently does the wrong thing.

| Status | Calls |
|---|---|
| `verified` | REST-008, REST-009, REST-012, GQL-000 |
| `unverified` | REST-001, REST-002, REST-003, REST-004, REST-005, REST-006, REST-007, REST-010, REST-011 |
| `not-applicable` | OAUTH-001, OAUTH-002 |

## Migration progress

One row per shipped phase. A call is `removed` when its call site is gone from
the codebase, and `migrated` when the code still exists but has no production
caller. Both claims are enforced by
`backend/tests/unit/test_rest_inventory.py`, which greps the application
package: a status can only be asserted here if the code actually agrees.

| Phase | Shipped | Calls | Status |
|---|---|---|---|
| GQL-1 | 2026-08-22 | — | foundation only; migrated nothing, by design |
| GQL-2 | 2026-08-22 | `GQL-000`, `REST-008`, `REST-009` | `REST-008`/`REST-009` removed, `GQL-000` migrated |
| GQL-3 | — | `REST-001`, `REST-002`, `REST-003`, `REST-006` | pending |
| GQL-4 | — | `REST-004`, `REST-005` | pending |
| GQL-5 | — | `REST-007` | pending |
| GQL-6 | — | `REST-010`, `REST-011`, `REST-012` | pending |

**10 of 12 versioned Admin REST calls remain.** GQL-2 is not a claim that the
app is GraphQL-only; it is two calls out of twelve.

## Summary table

| ID | Method | Path | Caller | Phase | Verified | Risk | Allowed in a new public app |
|---|---|---|---|---|---|---|---|
| `REST-001` | GET | `/admin/api/{version}/products.json?handle={handle}&limit=1` | `ShopifySyncService._create_or_adopt` | GQL-3 | `unverified` | medium | **no** |
| `REST-002` | POST | `/admin/api/{version}/products.json` | `ShopifySyncService._create_or_adopt` | GQL-3 | `unverified` | high | **no** |
| `REST-003` | PUT | `/admin/api/{version}/products/{product_id}.json` | `ShopifySyncService.publish_product` | GQL-3 | `unverified` | high | **no** |
| `REST-004` | GET | `/admin/api/{version}/locations.json` | `ShopifySyncService.push_inventory` | GQL-4 | `unverified` | medium | **no** |
| `REST-005` | POST | `/admin/api/{version}/inventory_levels/set.json` | `ShopifySyncService.push_inventory` | GQL-4 | `unverified` | high | **no** |
| `REST-006` | PUT | `/admin/api/{version}/variants/{variant_id}.json` | `ShopifySyncService.push_price` | GQL-3 | `unverified` | high | **no** |
| `REST-007` | GET | `/admin/api/{version}/orders.json?status=any&limit={limit}` | `ShopifySyncService.import_orders` | GQL-5 | `unverified` | medium | **no** |
| ~~`REST-008`~~ | GET | `/admin/api/{version}/webhooks.json` | ~~`ShopifyIntegrationService.register_webhooks`~~ | GQL-2 ✅ | `verified` | low | removed |
| ~~`REST-009`~~ | POST | `/admin/api/{version}/webhooks.json` | ~~`ShopifyIntegrationService.register_webhooks`~~ | GQL-2 ✅ | `verified` | medium | removed |
| `REST-010` | GET | `/admin/api/{version}/webhooks.json` | `ShopifyClient.delete_registered_webhooks` | GQL-6 | `unverified` | low | **no** |
| `REST-011` | DELETE | `/admin/api/{version}/webhooks/{webhook_id}.json` | `ShopifyClient.delete_registered_webhooks` | GQL-6 | `unverified` | low | **no** |
| `REST-012` | DELETE | `/admin/api/{version}/api_permissions/current.json` | `ShopifyClient.revoke_access_token` | GQL-6 | `verified` | high | **no** |
| `OAUTH-001` | GET | `/admin/oauth/authorize` | `build_authorization_url` | none | `not-applicable` | low | yes |
| `OAUTH-002` | POST | `/admin/oauth/access_token` | `ShopifyClient.exchange_token` | none | `not-applicable` | low | yes |
| `GQL-000` | POST | `/admin/api/{version}/graphql.json` | `graphql_operations.fetch_shop_authority` | GQL-2 ✅ | `verified` | low | yes |

## Detail

### REST-001 — GET `/admin/api/{version}/products.json?handle={handle}&limit=1`

- **Source**: `backend/app/integrations/shopify/sync.py` → `ShopifySyncService._create_or_adopt`
- **Purpose**: Look up an existing product by handle so a retried publish adopts it instead of creating a duplicate.
- **Operation**: query
- **Scopes**: `read_products`
- **Tenant/store authority**: store_id -> ShopifyConnection (tenant-scoped)
- **Written locally**: none (read); result feeds StoreListing.external_product_id
- **Shopify side effect**: none
- **Idempotency**: safe; this call is itself the idempotency guard for REST-002
- **Retry**: ShopifyClient generic retry: timeouts, 429, 5xx
- **Pagination**: limit=1, no cursor
- **Current tests**: `backend/tests/unit/test_shopify_publish_idempotency.py`
- **Proposed GraphQL**: `query { productByIdentifier(identifier: {handle: $handle}) { id } }`
- **Target phase**: GQL-3
- **Schema verification**: `unverified`
- **Risk**: medium
- **Production usage**: publish path, every publish attempt
- **Removal status**: `present`
- **Permitted in a new public app**: **no — must be migrated**

### REST-002 — POST `/admin/api/{version}/products.json`

- **Source**: `backend/app/integrations/shopify/sync.py` → `ShopifySyncService._create_or_adopt`
- **Purpose**: Create the Shopify product for a DropPilot draft.
- **Operation**: mutation
- **Scopes**: `write_products`
- **Tenant/store authority**: store_id -> ShopifyConnection (tenant-scoped)
- **Written locally**: StoreListing.external_product_id, external_variant_map, inventory_item_map, status
- **Shopify side effect**: creates a product and its variants
- **Idempotency**: not idempotent; guarded by the handle lookup in REST-001
- **Retry**: ShopifyClient generic retry — a known hazard: a retried POST can duplicate a product
- **Pagination**: n/a
- **Current tests**: `backend/tests/unit/test_shopify_publish_idempotency.py`
- **Proposed GraphQL**: `mutation productSet($input: ProductSetInput!) { productSet(input: $input) { product { id variants(first: 250) { nodes { id inventoryItem { id } } } } userErrors { field message code } } }`
- **Target phase**: GQL-3
- **Schema verification**: `unverified`
- **Risk**: high
- **Production usage**: publish path
- **Removal status**: `present`
- **Permitted in a new public app**: **no — must be migrated**

### REST-003 — PUT `/admin/api/{version}/products/{product_id}.json`

- **Source**: `backend/app/integrations/shopify/sync.py` → `ShopifySyncService.publish_product`
- **Purpose**: Update an already-published product (title, body, variants, images).
- **Operation**: mutation
- **Scopes**: `write_products`
- **Tenant/store authority**: store_id -> ShopifyConnection (tenant-scoped)
- **Written locally**: StoreListing.external_variant_map, inventory_item_map, last_synced_at
- **Shopify side effect**: replaces product fields; an incomplete variant list can DELETE variants
- **Idempotency**: idempotent by product id
- **Retry**: ShopifyClient generic retry
- **Pagination**: n/a
- **Current tests**: `backend/tests/unit/test_shopify_publish_idempotency.py`
- **Proposed GraphQL**: `mutation productSet($input: ProductSetInput!) { productSet(input: $input) { product { id } userErrors { field message code } } }`
- **Target phase**: GQL-3
- **Schema verification**: `unverified`
- **Risk**: high
- **Production usage**: publish path for existing listings
- **Removal status**: `present`
- **Permitted in a new public app**: **no — must be migrated**

### REST-004 — GET `/admin/api/{version}/locations.json`

- **Source**: `backend/app/integrations/shopify/sync.py` → `ShopifySyncService.push_inventory`
- **Purpose**: Find a location to set inventory against.
- **Operation**: query
- **Scopes**: `read_locations`
- **Tenant/store authority**: store_id -> ShopifyConnection (tenant-scoped)
- **Written locally**: none
- **Shopify side effect**: none
- **Idempotency**: safe
- **Retry**: ShopifyClient generic retry
- **Pagination**: unpaginated — silently uses locations[0]
- **Current tests**: **none**
- **Proposed GraphQL**: `query { locations(first: 10) { nodes { id name isActive } pageInfo { hasNextPage endCursor } } }`
- **Target phase**: GQL-4
- **Schema verification**: `unverified`
- **Risk**: medium
- **Production usage**: inventory push
- **Removal status**: `present`
- **Permitted in a new public app**: **no — must be migrated**

### REST-005 — POST `/admin/api/{version}/inventory_levels/set.json`

- **Source**: `backend/app/integrations/shopify/sync.py` → `ShopifySyncService.push_inventory`
- **Purpose**: Set available quantity for one inventory item at one location.
- **Operation**: mutation
- **Scopes**: `write_inventory`
- **Tenant/store authority**: store_id -> ShopifyConnection (tenant-scoped)
- **Written locally**: StoreListing.last_synced_at
- **Shopify side effect**: overwrites available stock
- **Idempotency**: idempotent for a fixed quantity, but blindly overwrites concurrent changes
- **Retry**: ShopifyClient generic retry
- **Pagination**: n/a
- **Current tests**: **none**
- **Proposed GraphQL**: `mutation inventorySetQuantities($input: InventorySetQuantitiesInput!) { inventorySetQuantities(input: $input) { userErrors { field message code } } }`
- **Target phase**: GQL-4
- **Schema verification**: `unverified`
- **Risk**: high
- **Production usage**: inventory push
- **Removal status**: `present`
- **Permitted in a new public app**: **no — must be migrated**

### REST-006 — PUT `/admin/api/{version}/variants/{variant_id}.json`

- **Source**: `backend/app/integrations/shopify/sync.py` → `ShopifySyncService.push_price`
- **Purpose**: Push a recalculated sell price to one Shopify variant.
- **Operation**: mutation
- **Scopes**: `write_products`
- **Tenant/store authority**: store_id -> ShopifyConnection (tenant-scoped)
- **Written locally**: PriceChange rows via the pricing engine caller
- **Shopify side effect**: changes the customer-visible price
- **Idempotency**: idempotent for a fixed price
- **Retry**: ShopifyClient generic retry
- **Pagination**: n/a
- **Current tests**: **none**
- **Proposed GraphQL**: `mutation productVariantsBulkUpdate($productId: ID!, $variants: [ProductVariantsBulkInput!]!) { productVariantsBulkUpdate(productId: $productId, variants: $variants) { userErrors { field message code } } }`
- **Target phase**: GQL-3
- **Schema verification**: `unverified`
- **Risk**: high
- **Production usage**: pricing sync
- **Removal status**: `present`
- **Permitted in a new public app**: **no — must be migrated**

### REST-007 — GET `/admin/api/{version}/orders.json?status=any&limit={limit}`

- **Source**: `backend/app/integrations/shopify/sync.py` → `ShopifySyncService.import_orders`
- **Purpose**: Poll recent Shopify orders into DropPilot.
- **Operation**: query
- **Scopes**: `read_orders`
- **Tenant/store authority**: store_id -> ShopifyConnection (tenant-scoped)
- **Written locally**: Order and OrderItem rows
- **Shopify side effect**: none
- **Idempotency**: safe; upserted by external id
- **Retry**: ShopifyClient generic retry
- **Pagination**: limit only; no Link-header cursor following, so older orders are never reached
- **Current tests**: **none**
- **Proposed GraphQL**: `query orders($first: Int!, $after: String) { orders(first: $first, after: $after, sortKey: UPDATED_AT) { nodes { id name } pageInfo { hasNextPage endCursor } } }`
- **Target phase**: GQL-5
- **Schema verification**: `unverified`
- **Risk**: medium
- **Production usage**: scheduled order sync (Celery beat)
- **Removal status**: `present`
- **Permitted in a new public app**: **no — must be migrated**

### REST-008 — GET `/admin/api/{version}/webhooks.json`

- **Source**: `backend/app/integrations/shopify/service.py` → `ShopifyIntegrationService.register_webhooks` — **removed in GQL-2**
- **Purpose**: List existing webhook subscriptions so registration is idempotent.
- **Operation**: query
- **Scopes**: none beyond install
- **Tenant/store authority**: store_id -> ShopifyConnection (tenant-scoped)
- **Written locally**: none
- **Shopify side effect**: none
- **Idempotency**: safe
- **Retry**: ShopifyGraphQLClient bounded retry (query)
- **Pagination**: cursor-paginated via PageWalker (was unpaginated over REST)
- **Current tests**: `backend/tests/unit/test_shopify_gql2_operations.py`, `backend/tests/unit/test_shopify_gql2_reconciliation.py`, `backend/tests/integration/test_shopify_gql2_webhook_concurrency.py`, `backend/tests/integration/test_shopify_webhook_processing.py`
- **Proposed GraphQL**: `query WebhookSubscriptions($first: Int!, $after: String) { webhookSubscriptions(first: $first, after: $after) { nodes { id topic uri format includeFields filter } pageInfo { hasNextPage endCursor } } }`
- **Target phase**: GQL-2
- **Schema verification**: `verified`
- **Risk**: low
- **Production usage**: none - the call site was removed in GQL-2; replaced by graphql_operations.list_webhook_subscriptions
- **Removal status**: `removed`
- **Permitted in a new public app**: **no — migrated, no longer called**

> **GQL-2 correction.** GQL-1 proposed `endpoint { __typename }`. The official
> 2026-07 reference deprecates `callbackUrl` and the `endpoint` union; `uri` is
> the current field, and the shipped document selects it. Checking rather than
> recalling is what caught this.

### REST-009 — POST `/admin/api/{version}/webhooks.json`

- **Source**: `backend/app/integrations/shopify/service.py` → `ShopifyIntegrationService.register_webhooks` — **removed in GQL-2**
- **Purpose**: Create a webhook subscription for a topic DropPilot consumes.
- **Operation**: mutation
- **Scopes**: none beyond install
- **Tenant/store authority**: store_id -> ShopifyConnection (tenant-scoped)
- **Written locally**: ShopifyConnection.webhooks_registered_at
- **Shopify side effect**: creates a webhook subscription
- **Idempotency**: guarded by a re-list plus SELECT ... FOR UPDATE on the connection row; never automatically retried, so an unknown outcome is resolved by re-listing
- **Retry**: never - mutation; the shared client refuses automatic mutation retry
- **Pagination**: n/a
- **Current tests**: `backend/tests/unit/test_shopify_gql2_operations.py`, `backend/tests/unit/test_shopify_gql2_reconciliation.py`, `backend/tests/integration/test_shopify_gql2_webhook_concurrency.py`
- **Proposed GraphQL**: `mutation WebhookSubscriptionCreate($topic: WebhookSubscriptionTopic!, $webhookSubscription: WebhookSubscriptionInput!) { webhookSubscriptionCreate(topic: $topic, webhookSubscription: $webhookSubscription) { webhookSubscription { id topic uri format includeFields filter } userErrors { field message } } }`
- **Target phase**: GQL-2
- **Schema verification**: `verified`
- **Risk**: medium
- **Production usage**: none - the call site was removed in GQL-2; replaced by graphql_operations.create_webhook_subscription
- **Removal status**: `removed`
- **Permitted in a new public app**: **no — migrated, no longer called**

> **GQL-2 note.** The REST version inherited `ShopifyClient`'s generic retry, so
> a timed-out POST could be replayed and create a second subscription. The
> GraphQL replacement is parsed as a mutation and is never retried
> automatically; an unknown outcome is resolved by re-listing on the next
> reconciliation.

### REST-010 — GET `/admin/api/{version}/webhooks.json`

- **Source**: `backend/app/integrations/shopify/client.py` → `ShopifyClient.delete_registered_webhooks`
- **Purpose**: List webhooks to remove during disconnect.
- **Operation**: query
- **Scopes**: none beyond install
- **Tenant/store authority**: store_id -> ShopifyConnection (tenant-scoped)
- **Written locally**: none
- **Shopify side effect**: none
- **Idempotency**: safe
- **Retry**: ShopifyClient generic retry
- **Pagination**: unpaginated
- **Current tests**: **none**
- **Proposed GraphQL**: `query { webhookSubscriptions(first: 100) { nodes { id } pageInfo { hasNextPage endCursor } } }`
- **Target phase**: GQL-6
- **Schema verification**: `unverified`
- **Risk**: low
- **Production usage**: store disconnect
- **Removal status**: `present`
- **Permitted in a new public app**: **no — must be migrated**

### REST-011 — DELETE `/admin/api/{version}/webhooks/{webhook_id}.json`

- **Source**: `backend/app/integrations/shopify/client.py` → `ShopifyClient.delete_registered_webhooks`
- **Purpose**: Remove a webhook subscription during disconnect.
- **Operation**: mutation
- **Scopes**: none beyond install
- **Tenant/store authority**: store_id -> ShopifyConnection (tenant-scoped)
- **Written locally**: none
- **Shopify side effect**: deletes a webhook subscription
- **Idempotency**: idempotent; a second delete 404s and is swallowed
- **Retry**: ShopifyClient generic retry; failures logged and ignored
- **Pagination**: n/a
- **Current tests**: **none**
- **Proposed GraphQL**: `mutation webhookSubscriptionDelete($id: ID!) { webhookSubscriptionDelete(id: $id) { deletedWebhookSubscriptionId userErrors { field message } } }`
- **Target phase**: GQL-6
- **Schema verification**: `unverified`
- **Risk**: low
- **Production usage**: store disconnect
- **Removal status**: `present`
- **Permitted in a new public app**: **no — must be migrated**

### REST-012 — DELETE `/admin/api/{version}/api_permissions/current.json`

- **Source**: `backend/app/integrations/shopify/client.py` → `ShopifyClient.revoke_access_token`
- **Purpose**: Revoke the offline access token — uninstall the app from the API side.
- **Operation**: mutation
- **Scopes**: none beyond install
- **Tenant/store authority**: store_id -> ShopifyConnection (tenant-scoped)
- **Written locally**: ShopifyConnection status set to DISCONNECTED by the caller
- **Shopify side effect**: uninstalls the app for the shop
- **Idempotency**: idempotent; failures logged and ignored
- **Retry**: ShopifyClient generic retry; failures swallowed
- **Pagination**: n/a
- **Current tests**: **none**
- **Proposed GraphQL**: `mutation { appUninstall { app { id } userErrors { field message } } }`
- **Target phase**: GQL-6
- **Schema verification**: `verified`
- **Risk**: high
- **Production usage**: store disconnect
- **Removal status**: `present`
- **Permitted in a new public app**: **no — must be migrated**

### OAUTH-001 — GET `/admin/oauth/authorize`

- **Source**: `backend/app/integrations/shopify/auth.py` → `build_authorization_url`
- **Purpose**: Redirect the merchant to Shopify to grant scopes.
- **Operation**: oauth
- **Scopes**: none beyond install
- **Tenant/store authority**: normalise_shop_domain on merchant input
- **Written locally**: OAuth state in Redis
- **Shopify side effect**: none until the merchant approves
- **Idempotency**: safe
- **Retry**: n/a (browser redirect)
- **Pagination**: n/a
- **Current tests**: `backend/tests/unit/test_shopify_install.py`, `backend/tests/unit/test_shopify_shop_domain_uniqueness.py`
- **Proposed GraphQL**: `n/a — OAuth is not part of the Admin GraphQL surface`
- **Target phase**: none
- **Schema verification**: `not-applicable`
- **Risk**: low
- **Production usage**: install flow
- **Removal status**: `retained`
- **Permitted in a new public app**: yes

### OAUTH-002 — POST `/admin/oauth/access_token`

- **Source**: `backend/app/integrations/shopify/client.py` → `ShopifyClient.exchange_token`
- **Purpose**: Exchange the OAuth code for an offline access token.
- **Operation**: oauth
- **Scopes**: none beyond install
- **Tenant/store authority**: normalise_shop_domain plus HMAC-verified callback
- **Written locally**: ShopifyConnection.encrypted_access_token
- **Shopify side effect**: issues an access token
- **Idempotency**: single-use code; a replay fails at Shopify
- **Retry**: none
- **Pagination**: n/a
- **Current tests**: **none**
- **Proposed GraphQL**: `n/a — OAuth is not part of the Admin GraphQL surface`
- **Target phase**: none
- **Schema verification**: `not-applicable`
- **Risk**: low
- **Production usage**: install flow
- **Removal status**: `retained`
- **Permitted in a new public app**: yes

### GQL-000 — POST `/admin/api/{version}/graphql.json`

- **Source**: `backend/app/integrations/shopify/client.py` → `ShopifyClient.fetch_shop_currency_code` — **migrated in GQL-2** to `app/integrations/shopify/graphql_operations.py` → `fetch_shop_authority`
- **Purpose**: Read shop.currencyCode as the store-currency authority.
- **Operation**: query
- **Scopes**: none beyond install
- **Tenant/store authority**: store_id -> ShopifyConnection (tenant-scoped)
- **Written locally**: Store.currency, currency_last_synced_at
- **Shopify side effect**: none
- **Idempotency**: safe
- **Retry**: ShopifyGraphQLClient bounded retry (query)
- **Pagination**: n/a
- **Current tests**: `backend/tests/unit/test_shopify_gql2_operations.py`, `backend/tests/integration/test_shopify_gql2_currency.py`, `backend/tests/unit/test_m24a_currency_fx.py`
- **Proposed GraphQL**: `query ShopAuthority { shop { currencyCode } }`
- **Target phase**: GQL-2
- **Schema verification**: `verified`
- **Risk**: low
- **Production usage**: none - store connect and currency refresh now use graphql_operations.fetch_shop_authority; ShopifyClient.fetch_shop_currency_code is retained only for its existing unit test and has no production caller
- **Removal status**: `migrated`
- **Permitted in a new public app**: yes

> **Why the old method still exists.** `ShopifyClient.fetch_shop_currency_code`
> has zero production callers after GQL-2, but is not deleted:
> `backend/tests/unit/test_m24a_currency_fx.py` drives it directly, and the
> phase brief forbids weakening an existing test to make a migration look
> tidier. A drift test asserts the caller count stays at zero, so the method
> cannot quietly come back. Deleting it, and its test, belongs to GQL-6 along
> with the rest of the legacy client.

## What the sweep also found

Three things worth recording now rather than discovering mid-migration:

1. **`REST-002` is retried.** `ShopifyClient.request` retries POSTs on timeout
   and 5xx, so a product creation whose response was lost can run twice. The
   handle lookup in `REST-001` is the only thing standing between that and a
   duplicate product. GQL-3 must not inherit this shape.
2. **`REST-004` uses `locations[0]`.** The first location in an unpaginated,
   unordered list decides where every merchant's stock is set. A shop with
   several locations may be having its inventory written to the wrong one
   today; GQL-4 owes that an explicit choice, not a better-ordered guess.
3. **`REST-007` never follows a cursor.** Order import reads one page and
   stops, so a shop with more than `limit` new orders since the last poll
   loses the remainder permanently. GQL-5 inherits a bug, not just a call.

None of these are fixed in GQL-1 — this phase adds no behaviour — but each is
a requirement on the phase that owns the call.
