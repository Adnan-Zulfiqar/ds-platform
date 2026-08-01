# Shopify connection debug report

| | |
|---|---|
| Date | 2026-08-01 |
| Branch | `develop` |
| Store under test | `mriy3s-zv.myshopify.com` (custom domain `tenwer.com`) |

---

## Verdict

**Failure point: Shopify authorization screen (before DropPilot callback).**

The DropPilot OAuth *start* path works. Shopify never completes consent for this
app + store, so token exchange, encryption, and DB insert never run.

| Step | Result |
|---|---|
| Frontend Connect → `POST /integrations/shopify/connect` | **201** — `shopify_oauth_begun` |
| Authorization URL built | OK — `https://{shop}.myshopify.com/admin/oauth/authorize?...` |
| Shopify approve / install | **Fails** — browser "Unauthorized Access"; unauthenticated probe **403** on `admin.shopify.com/.../oauth/authorize` |
| Callback `GET /integrations/shopify/callback` | **Not reached** for real connect attempts |
| Code exchange / token encrypt / DB row | **Not executed** — `shopify_connections` count = **0** |

Earlier attempts with `tenwer.com` / `tenwer.shopify.com` were rejected by our
domain normaliser (correct). The real myshopify domain is `mriy3s-zv.myshopify.com`.

---

## Environment check (secrets masked)

| Variable | Status |
|---|---|
| `SHOPIFY_API_KEY` | configured (len 32) |
| `SHOPIFY_API_SECRET` | configured (len 38, `shpss_` prefix) |
| `SHOPIFY_CALLBACK_URL` | `https://api.whiteto.com/api/v1/integrations/shopify/callback` |
| Scopes | `read_products,write_products,read_inventory,write_inventory,read_orders,read_locations` |
| Whitespace issues | none |
| Public callback tunnel | reaches this backend (empty probe → 303 to frontend) |

---

## Root cause (most likely)

Shopify is rejecting the **install/authorize** request for this Client ID on
store `mriy3s-zv`. That is almost always app-configuration, not DropPilot code:

1. **Allowed redirection URL(s)** in the Shopify app must match
   `SHOPIFY_CALLBACK_URL` **exactly** (scheme, host, path, no trailing slash mismatch).
2. The Client ID/secret must be from the **same** app that has that redirect URL.
3. The app must be installable on this store (custom app enabled for the store,
   or Partner app with distribution that includes this shop).
4. Requested **scopes** must be enabled for that app.

DropPilot cannot complete OAuth until Shopify serves a consent page that
redirects back with `code` + `hmac` + `state`.

---

## What we changed (diagnostics only — no rewrite)

| Area | Change |
|---|---|
| Domain validation | Reject custom domains like `tenwer.com`; require `*.myshopify.com` |
| Callback errors | Distinct reasons: `hmac` / `state` / `exchange` / `failed` (query param + logs) |
| Logging | Safe fields only: shop, param names, status codes, error type — never secrets/codes/tokens |
| Frontend banners | Surface hmac/state/exchange messages on Integrations |
| Tests | Unit coverage for HMAC vs missing-state classification |

---

## Partner / custom app checklist

In Shopify Admin / Partner Dashboard for this app:

1. **Allowed redirection URL(s)** →  
   `https://api.whiteto.com/api/v1/integrations/shopify/callback`
2. Confirm Client ID matches `SHOPIFY_API_KEY` (first/last 4 chars: `a7dd…a53a`).
3. Confirm Client secret matches `SHOPIFY_API_SECRET` (same app).
4. Enable Admin API scopes listed above (or reduce `SHOPIFY_SCOPES` to match).
5. Re-try Connect with **`mriy3s-zv.myshopify.com` only**.

After a successful consent, backend logs should show `shopify_oauth_completed`
and Integrations should show a connected store.

---

## Remaining limitations

- Live OAuth still **unverified** until Partner/custom app redirect URL + install
  eligibility are confirmed (debt M17).
- Unauthenticated HTTP probes of `admin.shopify.com` may hit bot challenges;
  the authoritative signal remains the merchant browser ("Unauthorized Access")
  plus absence of callback logs.

---

## Tests executed

| Suite | Result |
|---|---|
| `pytest` Shopify auth + OAuth error + scoping units | **13 passed** |
| Frontend lint / typecheck / build | **pass** |
| Live Shopify consent | **blocked** at Shopify authorize (documented above) |
