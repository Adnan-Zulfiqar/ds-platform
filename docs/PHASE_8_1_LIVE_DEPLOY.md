# Phase 8.1 — live deploy / tunnel verification addendum

| | |
|---|---|
| Date | 2026-08-03 |
| Branch | `develop` |
| Prior tag | `phase-8-1-complete` → `315d65f` (**not moved**) |
| Follow-up from | [PHASE_8_1_VERIFICATION.md](PHASE_8_1_VERIFICATION.md) |
| Re-verify tip base | `df9c22b` |

---

## 1. Git (2026-08-03 re-verify)

| Item | Value |
|---|---|
| Branch | `develop` @ `df9c22b` (synced `origin/develop`) |
| `main` | Untouched (`82de677`) |
| Dirty tree (left alone) | `backend/app/models/product.py` — Product Editor parallel WIP; **not** part of Shopify commits |
| Stashes **preserved** (not restored) | `stash@{0}: On develop: wip: leftover product editor test` |
| | `stash@{1}: On develop: wip: product editor (parked for Shopify live deploy)` |

---

## 2. Local routes

Backend on `127.0.0.1:8000` (Redis `6379` reachable).

| Method | Path | Status |
|---|---|---|
| GET | `/api/v1/integrations/shopify/install` | **303** |
| GET | `/api/v1/integrations/shopify/callback` | **303** |
| POST | `/api/v1/integrations/shopify/claim-install` | **401** |
| POST | `/api/v1/integrations/shopify/webhook` | **401** |
| GET | `/health/live` | **200** |

---

## 3. Cloudflare routing — before and after

### Agent

Windows service `cloudflared` (Automatic), `tunnel run --token-file C:\ProgramData\cloudflared\token`. Remotely managed; **no** local ingress `config.yml`. This repository cannot change Public Hostname rules.

### Before (first live-deploy pass) and after (this re-verify)

**Unchanged.** Still path-scoped — not a full hostname → `http://localhost:8000` proxy.

| Path | Public result | Hits FastAPI? (`x-request-id`) |
|---|---|---|
| GET `/api/v1/integrations/shopify/callback` | **303** | **Yes** |
| POST `/api/v1/integrations/shopify/callback` | **401** | **Yes** |
| GET `/api/v1/integrations/shopify/install` | **404** bare CF | **No** |
| POST `/api/v1/integrations/shopify/claim-install` | **404** bare CF | **No** |
| POST `/api/v1/integrations/shopify/webhook` | **404** bare CF | **No** |
| POST `/api/v1/integrations/shopify/webhooks/orders-create` | **404** bare CF | **No** |
| GET `/health/live` | **404** bare CF | **No** |

**Conclusion:** Cloudflare ingress remains incorrect for Phase 8.1 App URL installs. Backend code was **not** changed to paper over the 404.

### Required human fix (Zero Trust)

Preferred: Public Hostname `api.whiteto.com` → service `http://localhost:8000` with path `*` / empty.

Minimum paths if keep allowlist: `/install`, `/webhook`, `/webhooks/*`, keep `/callback`. Do not alter unrelated DNS.

---

## 4. Canonical webhook architecture

| Role | Route | Notes |
|---|---|---|
| **Canonical shared webhook** | `POST …/shopify/webhook` | Dedicated; topic from `X-Shopify-Topic`; HMAC + replay |
| Per-topic alternative | `POST …/shopify/webhooks/{topic}` | When base ends with `/webhooks` |
| OAuth callback | `GET …/shopify/callback` | Code exchange only |
| Shared fallback | `POST …/shopify/callback` | Explicit HMAC webhook receiver (`shopify_webhook_via_callback`) for path-scoped tunnels only |

**Canonical public URL once Cloudflare forwards it:**

`https://api.whiteto.com/api/v1/integrations/shopify/webhook`

Until then, live registration must use the only public HMAC receiver:

`https://api.whiteto.com/api/v1/integrations/shopify/callback`

`.env.example` now defaults the example base to singular `/webhook` and documents the callback fallback. Do **not** point production webhooks at callback after `/webhook` is public.

Covered by existing unit/integration tests: HMAC reject, topic dispatch, replay/idempotency, `app/uninstalled`, fail-closed mutating topics. **Live** webhook registration still unverified (no completed OAuth).

---

## 5. Partner Dashboard

| Setting | Value | Verifiable from code? |
|---|---|---|
| App URL | `https://api.whiteto.com/api/v1/integrations/shopify/install` | Route exists; **Dashboard value** visual only |
| Allowed redirection | `https://api.whiteto.com/api/v1/integrations/shopify/callback` | Matches config field name; Dashboard visual only |
| Embedded | Off | Code has no App Bridge / session-token path |
| Scopes | `read_products,write_products,read_inventory,write_inventory,read_orders,read_locations` | Code default + `.env.example` |
| Distribution / custom link | Custom and/or Public | **Dashboard only** |

API secret: never printed.

---

## 6. Live OAuth

**Blocked** by public GET `/install` **404**. Per verification gate, App URL / full public-install round trip was not started this pass.

| Step | Result |
|---|---|
| Public install reachable | **No** (404) |
| Merchant consent / callback / encrypt / tenant / UI / webhooks / disconnect / reconnect | **Not run** |
| **M17** | **Still open** |

Typed Connect can still start OAuth against localhost API with public callback, but closing M17 for this pass requires public `/install` per the live-deploy checklist.

---

## 7. Global shop ownership safety

| Control | Status |
|---|---|
| Global unique `shop_domain` | Model + migration `20260803_0800_0012_shopify_shop_domain_global_unique` |
| Connect rejects foreign owner | `ShopifyShopTakenError` via maintenance repo lookup |
| Webhook resolve | `get_connected_by_shop_domain` → `scalar_one_or_none()` (not ambiguous `.first()`) |
| Tests | `test_shopify_shop_domain_db.py`, `test_shopify_shop_domain_uniqueness.py` |

Not a release blocker for uniqueness — already fixed. Live multi-tenant collision still unverified without real installs.

---

## 8. Gates (re-verify)

| Gate | Result |
|---|---|
| Backend ruff check / mypy / pytest | Pass / Pass / **829 passed** |
| `ruff format --check .` | Contaminated by parallel PE migration WIP; `app`+`tests` format clean |
| Frontend lint / typecheck / build | Pass |
| Playwright Shopify + integrations + shell (chromium + mobile-chrome) | **88 passed**, 0 failed, 0 skipped, 0 flaky |
