# Track E7 W1 — connect WooCommerce stores

Stage W1 of `docs/track-e/E7_WOOCOMMERCE_PLAN.md` (in the Track E proposals
PR). Status: **implemented; verified against a faked WooCommerce API only.**

## What it does

- **Where:** Settings → Integrations → WooCommerce (owner or admin only).
- **What the merchant enters:** a store name, the https:// site address, and
  a consumer key and secret created under WooCommerce → Settings → Advanced
  → REST API.
- **What the server does:**
  - Calls the store's `/wp-json/wc/v3/settings/general` with those keys.
  - Saves nothing unless the store accepts them.
  - Takes the store's verified currency from the same response.
  - Records the store with `platform = woocommerce`.
- **Reconnecting** the same site reuses its store.
- **Disconnect** forgets the keys and keeps the store and its listings.
  Revoking the key itself is done in WordPress.

## Decisions

- **No new table.** The keys live in the store's existing
  `encrypted_credentials` column, which was built for this. A separate
  connection table would be a second place to keep, and to erase, the same
  secret.
- **One SSRF contract.** The image fetcher's hop validation is now a shared
  `pin_https_target` in `app/ai/image_fetch.py`, used by both callers:
  - https only;
  - every resolved address must be globally routable (NAT64 and IPv4-mapped
    addresses are unwrapped first);
  - the connection is pinned to the vetted address;
  - redirects are refused, and the merchant is told the target.
- **Explicit readiness.** Shopify is now a named case in publish readiness.
  A future channel added without rules of its own is refused instead of
  being checked against Shopify's.
- **Fixed in passing:** workspace closure now also clears
  `stores.encrypted_credentials`. Before this it deleted the three
  connection tables but left credentials on store rows in place.

## Not done in W1

- The `/wc-auth/v1/authorize` approval flow. Pasting keys is the only path.
- Checking that a key has write permission. W2 will find this out on the
  first publish.
- **Known limitation, unchanged:** the generic `POST /stores` can still record
  any platform's credentials without checking them. W2 publishing will only
  use stores connected through this verified path.

## Verified

- `tests/unit/test_woocommerce_client.py`:
  - only https addresses are accepted;
  - private, loopback, link-local and CGNAT addresses are never contacted;
  - the request is pinned, uses Basic auth and sends the right Host header;
  - redirects are refused;
  - rejected keys are mapped to an error without echoing them;
  - a 404 is reported as unreachable;
  - an oversized response is cut off.
- `tests/integration/test_woocommerce_connect.py` (real Postgres) covers:
  - connect, with currency and encrypted storage;
  - a reconnect reusing the store;
  - rejected and malformed keys;
  - a private-address site;
  - the role checks;
  - cross-tenant access returning 404;
  - disconnect;
  - workspace closure.
- Playwright: `woocommerce-connect.spec.ts`, plus the channels, integrations
  and eBay specs that share the page.

## Not verified

- A real WooCommerce store (owner item).
