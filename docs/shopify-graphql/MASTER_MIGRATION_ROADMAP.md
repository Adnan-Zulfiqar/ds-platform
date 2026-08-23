# Shopify GraphQL migration — master roadmap

Shopify requires new public apps submitted to the App Store to use GraphQL
exclusively (*"all new public apps submitted to the App Store after this date
must only use GraphQL"*, effective 1 April 2025). This platform made **twelve**
versioned Admin REST calls when the inventory was taken; **ten remain**. Each one is a submission blocker, each is
inventoried in [`REST_INVENTORY.md`](REST_INVENTORY.md), and each has a phase.

Pinned GraphQL Admin API version: **`2026-07`** — the latest *stable* release
as of 22 August 2026. `2026-10` is the release candidate and becomes stable on
1 October 2026; production does not run a release candidate.

## Phases

| Phase | Scope | Calls | Status |
|---|---|---|---|
| **GQL-1** | Shared GraphQL client and complete REST inventory | — | **complete** |
| **GQL-2** | Shop currency authority and shop-scoped webhook reconciliation | `REST-008`, `REST-009`, `GQL-000` | **complete** |
| **GQL-3** | Products, variants, media and publications | `REST-001`, `REST-002`, `REST-003`, `REST-006` | not started |
| **GQL-4** | Inventory, locations and unit cost | `REST-004`, `REST-005` | not started |
| **GQL-5** | Orders, fulfillment orders and line items | `REST-007` | not started |
| **GQL-6** | Disconnect/uninstall and removal of remaining Admin REST | `REST-010`, `REST-011`, `REST-012` | not started |

GQL-1 and GQL-2 are complete. No later phase may be marked complete until its
calls are migrated, its replacements verified against the official reference, and
its inventory rows moved to `removed` or `migrated` — a claim
`backend/tests/unit/test_rest_inventory.py` now checks against the code itself,
not just against the sibling document.

**Ten of twelve versioned Admin REST calls remain.** GQL-2 migrated two.

## Blueprint guards

Carried forward into every phase that touches the named area. Each exists because
it has already gone wrong somewhere, and none of them are optional.

1. **Complete variant maps before `productSet`.** The mutation replaces what it
   is given. A partial variant list is a deletion, not an omission.
2. **Never omit variants in a way that deletes them accidentally.** The corollary,
   stated separately because it is the failure mode, not the rule.
3. **Persist real Shopify GIDs, never array indexes.** A previous generation of
   this integration derived variant identity from position, so a reordered
   response repriced the wrong variant. `gid.py` has no API that accepts an index.
4. **Prefer `compareQuantity` where supported.** Blind quantity overwrites lose
   concurrent changes; `REST-005` does exactly that today.
5. **Verify 2026-07 unit-cost, media URL and Publications fields before
   implementing.** These three moved between recent versions. Read the reference;
   do not carry a field name forward from a blog or from memory.
6. **`appUninstall` replaces REST API-permission deletion.** Verified in the
   2026-07 reference during GQL-1: `appUninstall` returns `{ app, userErrors }`
   and may only be used by an app on itself. It is the replacement for
   `REST-012`.
7. **The final GQL-6 REST surface may retain only `graphql.json` plus the
   unversioned OAuth endpoints.** `OAUTH-001` and `OAUTH-002` stay; OAuth has no
   GraphQL equivalent. Anything else under `/admin/api/` must be gone.

## Non-migration milestones

Not every App Store blocker is a REST call, and a blocker with no milestone is
one nobody schedules. These sit outside the GQL-* sequence and can be done in
parallel with it.

| Milestone | Scope | Blocks submission | Status |
|---|---|---|---|
| **`SHOPIFY-COMPLIANCE-1`** | Mandatory privacy webhooks | **yes** | not started |
| `SHOPIFY-OPS-1` | Scheduled webhook reconciliation sweep | no | not started |

### `SHOPIFY-COMPLIANCE-1` — Mandatory privacy webhooks

**Blocks Shopify App Store submission.** Shopify requires every app to handle
three privacy topics, and an app that does not is rejected at review regardless
of how much of the Admin API it has migrated:

- `customers/data_request`
- `customers/redact`
- `shop/redact`

A sweep of `backend/app/` during GQL-2 found **no handler, no route and no topic
mapping** for any of the three. They are configured in the Partner Dashboard or
`shopify.app.toml` rather than created through `webhookSubscriptionCreate`, so
they are not a row in the REST inventory and no GQL-* phase would ever pick them
up — which is exactly why they are named here instead.

Deliberately **not** implemented in GQL-2 or its acceptance fix: the scope there
was `GQL-000`, `REST-008` and `REST-009`, and quietly widening it would have
shipped GDPR-relevant handlers nobody reviewed against a requirement.

### `SHOPIFY-OPS-1` — Scheduled webhook reconciliation sweep

**Does not block submission.** After the GQL-2 acceptance fix, recovery from a
failed webhook registration is *deterministic but manual*: the store is shown as
connected-but-degraded and an administrator clicks **Retry webhook setup**
(`POST /api/v1/integrations/shopify/stores/{store_id}/webhooks/reconcile`).

It would also close the one gap F-01b cannot: `webhooks_registered_at` records
when the last healthy reconciliation happened, and nothing observes a
subscription Shopify deletes afterwards. A periodic re-check is what would turn
"last confirmed" into something closer to "currently correct".

There is **no background job** that reconciles unhealthy stores on its own. The
reconciler is already idempotent and already serialised per store, so a periodic
sweep over connections with `webhooks_registered_at IS NULL` would be a thin
Celery task rather than new machinery — but it is a queue-shaped decision, and
GQL-2 was explicitly forbidden from introducing a second queue. Until it exists,
a merchant who never revisits the integrations page stays degraded.

## Definition of done for a migration phase

A phase is complete when, for every call it owns:

- the replacement operation is `verified` against the official 2026-07 reference;
- it runs through `ShopifyGraphQLClient` with an explicit `OperationType`;
- mutations check `userErrors` through the operation-layer contract;
- connections are walked with `PageWalker`, never an open loop;
- persisted identifiers are complete GIDs;
- the inventory row is moved to `removed` and the REST code is deleted, not left
  behind "just in case";
- the full backend suite passes and no new Playwright failure appears.

## What GQL-1 delivered

One `ShopifyGraphQLClient`, a GID value object, bounded pagination helpers, a
separate pinned GraphQL API version setting, and the fifteen-row inventory with
its drift guard. No REST call was migrated, no behaviour changed, and no live
Shopify request was made.

See [`GQL1_FOUNDATION.md`](GQL1_FOUNDATION.md) for the architecture decision, the
documentation citations and the known limitations.

## What GQL-2 delivered

The operation layer GQL-1 deferred, and the first three inventory rows moved
onto it: `shop.currencyCode`, the webhook listing and the webhook create. Same
topics, same delivery URIs, same shop-scoped model — only the transport changed.
Reconciliation is serialised per store with `SELECT … FOR UPDATE` on the
connection row, and the mutation is never retried automatically.

No migration was needed: no webhook identifier is persisted anywhere, so
`alembic heads` is unchanged at `0028`. No live Shopify request was made.

See [`GQL2_SHOP_WEBHOOKS.md`](GQL2_SHOP_WEBHOOKS.md) for the schema citations,
the shop-scoped-versus-TOML decision, the concurrency proof and the known
limitations.

### Carried into later phases

1. **`shopify.app.toml` subscriptions must not be introduced while per-shop
   subscriptions exist.** `webhookSubscriptions` returns only shop-scoped
   subscriptions, so running both modes for one topic delivers every event twice
   with no way for this codebase to detect it. Moving to config-managed
   subscriptions needs `webhookSubscriptionDelete` first, which is **GQL-6's**.
2. **The three mandatory privacy webhooks are missing** — see
   `SHOPIFY-COMPLIANCE-1` below.
3. **`WebhookSubscription.uri`, not `callbackUrl` or `endpoint`.** GQL-1's
   proposal for `REST-008` named a deprecated field. Later phases should treat
   every `unverified` row the same way: read the 2026-07 reference before
   implementing, never carry the proposal forward as if it were checked.
