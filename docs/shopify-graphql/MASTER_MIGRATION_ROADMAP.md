# Shopify GraphQL migration — master roadmap

Shopify requires new public apps submitted to the App Store to use GraphQL
exclusively (*"all new public apps submitted to the App Store after this date
must only use GraphQL"*, effective 1 April 2025). This platform makes **twelve**
versioned Admin REST calls today. Each one is a submission blocker, each is
inventoried in [`REST_INVENTORY.md`](REST_INVENTORY.md), and each has a phase.

Pinned GraphQL Admin API version: **`2026-07`**.

## Phases

| Phase | Scope | Calls | Status |
|---|---|---|---|
| **GQL-1** | Shared GraphQL client and complete REST inventory | — | **complete** |
| **GQL-2** | Webhook registration and compliance-webhook audit | `REST-008`, `REST-009`, `GQL-000` | not started |
| **GQL-3** | Products, variants, media and publications | `REST-001`, `REST-002`, `REST-003`, `REST-006` | not started |
| **GQL-4** | Inventory, locations and unit cost | `REST-004`, `REST-005` | not started |
| **GQL-5** | Orders, fulfillment orders and line items | `REST-007` | not started |
| **GQL-6** | Disconnect/uninstall and removal of remaining Admin REST | `REST-010`, `REST-011`, `REST-012` | not started |

Only GQL-1 is complete. No later phase may be marked complete until its calls
are migrated, its replacements verified against the official reference, and its
inventory rows moved to `removed`.

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
