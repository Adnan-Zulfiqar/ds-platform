# Phase 8.1 plan — Shopify OAuth to production quality

| | |
|---|---|
| Date | 2026-08-03 |
| Branch | `develop` only — **do not touch `main`** |
| Status | Plan + implementation scope |
| Depends on | Phase 8 complete (`phase-8-complete`), [SHOPIFY_INSTALL_FLOW_AUDIT.md](SHOPIFY_INSTALL_FLOW_AUDIT.md), [SHOPIFY_PRODUCTION_INSTALL_PLAN.md](SHOPIFY_PRODUCTION_INSTALL_PLAN.md) |
| Official refs | [Authorization code grant](https://shopify.dev/docs/apps/build/authentication-authorization/access-tokens/authorization-code-grant), [App Store §2.3.1](https://shopify.dev/docs/apps/launch/shopify-app-store/app-store-requirements), [Verify HMAC](https://shopify.dev/docs/apps/build/authentication-authorization/access-tokens/authorization-code-grant#step-2-verify-the-installation-request) |

---

## Goal

Make DropPilot’s Shopify install behave like AutoDS / DSers / Printful for merchants:

- **Never** ask for API key, API secret, or access token (platform `.env` only).
- Typed shop domain **only** for Connect-from-DropPilot (custom / private-style install).
- Public / App Store installs: Shopify provides `shop` (+ `hmac`, `timestamp`, often `host`) to the App URL; DropPilot validates HMAC and redirects to authorize.
- Do **not** fake OAuth, HMAC, or live verification.

---

## Part 1 — Audit findings (code on `develop` as of 2026-08-03)

Verified by reading the files below. Severity: Critical / High / Medium / Low / Info.

### Files audited

| Area | Path |
|---|---|
| OAuth helpers | `backend/app/integrations/shopify/auth.py` |
| Lifecycle | `backend/app/integrations/shopify/service.py` |
| Webhooks | `backend/app/integrations/shopify/webhook.py` |
| HTTP client | `backend/app/integrations/shopify/client.py` |
| Exceptions / schemas | `exceptions.py`, `schemas.py` |
| Routes | `backend/app/api/v1/integrations/router.py` |
| Model / repo | `models/shopify.py`, `repositories/shopify.py` |
| UI | `frontend/components/integrations/shopify-card.tsx` |
| Services / types | `frontend/services/integrations.ts`, `types/api.ts` |
| Env template | `.env.example` (Shopify section) |

### Critical

| # | Finding | Evidence |
|---|---|---|
| C1 | **No App URL install handler** — cannot receive Shopify `?shop&hmac&timestamp&host` | Router Shopify routes ~243–401; no `GET …/install` |
| C2 | **No tenant claim / pending-install path** — OAuth state always requires authenticated `POST /connect` | `service.begin_connection` + `require_tenant_id`; no claim API |
| C3 | **Webhook processing always ACKs after failures** — mutating work (orders/uninstall) exceptions swallowed; Shopify will not retry | `webhook.py` ~132–135 |

### High

| # | Finding | Evidence |
|---|---|---|
| H1 | **Reconnect after disconnect can fail** on `uq_stores_tenant_slug` — disconnect hard-deletes connection, leaves `Store`; next OAuth `create` reuses slug | `service.disconnect` + `complete_connection` else-branch |
| H2 | **Disconnect does not revoke** Shopify access or unregister webhooks | `disconnect` deletes local row only |
| H3 | **`app/uninstalled` leaves ERROR row** that still claims global `shop_domain` | `webhook.py` `mark_error`; uniqueness on `shop_domain` |
| H4 | **Webhook base singular `/webhook` has no route** — only `/webhooks/{topic}` and POST `/callback` | `webhook_delivery_address`; local `.env` has been observed with `…/webhook` |
| H5 | **Typed shop domain required** — blocks App Store §2.3.1 public install | `shopify-card.tsx` always shows domain field |
| H6 | **Webhook registration best-effort**; OAuth still `connected` if registration fails | `router` callback try/except |
| H7 | **`register_webhooks` not idempotent** — reconnect re-POSTs all topics | `service.register_webhooks` loop |

### Medium

| # | Finding | Evidence |
|---|---|---|
| M1 | `configured` ignores encryption readiness | `list_status` vs `begin_connection` |
| M2 | Callback maps only HMAC/state/exchange; shop-taken → generic `failed` | `router` callback |
| M3 | Token exchange before final ownership check (race window) | `complete_connection` |
| M4 | Merchant banners leak operator env names | integrations page + card copy |
| M5 | OAuth `state` returned in JSON before redirect (unnecessary) | connect response schema |
| M6 | `shop_id` column never set | model vs service |
| M7 | ERROR connections listed; badge uses connection count not status | `shopify-card.tsx` |
| M8 | Return URL query concat breaks if `frontend_return_url` already has `?` | `router` |

### Low / Info (not blocking 8.1)

- Offline tokens: **no false refresh path** (correct).
- OAuth URL shape correct: `https://{shop}/admin/oauth/authorize`.
- State: Redis one-time, TTL, tenant/shop bind — solid for DropPilot-started flow.
- Encryption: Fernet required on connect/complete.
- Global `shop_domain` uniqueness + maintenance pre-check (A-01).
- Domain normalisation rejects custom storefront domains.
- Webhook HMAC + fail-closed replay for mutating topics (A-09).

### What already matches the goal

- Frontend **does not** collect merchant API key / secret / token (domain only).
- Platform credentials live in server `.env`.
- Callback HMAC verification exists.
- Tokens stored encrypted.

### Live status (do not claim success)

| Step | Result (last probe 2026-08-03) |
|---|---|
| Credentials / `configured` | Yes (local `.env`) |
| `POST /connect` → authorize URL | 201 |
| Authorize for `mriy3s-zv.myshopify.com` | **403** (Partner install eligibility / App URL / Allowed redirection — human Dashboard) |
| `shopify_connections` after connect | **0** (M17) |
| Webhook singular `/webhook` | Would register **404** vs code routes |

---

## Part 2 — App install flow (implement)

### `GET /api/v1/integrations/shopify/install`

Behaviour (Shopify App URL):

1. Require platform credentials + encryption configured.
2. If query contains `shop`, `hmac`, `timestamp` (and optional `host`):
   - Verify HMAC per Shopify docs (sign all params except `hmac`/`signature`).
   - Reject invalid HMAC → redirect frontend `?shopify=hmac` or 401 for non-browser.
   - Normalise `shop` → `*.myshopify.com`; reject invalid / localhost / non-myshopify.
3. **Tenant bind:**
   - If authenticated admin with tenant: write Redis OAuth state with `tenant_id`, redirect **303** to Shopify authorize.
   - If anonymous: issue short-lived **install ticket** (HMAC already verified), redirect **303** to frontend `?shopify=claim_needed&shop=…&install_token=…`. Frontend (logged-in admin) calls `POST /shopify/claim-install` → authorize URL → browser redirect.
4. Without Shopify HMAC params: **400** — install is App URL only; Connect button uses `POST /connect`.

### Connect button (existing, hardened)

- `POST /shopify/connect` remains: admin + typed `shop` only (no keys).
- Normalise `shop` / `store` / `https://shop.myshopify.com` → `shop.myshopify.com`.
- Design: App Store later can open Integrations with `?shop=` / install ticket and **skip** the domain dialog.

---

## Part 3 — Remove API key UX

- Verify no merchant-facing inputs for key/secret/token (already true).
- Soften operator copy that names `SHOPIFY_API_KEY` / secret on the Integrations UI (merchant-facing pages should not instruct merchants to set secrets).

---

## Part 4 — Connect button UX

| Mode | Flow |
|---|---|
| **Typed (today)** | Connect → dialog → domain → normalise → `POST /connect` → `location.assign(authorize)` |
| **App / claim** | Install or return with `install_token` / `shop` → skip dialog → claim or connect → authorize |
| **Future App Store** | Same App URL path; dialog skipped when shop already known |

---

## Part 5 — Webhook registration

| Topic | Required |
|---|---|
| `products/create` | Yes |
| `products/update` | Yes |
| `inventory_levels/update` | Yes |
| `orders/create` | Yes |
| `orders/updated` | Yes |
| `app/uninstalled` | Yes |

`SHOPIFY_WEBHOOK_CALLBACK_BASE` must resolve to a live receiver:

| Base ends with | Delivery |
|---|---|
| `/webhooks` | `…/webhooks/{topic-with-hyphens}` |
| `/callback` | Shared URL; topic from `X-Shopify-Topic` |
| `/webhook` (singular) | Shared URL — **add route** POST `/shopify/webhook` |
| Anything else | **Reject** at registration (`ShopifyWebhookConfigError`) |

Make registration **idempotent** (skip identical address+topic).

---

## Part 6 — Token handling

| Concern | Action |
|---|---|
| Encrypted storage | Keep Fernet; `configured` requires encryption |
| Refresh | N/A for offline Admin tokens — do not invent refresh |
| Disconnect | Best-effort revoke + delete webhooks, then hard-delete connection, leave store `DISCONNECTED` |
| Uninstall webhook | Release shop claim (delete connection + disconnect store), not ERROR-with-token |
| Tenant ownership | Keep global uniqueness + pre-check |
| Reconnect | Reuse existing store by slug / `external_store_id` |
| Multiple stores | Unchanged — multiple connections per tenant allowed; one shop globally |

---

## Part 7 — Store validation

Reject: invalid labels, `localhost`, non-`*.myshopify.com` custom domains, duplicate foreign-tenant shops.

Normalise: `Shop`, `shop`, `shop.myshopify.com`, `https://shop.myshopify.com/` → `shop.myshopify.com`.

---

## Part 8 — Live verification

Use a real Shopify development store. Checklist:

Install URL → Consent → Callback → Token exchange → Webhooks → Encrypted row → Status → Disconnect → Reconnect.

**Do not claim success unless verified.** If Partner Dashboard / authorize 403 blocks, document the exact gap (M17).

---

## Part 9 — Frontend

- Connected / disconnected / error / sync / last sync / reconnect / disconnect.
- Loading + empty states without placeholder garbage.
- Dialog for typed connect; skip when App/claim params present.
- No merchant API-key fields.

---

## Part 10 — Testing

Backend: `ruff`, `ruff format --check`, `mypy app`, `pytest`.

Frontend: `lint`, `typecheck`, `build`, Playwright (connect, invalid domain, disconnect/reconnect where mockable, callback query banners). Live OAuth only if credentials allow; else document.

---

## Part 11 — Documentation

- `docs/PHASE_8_1_COMPLETION.md`
- Update `PROJECT_ROADMAP.md`, `CHANGELOG.md`, `TECHNICAL_DEBT.md`

---

## Part 12 — Git

Logical commits on `develop`. Tag `phase-8-1-complete`. Push branch + tag. Clean tree / origin synced. **Never touch `main`.**

---

## Implementation order

1. Commit prior audit docs if still unstaged.
2. Auth normalisation + webhook base validation + singular webhook route.
3. Service: install ticket, claim, reconnect store reuse, disconnect revoke, uninstall release claim, idempotent webhooks, mutating webhook fail.
4. Router: `GET /install`, `POST /claim-install`.
5. Frontend card + integrations banners.
6. Unit / integration / Playwright updates.
7. Quality gates + live notes.
8. Completion docs + tag + push.

---

## Risks & assumptions

| Risk | Mitigation |
|---|---|
| App Store distribution not chosen in Partner Dashboard | Code supports App URL; live install still needs human Dashboard config |
| Authorize 403 (M17) | Cannot fake; document gap in completion report |
| Revoke API shape differs by API version | Best-effort; never fail disconnect if revoke fails |
| Anonymous App URL → claim | Required for multi-tenant; state never invents a tenant |

## Out of scope

- Embedded App Bridge / session tokens
- Fulfilment push
- Changing AliExpress, catalogue, inventory, pricing, order automation beyond Shopify install hardening
- Touching `main`
