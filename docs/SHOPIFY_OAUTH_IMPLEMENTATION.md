# Shopify OAuth implementation

How DropPilot connects a merchant's Shopify store: the OAuth flow, the security
controls around it, webhook registration, and the store connection lifecycle.
Written after auditing and fixing the existing implementation (2026-08-03) —
see [FULL_APPLICATION_AUDIT.md](FULL_APPLICATION_AUDIT.md) (A-04, A-09, A-15,
A-16) and [TECHNICAL_DEBT.md](TECHNICAL_DEBT.md) for the fix history.

---

## 1. What the merchant sees

The merchant never enters a Shopify API key, client secret, access token, or
admin token. Those belong to DropPilot's own Shopify Partner app and live only
in environment variables (`SHOPIFY_API_KEY`, `SHOPIFY_API_SECRET`) on the
backend. The merchant provides exactly one thing: their store domain.

1. Dashboard → Settings → Integrations → **Connect Shopify**.
2. A form asks for **store domain** only (`frontend/components/integrations/shopify-card.tsx`),
   accepting `store`, `store.myshopify.com`, or `https://store.myshopify.com` —
   all normalised server-side to `store.myshopify.com`
   (`normalise_shop_domain`, `backend/app/integrations/shopify/auth.py:23`).
3. **Continue** calls `POST /api/v1/integrations/shopify/connect`, then the
   browser is redirected immediately to the returned `authorizationUrl` — the
   real Shopify OAuth consent screen on the merchant's own store.
4. The merchant approves the app on Shopify's domain, not DropPilot's.
5. Shopify redirects the browser to
   `GET /api/v1/integrations/shopify/callback`. The backend validates the
   request, exchanges the code, encrypts and stores the token, and redirects
   back into the dashboard with `?shopify=connected`.
6. The connected-stores card shows: shop domain, connection status, when it
   was connected, last sync time, and any stored error — with reconnect
   (re-run the same flow) and disconnect available per store.

## 2. Endpoints

All under `/api/v1/integrations/shopify/` (`backend/app/api/v1/integrations/router.py`):

| Method | Path | Purpose | Auth |
|---|---|---|---|
| `POST` | `/connect` | Begin OAuth: validate the shop domain, issue state, return the authorization URL | Admin/owner |
| `GET` | `/callback` | Shopify's OAuth redirect target: validate, exchange, store, register webhooks, redirect to the frontend | None (state token is the credential) |
| `GET` | `/status` | List connected stores + whether the app is configured | Any authenticated role |
| `DELETE` | `/stores/{store_id}` | Disconnect one store | Admin/owner |
| `POST` | `/webhooks/{topic}` | Receive a Shopify webhook for `{topic}` | HMAC signature |
| `POST` | `/callback` | Shared callback path, webhook delivery when a tunnel can only forward one URL (topic from `X-Shopify-Topic`) | HMAC signature |
| `POST` | `/publish` | Publish a DropPilot product to a connected store | Admin/owner |
| `POST` | `/sync/orders` | Import orders from a connected store | Admin/owner |

There is no separate `GET /install` — `POST /connect` returns the
authorization URL and the frontend redirects the browser to it in the same
step, which is the identical merchant experience (validate-then-redirect) with
one difference: the initial call is authenticated (it must know which tenant
is connecting) rather than a bare redirect. This was a deliberate choice, kept
rather than renamed to `/install`, because the existing endpoint already does
everything the spec's `/install` would do and a rename buys nothing.

## 3. Security controls

### 3.1 Platform credentials, never merchant-supplied

`ShopifyService._require_app_credentials()` reads `settings.shopify.api_key`
and `settings.shopify.api_secret` — both server-side environment variables —
and raises `ShopifyConfigError` if either is missing. No request schema
anywhere in `app/integrations/shopify/schemas.py` has a field for an API key,
secret, or token; `ShopifyConnectRequest` accepts only `shop` and an optional
`store_name`.

### 3.2 OAuth `state` — CSRF and tenant binding

