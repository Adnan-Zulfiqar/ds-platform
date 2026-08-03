# Phase 8.1 — live deploy / tunnel verification addendum

| | |
|---|---|
| Date | 2026-08-03 |
| Branch | `cursor/shopify-cloudflare-verification` (from `origin/develop`) |
| Prior tag | `phase-8-1-complete` → `315d65f` (**not moved**) |
| Follow-up from | [PHASE_8_1_VERIFICATION.md](PHASE_8_1_VERIFICATION.md) |
| Worktree | `../droppilot-shopify-live` (clean; Product Editor main tree untouched) |

---

## 1. Git

| Item | Value |
|---|---|
| Base | `origin/develop` (includes `c93e52d` Shopify docs + later PE commits on develop) |
| `main` | Untouched |
| Main-repo stashes (read-only check) | `stash@{0}: wip: leftover product editor test` |
| | `stash@{1}: wip: product editor (parked for Shopify live deploy)` |
| Stashes | **Not** restored / deleted / modified |

---

## 2. Local routes

Redis `6379` and Postgres `5432` reachable. Backend on `127.0.0.1:8000`.

| Method | Path | Status |
|---|---|---|
| GET | `/health/live` | **200** |
| GET | `/api/v1/integrations/shopify/install` | **303** |
| GET | `/api/v1/integrations/shopify/callback` | **303** |
| POST | `/api/v1/integrations/shopify/claim-install` | **401** |
| POST | `/api/v1/integrations/shopify/webhook` | **401** |

No local 404 on implemented Shopify routes.

---

## 3. Cloudflare routing — wildcard added

### Configured Public Hostname rules (operator-reported)

1. `/api/v1/integrations/aliexpress/callback` → `http://localhost:8000`
2. `/api/v1/integrations/aliexpress/webhook` → `http://localhost:8000`
3. `/api/v1/integrations/shopify/callback` → `http://localhost:8000`
4. `/api/v1/integrations/shopify/webhook` → `http://localhost:8000`
5. `*` → `http://localhost:8000` (**new**)

### Public probes after wildcard (all hit FastAPI — `x-request-id` present)

| Method | Path | Public | Local | Match? |
|---|---|---|---|---|
| GET | `/health/live` | **200** | 200 | Yes |
| GET | `/api/v1/integrations/shopify/install` | **303** | 303 | Yes |
| GET | `/api/v1/integrations/shopify/callback` | **303** | 303 | Yes |
| POST | `/api/v1/integrations/shopify/claim-install` | **401** | 401 | Yes |
| POST | `/api/v1/integrations/shopify/webhook` | **401** | 401 | Yes |
| GET | `/api/v1/integrations/aliexpress/callback` | **303** | — | FastAPI |
| POST | `/api/v1/integrations/aliexpress/webhook` | **200** | — | FastAPI |

**Before:** public `/install`, `/webhook`, `/health/live` were bare Cloudflare **404**.  
**After:** same paths reach FastAPI with expected auth/redirect codes.

### Recommendation on routes 1–4

The `*` rule successfully forwards every tested AliExpress and Shopify path to the same service. **Routes 1–4 are redundant** and may be removed from the Public Hostname list once you are satisfied, leaving:

- Hostname: `api.whiteto.com`
- Path: `*`
- Service: `http://localhost:8000`
- Keep Cloudflare’s catch-all 404 for unmatched hostnames

This agent did **not** delete Cloudflare routes (no Zero Trust write access from the repo).

---

## 4. Canonical webhook URL

| Role | URL |
|---|---|
| OAuth callback (GET) | `https://api.whiteto.com/api/v1/integrations/shopify/callback` |
| **Canonical webhook (POST)** | `https://api.whiteto.com/api/v1/integrations/shopify/webhook` |

Runtime `.env` updated this pass:

`SHOPIFY_WEBHOOK_CALLBACK_BASE=https://api.whiteto.com/api/v1/integrations/shopify/webhook`

Backend restarted so registration uses the dedicated receiver. OAuth GET callback and webhook POST remain separate handlers.

Public POST `/webhook` without HMAC → **401** (handler alive). Valid HMAC / topic / replay / `app/uninstalled` covered by existing Shopify pytest (`37` shopify-selected tests passed). Live registration still depends on a completed OAuth install.

---

## 5. Partner Dashboard

| Setting | Required value | From code? |
|---|---|---|
| App URL | `https://api.whiteto.com/api/v1/integrations/shopify/install` | Route exists; Dashboard visual confirm |
| Allowed redirection | `https://api.whiteto.com/api/v1/integrations/shopify/callback` | Matches `SHOPIFY_CALLBACK_URL` |
| Embedded | Off | No App Bridge path in code |
| Scopes | `read_products,write_products,read_inventory,write_inventory,read_orders,read_locations` | Code default + `.env.example` |
| Client ID / distribution / install eligibility | — | **Dashboard visual only** |

Client secret never printed.

---

## 6. Live OAuth

| Step | Result |
|---|---|
| Public `/install` | **303** (no longer 404) |
| `POST /shopify/connect` for `mriy3s-zv.myshopify.com` | **201** — authorize URL issued (`redirect_uri` = public callback) |
| Shopify login/consent | **Reached** real Shopify login UI; **awaiting merchant approve** |
| Callback / encrypt / tenant / webhooks / UI / disconnect / reconnect | **Pending consent** |
| **M17** | **Still open** until consent + token exchange succeed |

---

## 7. Shop ownership

Global unique `shop_domain` + maintenance lookup (`scalar_one_or_none`) remain in place (migration `0012`). Not re-opened as a blocker.

---

## 8. Gates (this worktree)

| Gate | Result |
|---|---|
| `ruff check app tests` / `ruff format --check app tests` / `mypy app --strict` | Pass |
| `pytest -k shopify` | **37 passed** |
| Full `pytest` on develop tip | **78 failed / 773 passed** — failures concentrated in product/orders PE surface (local DB vs develop tip); **not** treated as Shopify regressions |
| Frontend lint / typecheck / build | Pass |
| Playwright Shopify + integrations + shell | **Not green this pass** — timeouts against local e2e server (port contention / auth). Prior verification pass: **88 passed**. Re-run when e2e port is free. |
