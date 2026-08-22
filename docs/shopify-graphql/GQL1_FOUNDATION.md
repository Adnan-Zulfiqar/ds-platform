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
* **`2026-10` is the release candidate, not a stable version.** An earlier
  revision of this document said it was "now the latest stable", which was
  wrong. Per the release table, `2026-07` released on 1 July 2026 and `2026-10`
  releases on **1 October 2026**; a release candidate is *"published on the same
  date as the stable release"*, so `2026-10` has been the RC since 1 July. As of
  22 August 2026 the latest **stable** version is `2026-07`, which is what
  production is pinned to. The setting is configurable so the quarterly move to
  `2026-10` after 1 October is a reviewed edit rather than a rewrite.

## Architecture decision

**Reuse the existing stack; add no second one.**

| Concern | Decision |
|---|---|
| HTTP | `httpx`, already the only client in the backend. Shopify's Node library is not introduced into a Python service. |
| Dependencies | **One added: `graphql-core`** (see below). A codegen toolchain is still declined — this is a parser, not a schema pipeline. |
| Errors | Extends `ShopifyError` / `app.core.exceptions`, so a GraphQL failure lands in the same envelope with the same `code` discipline as everything else. |
| Logging | The existing `structlog` `get_logger`, with the existing correlation id attached by middleware. |
| Tokens | Unchanged. Still encrypted at rest, still decrypted by `ShopifyIntegrationService.client_for_store`. GQL-1 changes no persistence. |
| Shop domain | The **existing** authority in `auth.py`, factored into `is_canonical_shop_domain` and shared. No second validator. |
| Settings | Extends `ShopifySettings`, next to the REST configuration it deliberately does not share. |

### The one dependency: `graphql-core`

Added by acceptance finding F-02. Retry eligibility must come from the document,
and deciding "is this a mutation" needs a real parser — the word appears in
comments, in fragment names and in field names, a document can hold several
operations of different kinds, and the failure mode of guessing is a duplicated
`productCreate`.

| | |
|---|---|
| Package | `graphql-core` |
| Version policy | `>=3.2.0,<4` — pinned below the next major, which is where spec-level breaking changes would land |
| Installed | 3.2.11 |
| Licence | MIT |
| Runtime dependencies | **none** on Python 3.13 (`typing-extensions` only below 3.10) |
| Why this one | The reference Python implementation of the GraphQL spec, a direct port of `graphql-js` |

Declared as a **direct production dependency** in `pyproject.toml`, not relied on
transitively: nothing else in this project pulls it in, and production code must
not import something that merely happens to be installed.

A regex or substring detector was explicitly rejected. It is not a smaller
version of this — it is a different, worse thing that fails silently on exactly
the documents where being wrong is dangerous.

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

### Version status as of 22 August 2026

| Version | Released | Status | Supported until |
|---|---|---|---|
| `2026-07` | 1 July 2026 | **latest stable — pinned here** | 16 July 2027 |
| `2026-10` | 1 October 2026 | release candidate | 16 October 2027 |

Do not move production to a release candidate. `2026-10` becomes eligible on
1 October 2026.

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
connection service, a static document, and separate variables. `operation_name`
may be omitted when the document defines exactly one operation. `operation_type`
is optional and is only an assertion.

### Operation classification and the retry invariant

**The parsed document decides whether a call may be retried. Nothing else.**

This was acceptance finding F-02. The first version took `OperationType` from the
caller and used it directly: a mutation declared as a query became eligible for
automatic retry, so a timed-out `productCreate` got three attempts and could
leave the merchant with three products. A behavioural probe against that code
recorded exactly that — three HTTP attempts at a mutation.

`operations.select_operation` now parses the document with `graphql-core` before
any request is made and resolves the operation using GraphQL semantics:

| Case | Behaviour |
|---|---|
| One operation, no `operationName` | selected; anonymous operations send no `operationName` |
| Several operations, valid `operationName` | that one is selected |
| Several operations, no `operationName` | local error |
| `operationName` not in the document | local error |
| Fragments before or after an operation | handled |
| Leading comments and whitespace | handled |
| Malformed document | local error, **no request** |
| Fragments-only document | local error, **no request** |
| `subscription` | local error — the Admin API here is request/response, and treating one as a query would give it query retries |
| Declared type disagrees with the parsed one | local error, **either direction** |

Failures raise `ShopifyOperationError` — a subclass of `ShopifyGraphQLError` so
existing handlers keep working, but distinct because the remedy differs: it is a
bug in a document or a call site, not an upstream condition to retry. It always
precedes the network, so it never describes something Shopify did.

The declaration is checked in both directions. Declaring a query as a mutation is
the harmless direction, but it is still a false statement about the document, and
a mismatch tolerated one way is a mismatch trusted the other.

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

**Mutations never retry automatically**, and after F-02 that is enforced by the
parsed document rather than by a caller's word. Replay safety is a per-operation
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

`gid.py` parses both documented shapes:

* `gid://shopify/{object_name}/{id}` — e.g. `gid://shopify/Product/123`
* `gid://shopify/{child}/{child_id}?{parent}_id={parent_id}` — the official
  example is `gid://shopify/InventoryLevel/123?inventory_item_id=456`

Parameterized GIDs were acceptance finding F-01: the first parser terminated the
id at `?` and rejected every one Shopify issues, which GQL-4 would have hit on
its first inventory call.

**The persistence contract.** The complete, opaque GID string is the authority.
`value` is exactly what Shopify sent — same characters, same parameter order,
same percent-encoding — and that is what is stored and what is sent back.
`resource`, `numeric_id` and `parameters` are *validation and convenience views*:
safe to read, log or branch on, never a basis for reconstructing an identifier. A
parameterized GID rebuilt from its parts would be a different string, and for an
`InventoryLevel` a different string addresses a different inventory level.

Nothing is specific to `InventoryLevel`. Shopify documents one parameter today
and promises nothing about tomorrow, so the *syntax* is validated — key shape,
value character class, percent-escape validity, no duplicate keys — and the
semantics are left to Shopify. An arbitrary query string is not treated as safe
just because it arrived attached to a GID: an empty query, a bare key, an empty
value, a duplicate key or a bad escape are all refused rather than repaired,
because each has more than one plausible reading and guessing which one Shopify
meant is not a choice a client should have.

`expect(resource)` still rejects the wrong type, because a `ProductVariant` GID
passed where an `InventoryItem` was meant parses perfectly and fails silently.
There is no API that accepts an array index — the blueprint guard about
identity-by-position is enforced by there being no way to do it.

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

## Deferred acceptance findings

Raised by the independent review, deliberately **not** implemented in this pass
so the acceptance fix stays the size it claims to be.

* **F-04 — version-calendar enforcement.** Nothing checks at runtime that the
  configured version is a released stable one rather than a future or
  release-candidate quarter. The format validator would accept `2027-10` today.
  Low risk while the value is a reviewed constant; worth a startup assertion
  against a maintained table when the first quarterly upgrade happens.
* **F-05 — API-version fall-forward detection.** Shopify returns
  `X-Shopify-API-Version` on every response, and it can differ from the version
  requested when the requested one is unsupported. This client does not compare
  them, so a silently fallen-forward version would go unnoticed until a schema
  difference bit. Cheap to add — one header comparison and a log — and it wants
  a deliberate decision about whether the mismatch should warn or fail.

Neither is a blocker for GQL-1: the version is pinned to a released stable
quarter, and the migration phases that would notice a schema difference have not
started.