`begin_connection()` issues a random 32-byte URL-safe token
(`OAuthState.issue()`, `auth.py:14`) and stores it in Redis
(`shopify:oauth:state:{token}`) with a TTL (`SHOPIFY_OAUTH_STATE_TTL_SECONDS`),
keyed to a JSON payload: `tenant_id`, `user_id`, `shop_domain`, `store_name`.
No tenant identifier ever appears in the URL or is trusted from the client —
the callback restores tenant context entirely from what was stored server-side
against this token.

`complete_connection()` consumes the state exactly once
(`_consume_state` reads then deletes the Redis key) — a replayed callback with
a used `state` fails with `ShopifyOAuthStateError`. The shop domain in the
callback is compared against the shop domain stored at `/connect` time; a
mismatch is rejected as tampering, not silently accepted.

### 3.3 HMAC verification

Every query parameter Shopify sends to the callback (except `hmac` and
`signature`) is re-signed with the app secret and compared against the
`hmac` parameter using `hmac.compare_digest` (`verify_oauth_hmac`,
`auth.py:60`) — constant-time, so timing cannot leak the correct digest.
Failure raises `ShopifyOAuthHmacError` before anything else runs, including
before the state token is even consumed.

Webhooks are verified separately (`verify_webhook_hmac`, `auth.py:74`): the
raw request body is signed with the same app secret and compared, base64,
against `X-Shopify-Hmac-SHA256`. This is why the webhook handler reads
`request.body()` directly rather than a parsed model — verifying a
re-serialized body would not prove the bytes Shopify actually sent were
unmodified.

### 3.4 Token storage

The access token returned by Shopify's token exchange is encrypted with the
platform's existing Fernet/MultiFernet service
(`app.core.encryption.encrypt`) before it touches the database —
`ShopifyConnection.encrypted_access_token` is the only place it is persisted,
and nothing decrypts it except `client_for_store()` when a request needs to
call Shopify's Admin API on the merchant's behalf. `begin_connection()` and
`complete_connection()` both refuse to proceed if
`is_encryption_configured()` is false, rather than falling back to storing the
token in plaintext.

### 3.5 Shop domain uniqueness (A-01, already resolved)

`shop_domain` carries a **global** unique constraint (not per-tenant) —
migration `0012`. Two tenants cannot connect the same store: `begin_connection`
and `complete_connection` both check `ShopifyMaintenanceRepository.get_by_shop_domain`
and raise `ShopifyShopTakenError` if another tenant already owns it. Webhook
tenant resolution uses an indexed lookup on `shop_domain`
(`get_connected_by_shop_domain`) rather than scanning connections and taking
the first match — the earlier version of this could route a valid, HMAC-signed
webhook to the wrong tenant if the same shop were ever connected twice.

### 3.6 Webhook replay protection (A-09, resolved this pass)

Each webhook delivery is deduplicated via Redis (`SET NX EX`, 7-day TTL) keyed
on the request body. If Redis is unreachable, the check now **fails closed**
(`503`) specifically for mutating topics — `orders/create`, `orders/updated`,
`app/uninstalled` — so an outage cannot cause a duplicate order upsert or a
missed uninstall. Non-mutating topics (`products/*`, `inventory_levels/*`)
still acknowledge on the same outage, because DropPilot does not act on those
today (see §5) — a dedup failure there cannot produce a duplicate write.

## 4. OAuth flow, end to end

