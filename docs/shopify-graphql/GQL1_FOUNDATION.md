# GQL-1 — shared GraphQL client foundation

The first phase of the Shopify App Store launch-readiness migration. It adds one
client and one inventory. It migrates **no** call: nothing in this phase changes
what the platform does to a merchant's shop.

## Why this phase exists

Shopify's changelog is the whole reason: *"all new public apps submitted to the
App Store after this date must only use GraphQL"*, effective 1 April 2025. Every
versioned Admin REST call in this repository is therefore a submission blocker.
[`REST_INVENTORY.md`](REST_INVENTORY.md) lists all twelve.

The phase deliberately builds the shared surface *before* migrating anything.
Migrating call-by-call onto whatever transport was nearest would give six
different retry policies, six different error models and six chances to leak a
token — and the sixth would be found in production.

## Official sources

Everything below was read from the current official documentation during this
phase. Nothing was written from memory of the schema.

| Fact | Source |
|---|---|
| `2026-07` is a released stable version, supported to 16 July 2027 | [API versioning](https://shopify.dev/docs/api/usage/versioning) |
| Endpoint `https://{shop}.myshopify.com/admin/api/2026-07/graphql.json`, `POST`, `X-Shopify-Access-Token`, `application/json` | [Admin GraphQL 2026-07](https://shopify.dev/docs/api/admin-graphql/2026-07) |
| `extensions.cost` → `requestedQueryCost`, `actualQueryCost`, `throttleStatus.{maximumAvailable,currentlyAvailable,restoreRate}` | [API limits](https://shopify.dev/docs/api/usage/limits) |
| A single query may not exceed 1,000 cost points | [API limits](https://shopify.dev/docs/api/usage/limits) |
| `errors[].extensions.code` includes `THROTTLED`, `MAX_COST_EXCEEDED`, `ACCESS_DENIED`, `SHOP_INACTIVE`, `INTERNAL_SERVER_ERROR` | [Admin GraphQL 2026-07](https://shopify.dev/docs/api/admin-graphql/2026-07) |
| `webhookSubscriptionCreate(topic:, webhookSubscription:)` returns `{ webhookSubscription, userErrors }` | [2026-07 reference](https://shopify.dev/docs/api/admin-graphql/2026-07/mutations/webhookSubscriptionCreate) |
| `appUninstall` returns `{ app, userErrors }` and can only be used by an app on itself | [2026-07 reference](https://shopify.dev/docs/api/admin-graphql/2026-07/mutations/appUninstall) |

**Two things the documentation does *not* say**, recorded so nobody later
mistakes an assumption for a fact:

* **`Retry-After` is not documented for GraphQL.** The limits page recommends a
  one-second backoff and says nothing about the header. The client honours it
  *if present* because doing so is free and correct, but the cost-derived wait
  is the real mechanism.
* **`2026-10` is now the latest stable version.** This phase pins `2026-07` as
  instructed; the setting is configurable precisely so the quarterly move is a
  reviewed edit rather than a rewrite.

## Architecture decision

**Reuse the existing stack; add no second one.**

| Concern | Decision |
|---|---|
| HTTP | `httpx`, already the only client in the backend. Shopify's Node library is not introduced into a Python service. |
| Dependencies | **None added.** `httpx` plus the existing typing tools cover this entirely; a GraphQL codegen toolchain would be a large dependency for a client that sends static documents. |
| Errors | Extends `ShopifyError` / `app.core.exceptions`, so a GraphQL failure lands in the same envelope with the same `code` discipline as everything else. |
| Logging | The existing `structlog` `get_logger`, with the existing correlation id attached by middleware. |
| Tokens | Unchanged. Still encrypted at rest, still decrypted by `ShopifyIntegrationService.client_for_store`. GQL-1 changes no persistence. |
| Shop domain | The **existing** authority in `auth.py`, factored into `is_canonical_shop_domain` and shared. No second validator. |
| Settings | Extends `ShopifySettings`, next to the REST configuration it deliberately does not share. |

### Why a new module rather than extending `ShopifyClient`

`ShopifyClient` is REST-first with a `graphql()` method bolted on. That method
retries on timeout regardless of operation type, raises a generic response error
for any top-level `errors`, never looks at `userErrors`, and ignores cost
entirely. Fixing it in place would change the behaviour of seven live REST
callers in a phase whose whole point is to change nothing.

So `ShopifyGraphQLClient` is the surface the migration moves onto, and
`ShopifyClient` is left untouched until each call migrates in its own phase.
Both exist for the duration of the migration; GQL-6 deletes the REST half.

## API version handling

```
SHOPIFY_API_VERSION=2026-07          # REST — legacy callers only
SHOPIFY_GRAPHQL_API_VERSION=2026-07  # GraphQL
```

Two settings, not one. They are on different migration clocks, and a single
shared value would let a REST pin silently drag GraphQL backwards with no
reviewer noticing. Both are validated against `\d{4}-(01|04|07|10)`, so
`latest`, `unstable` and typos like `2026-7` are refused **at configuration
load**, not at the first request. `/latest` is never constructed.

### Quarterly upgrade procedure

Shopify ships a stable version each quarter and supports each for at least
twelve months, with at least nine months of overlap. The support window for
`2026-07` ends **16 July 2027**.

1. Read the release notes for the target version, and the breaking-change list.
2. Change `SHOPIFY_GRAPHQL_API_VERSION` in one place; leave the REST setting alone.
3. Run the GQL suites — the endpoint assertion pins the version string, so a
   half-applied upgrade fails rather than shipping.
4. Re-verify every `verified` row in the REST inventory against the new
   reference; downgrade any that changed to `unverified`.

Never within nine months of a support window closing, and never as a side effect
of another change.

## The client

`backend/app/integrations/shopify/graphql.py`

### Required inputs

A canonical `.myshopify.com` domain, a decrypted token from the existing
connection service, a named operation, a static document, separate variables and
an **explicit** `OperationType`. Nothing is inferred.

The operation type matters more than it looks: it decides whether a failure is
retried. Sniffing the document for the word `mutation` would be guesswork — it
appears in comments, in fragment names, and in a query that merely mentions one —
so the caller states it or the call does not happen.

### URL and SSRF safety

The client **normalises nothing**. `is_canonical_shop_domain` is a predicate, not
a repairer: normalisation belongs at the OAuth boundary where a human typed
something, and by the time a token exists the stored domain is already canonical.
"Helpfully" repairing input here would give an attacker-supplied string a second
chance at the exact point the token is attached.

Refused: schemes, paths, ports, userinfo, query strings, fragments, uppercase,
surrounding whitespace, bare handles, `localhost`, IP addresses, multi-label
prefixes (`a.b.myshopify.com`), and suffix lookalikes
(`shop.myshopify.com.attacker.test`). The endpoint is built internally from the
validated domain and the configured version; a caller can never supply a URL.

Redirects are **not followed**. Shopify does not legitimately redirect this
endpoint, and following one would post the access token to whatever host the
`Location` header named.

### Token handling

The token is sent as `X-Shopify-Access-Token` and appears nowhere else. It is not
in `__repr__` or `__str__`, not in any log field, not in any exception message or
`details` payload, and not in the shared connection pool — which is created with
**no** auth headers precisely so it is a pool and not a global object holding one
merchant's credentials. Tests assert all of these rather than trusting them.

### Typed response

```python
GraphQLResponse(data, operation_name, api_version, request_id, cost, attempts)
```

Raw headers are not returned. Shopify's `X-Request-Id` is captured because their
support asks for it and it names a request rather than its contents.

### Error model

| Condition | Raised |
|---|---|
| Connect/read timeout | `ShopifyTimeoutError` |
| Transport failure (DNS, TLS, reset) | `ShopifyError` |
| HTTP 401 / 403, or `ACCESS_DENIED` | `ShopifyAuthError` |
| HTTP 429, or `THROTTLED` | `ShopifyThrottledError` |
| HTTP 502/503/504 | `ShopifyResponseError` (retryable) |
| Other HTTP ≥ 400 | `ShopifyResponseError` |
| Non-JSON body | `ShopifyResponseError` |
| Malformed response, missing `data` | `ShopifyGraphQLError` |
| Top-level `errors` | `ShopifyGraphQLError` |
| `MAX_COST_EXCEEDED` | `ShopifyQueryCostError` (never retried) |
| Mutation `userErrors` | `ShopifyUserError` |

Only `message`, `path`, `field` and the documented `code` survive into an error's
`details`. Everything else in a Shopify error entry can echo submitted values —
an offending title, an address — so it is dropped.

### Partial data fails closed

A response carrying both `data` and `errors` raises. `allow_partial_data=True`
exists so the choice is explicit and greppable; it is unused on every GQL-1 path.
Treating a partial response as success is how half a catalogue silently goes
unsynced and nobody finds out until a merchant does.

### Mutation `userErrors`

Transport success is not mutation success. `raise_for_user_errors(payload,
mutation_field=...)` takes the field name **from the caller** because Shopify
puts the payload under the mutation's own name and each has its own error type —
a client that went hunting for "something that looks like userErrors" would
eventually find the wrong list, or miss one and report a refused mutation as
success. A document that forgot to select `userErrors` is refused outright rather
than treated as clean.

### Cost and throttling

`extensions.cost` is parsed defensively: every field optional, every value
type-validated, and a malformed block can never fail a response whose `data` is
good. Cost is telemetry; the merchant's data arrived either way.

Wait time on a throttle, in order of preference:

1. `Retry-After`, integer seconds or HTTP-date, if present and in the future;
2. `(requestedQueryCost − currentlyAvailable) / restoreRate`, when all three are
   present and the deficit is positive;
3. bounded exponential backoff.

All three are capped at 30 s per wait and 60 s in total across a call, with
proportional jitter. The jitter bound is what the tests assert — the only way to
test a random number without pinning a seed.

### Retry policy

Retried (queries only): connect/read timeout, HTTP 429, 502/503/504, and
`THROTTLED`.

Never retried: 400, 401, 403, validation errors, `MAX_COST_EXCEEDED` (the same
document costs the same next time, so retrying converts an authoring mistake into
a slow outage), and `userErrors`.

**Mutations never retry automatically in GQL-1.** Replay safety is a per-operation
question — does Shopify dedupe it, is there an idempotency key, would a second
run create a second product — and the operation layer is where that is known.
`REST-002` in the inventory is the cautionary example: it is a POST that the
current REST client *does* retry.

### HTTP lifecycle

One process-wide `AsyncClient` with explicit connect/read/write/pool timeouts and
a bounded pool (20 connections, 10 keep-alive), created lazily and closed from the
application lifespan alongside the database and Redis pools. It carries no
credentials. Retry sleeps are plain `await asyncio.sleep`, so cancellation
propagates and a shutting-down worker is not held open by a backoff.

## GID and pagination foundations

`gid.py` parses `gid://shopify/{Resource}/{id}`, retains the **complete** GID as
the thing to persist, and offers `expect(resource)` because a `ProductVariant`
GID passed where an `InventoryItem` was meant parses perfectly and fails
silently. There is no API here that accepts an array index — the blueprint guard
about identity-by-position is enforced by there being no way to do it.

`pagination.py` supplies `PageInfo`, a bounded `PageWalker` and
`connection_nodes`. Cursor-only, explicit page and node budgets, duplicate-cursor
detection, and `hasNextPage: true` with no `endCursor` refused — that shape makes
a naive loop re-request page one forever. Exhausting a budget **raises**: a
truncated sync that looks complete is worse than a loud failure, because the
missing half is invisible.

GQL-1 migrates no catalogue endpoint. The bounds exist before the first caller
that needs them.

## What GQL-1 does not do

No REST call is migrated, removed or changed. No migration, no schema change, no
scope change, no OAuth change, no frontend change. No live Shopify request was
made during this phase — every test runs through `httpx.MockTransport`.

## Known limitations

1. **`ShopifyClient` is untouched and still REST-first.** Two Shopify clients
   exist for the duration of the migration. That is deliberate, and GQL-6 removes
   the REST half.
2. **Most proposed replacements are `unverified`.** Ten of the fifteen inventory
   rows have not had their exact 2026-07 fields read in the official reference.
   Verifying them is the first task of the phase that owns each call, and
   guessing now would be worse than the gap.
3. **`Retry-After` handling is defensive, not documented.** See above.
4. **No live verification.** The client has never spoken to Shopify. The request
   shape is asserted against the documented contract, not against a live shop —
   a real call belongs to the first phase that migrates an operation.
5. **The shared pool is process-wide.** Correct for a pool, but it means a
   pathological shop can occupy connections other shops would use. Bounded at 20;
   revisit if a per-tenant limit is ever needed.
