# EBAY-C2 — Seller policies, marketplaces and inventory locations

Status: **implemented; verified against a mocked eBay transport only.** No live eBay seller account has been read or
written (C1's consent is still an owner step).

## Why this milestone exists

An eBay offer cannot be published without four seller-specific references:
a fulfillment (shipping) policy, a payment policy, a return policy, and an
Inventory API location (`merchantLocationKey`), all on one marketplace.
EBAY-C3 (draft → listing) needs them on every publish. C2 is the first use of
the C1 token: it reads what the seller already has on eBay, lets an admin
choose the defaults DropPilot will use, and can create the one inventory
location most sellers do not have yet.

## Scope

| In | Out (later milestone) |
|---|---|
| Read the seller's fulfillment, payment and return policies for one marketplace (Account API) | Creating or editing business policies (the seller does that in Seller Hub) |
| Report whether the seller has opted in to business policies (`SELLING_POLICY_MANAGEMENT`) | Opting the seller in on their behalf |
| Read the seller's Inventory API locations | Editing or disabling locations |
| Create one warehouse location (name + postal address) | Store pickup / fulfilment-centre locations |
| Store one set of listing defaults per tenant and marketplace | Per-product overrides (C3) |
| A "Listing setup" panel on the eBay card | Any listing, offer, price or order (C3–C5) |

Marketplaces: the fixed list in `EBAY_SUPPORTED_MARKETPLACES`
(US, GB, DE, AU, CA, FR, IT, ES). A list rather than eBay's full enum because
each marketplace a merchant can pick is one C3 must later price and publish
for; growing it is a one-line change once C3 handles a new one.

## Decisions

- **No new OAuth scope.** `sell.account` and `sell.inventory` were requested
  in C1's audited scope set (`EBAY_OAUTH_SCOPES`), so no connected merchant is
  sent back through consent.
- **Policies and locations are read live, not cached.** They change in Seller
  Hub without notice to us. Only the merchant's *choice* is stored, and it is
  re-validated against eBay on every save. C3 must still handle a default
  that eBay has since deleted (a publish-time error, not a silent fallback).
- **Every call goes through `access_token_for`** (master roadmap item 7). A
  401/403 is treated as a revoked grant, exactly as C1's identity call does.
- **The defaults table is eBay data tied to a seller account, so it is
  declared and erased in the same change** (master roadmap release guard).
  `ebay_listing_defaults` stores policy ids and a location key; its owner
  erases every row belonging to a connection whose `ebay_user_id` is the
  deletion subject, across all tenants. Rows also go when the connection is
  disconnected (`ON DELETE CASCADE` from `ebay_connections`), because the ids
  mean nothing without that seller.
- **Physical rows, not soft delete.** Same reasoning as `ebay_connections`:
  eBay's deletion contract requires irreversibility, and a soft-deleted row
  would keep the ids.
- **Location keys are generated, not typed.** `droppilot-` plus a random
  suffix: eBay keys are permanent and unique per seller, and a merchant has no
  reason to choose one.
- **Reads are allowed to members; writes need an admin.** Creating a location
  changes the seller's eBay account, and the defaults decide where every
  future listing ships from.

## API

| Method | Path | Role | Purpose |
|---|---|---|---|
| GET | `/api/v1/integrations/ebay/listing-setup?marketplaceId=EBAY_US` | member | Live policies, locations, opt-in state, supported marketplaces, saved defaults |
| PUT | `/api/v1/integrations/ebay/listing-defaults` | admin | Validate the four ids against eBay, then store them |
| POST | `/api/v1/integrations/ebay/locations` | admin | Create a warehouse location on eBay |

Errors (stable `code`): `ebay_not_connected` (409), `ebay_reconnect_required`
(existing, 422), `ebay_policy_not_found` (422 — a chosen id is not among the
seller's current ones), `ebay_seller_api_unavailable` (503, the platform's
`ExternalServiceError` status).

Workspace closure (`WorkspaceClosureService`) deletes `ebay_connections`,
which removes the defaults by cascade; the rows carry no user id, so
platform-user erasure has nothing to clear in them.

## Verification

- Integration tests (17) against `httpx.MockTransport` through C1's
  `FakeEbay` harness: read, opt-in off, roles, no connection, unsupported
  marketplace, 401 → reconnect, 5xx, save and re-save, stale/foreign/disabled
  ids refused, location created and choosable, address refused before eBay,
  disconnect cascades, deletion owner erases by immutable id and is
  idempotent. The C0 governance tests now require both declarations.
- Migration `0037` applied by the full gate on a disposable database
  (3498 passed). Downgrade written, not exercised by a test.
- Playwright: 7 route-mocked panel tests (owner, member, viewer, opt-in off,
  refusal, no warehouse) — 44/44 with the channels and eBay specs.

**Not verified:** any call to real eBay. That needs a connected seller
(OWNER: C1 consent on the sandbox or a real account, then open the panel).