```
Merchant                Frontend                  Backend                    Shopify
   |  enters store URL     |                          |                          |
   |----------------------->|                          |                          |
   |                        | POST /shopify/connect    |                          |
   |                        |------------------------->|                          |
   |                        |                          | normalise shop domain    |
   |                        |                          | check shop not taken     |
   |                        |                          | issue + store state      |
   |                        |  { authorizationUrl }    |                          |
   |                        |<-------------------------|                          |
   |  browser redirected    |                          |                          |
   |----------------------------------------------------------------------------->|
   |                                    Shopify consent screen                    |
   |<-----------------------------------------------------------------------------|
   |  approves                                                                    |
   |------------------------------------------------------------------------------>|
   |                        |            GET /shopify/callback?code&state&hmac&shop
   |                        |<------------------------------------------------------|
   |                        |                          | verify HMAC              |
   |                        |                          | consume + verify state   |
   |                        |                          | verify shop == stored    |
   |                        |                          | exchange code for token  |
   |                        |                          |------------------------->|
   |                        |                          |    access_token, scope   |
   |                        |                          |<-------------------------|
   |                        |                          | encrypt + persist        |
   |                        |                          | register webhooks        |
   |                        |                          |------------------------->|
   |                        |  redirect ?shopify=connected                        |
   |<-----------------------------------------------------------------------------|
```

Failures redirect back to the frontend with a reason drawn from a fixed
vocabulary (`denied`, `hmac`, `state`, `exchange`, `failed`) rather than an
upstream Shopify message — the same pattern already used for the AliExpress
callback, so the two integrations fail predictably in the same way.

Webhook registration is **best-effort and non-blocking**: if it throws, the
exception is logged and swallowed (`router.py:285-289`) rather than undoing
the OAuth connection — a merchant should not see "connection failed" because
webhook registration hiccuped after the token exchange already succeeded.

## 5. Webhook lifecycle

`register_webhooks()` (`shopify/service.py:353`) registers six topics on every
successful connect or reconnect:

- `orders/create`, `orders/updated` — processed: upsert into DropPilot's order
  table (`ShopifySyncService.upsert_order_from_shopify`).
- `products/create`, `products/update`, `inventory_levels/update` —
  acknowledged (`200`) but **not processed**. DropPilot is the source of truth
  for the catalogue; a merchant edit made directly in Shopify Admin does not
  flow back automatically. This is a deliberate one-way-sync architecture
  decision, not an oversight — see §7 for its limitation.
- `app/uninstalled` — processed: marks the connection `IntegrationStatus.ERROR`
  immediately (`mark_error`), rather than waiting for the next Admin API call
  to fail with `401`. The access token is dead the instant Shopify sends this.

Each topic's delivery address is normally its own path,
`{SHOPIFY_WEBHOOK_CALLBACK_BASE}/{topic-with-dashes}`. `webhook_delivery_address()`
(`service.py:54`) has one exception: if the configured base is not itself a
`.../webhooks` address — e.g. a local-development tunnel that only forwards the
OAuth callback path — every topic is registered against that single URL
instead, and the receiver tells topics apart using the `X-Shopify-Topic`
header (the shared `POST /shopify/callback` route). This only matters for
local tunnel setups; a real deployment sets `SHOPIFY_WEBHOOK_CALLBACK_BASE` to
its own `.../webhooks` path and each topic gets its own address.

## 6. Store connection lifecycle

- **Multiple stores.** `ShopifyConnection` is keyed by `store_id`, one row per
  connected shop; a tenant can connect more than one Shopify store.
- **Reconnect.** Running `/connect` again for a shop already owned by the same
  tenant updates the existing `Store`/`ShopifyConnection` row in place
  (`complete_connection`'s `existing is not None` branch) rather than creating
  a duplicate — re-authorizing after a token was revoked, or Shopify rotated
  credentials, does not orphan the old row.
- **Disconnect.** `DELETE /shopify/stores/{store_id}` deletes the
  `ShopifyConnection` row (the encrypted token goes with it) and marks the
  `Store` `DISCONNECTED`. The frontend now catches a failed disconnect and
  shows a per-store error instead of the button silently doing nothing (A-15).
- **Health / status.** `GET /shopify/status` reports, per store: connection
  status, scopes granted, when connected, last sync time, last error, and
  whether webhooks are registered. Any authenticated role can read it —
  connection health is operational information every team member needs, not
  just admins.

## 7. AliExpress → Shopify publish path

Once a store is connected, `POST /shopify/publish` (backed by
`ShopifySyncService.publish_product`, `sync.py`) pushes a DropPilot product —
including anything imported from AliExpress and optimised through the existing
AI pipeline — to the connected Shopify store: creates or updates the Shopify
product, its variants and images, then records a `StoreListing` linking the
two.

**Duplicate-creation race (A-04, resolved this pass).** Celery's
`task_acks_late=True` gives at-least-once delivery: if a worker crashed after
Shopify accepted a `POST /products.json` but before the local `StoreListing`
committed, redelivery would `POST` again and create a second product with no
memory of the first. Fixed with a deterministic handle,
`droppilot-{product_id}` — Shopify's REST API has no create-idempotency key,
so a stable, content-addressed handle *is* the idempotency mechanism.
`_create_or_adopt()` searches `GET /products.json?handle=...&limit=1` first;
if a product with that handle already exists (from an earlier attempt whose
local commit never landed), it adopts that row instead of creating a new one.

Inventory and price pushes (`push_inventory`, `push_price`) and order import
(`import_orders`) were read and confirmed to follow the existing
`client_for_store` → encrypted-token → Admin API pattern; they were not
independently re-verified against a live store beyond what M17 already
records.

## 8. What is and is not verified

**Verified this pass** (unit + integration tests, no live Shopify store):

- OAuth HMAC verification, state issue/consume/expiry, shop-domain match
  enforcement — existing coverage, re-run and still green.
- `webhook_delivery_address()` — both branches (per-topic path, shared-tunnel
  fallback).
- Webhook replay-dedup failing closed (503) for a mutating topic and staying
  open for a non-mutating one, against a simulated Redis outage.
- `app/uninstalled` marking a connection `ERROR`, verified by reading back
  through a genuinely separate committed transaction (matching how the
  webhook handler itself reads, not the test's request-scoped session).
- Deterministic-handle publish idempotency (`_deterministic_handle`,
  `_create_or_adopt`) against a mocked Shopify client.
- Full backend gate: `ruff check`, `ruff format --check`, `mypy app --strict`
  (158 files), `pytest` (754 passed).
- Full frontend gate: `npm run lint`, `npm run typecheck`, `npm run build`.

**Not verified — no live Shopify Partner app credentials on this development
machine (M17, unchanged by this pass):**

- A real merchant completing the consent screen and landing back connected.
- A real Shopify webhook delivery (signed by an actual Shopify shop, not a
  hand-computed HMAC in a test).
- Live token exchange against Shopify's real `/admin/oauth/access_token`
  endpoint.
- A real product publish reaching a live store's catalogue.

This matches the existing, broader gap already recorded in
[TECHNICAL_DEBT.md](TECHNICAL_DEBT.md) M17 and
[SHOPIFY_CONNECTION_DEBUG_REPORT.md](SHOPIFY_CONNECTION_DEBUG_REPORT.md): the
architecture is sound and every piece that does not require a live Shopify
Partner app has been exercised, but nothing in this pass changes M17's status.
Closing it requires a development store and Partner app credentials, not more
code.

## 9. Known limitations

- **One-way catalogue sync.** Editing a product directly in Shopify Admin does
  not flow back to DropPilot — `products/*` and `inventory_levels/update`
  webhooks are acknowledged but not processed (§5). This is a deliberate
  scope boundary, not a bug: implementing the reverse direction means
  resolving conflicting edits, which is out of scope until a future phase
  calls for it.
- **Best-effort webhook registration.** A registration failure after a
  successful OAuth exchange is logged, not surfaced to the merchant as a
  connection failure. A store can therefore show `connected` with
  `webhooksRegisteredAt: null` — visible in `/status`, not currently alarmed
  on anywhere.
- **A-07 (open).** `GET /shopify/status` uses `CurrentPrincipal` rather than
  `RequireViewer` — an empty/revoked-role token within its access-token TTL
  can still read connection metadata. Tracked, not fixed in this pass.
- **A-08 (open).** `last_error` on a `ShopifyConnection` can contain
  `str(exc)` from an upstream or internal failure, which may leak an internal
  fragment to anyone who can read `/status`. Tracked, not fixed in this pass.
