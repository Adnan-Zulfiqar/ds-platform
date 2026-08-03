# Phase 8.1 — live deploy / tunnel verification addendum

| | |
|---|---|
| Date | 2026-08-03 |
| Branch | `develop` |
| Prior tag | `phase-8-1-complete` → `315d65f` (**not moved**) |
| Follow-up from | [PHASE_8_1_VERIFICATION.md](PHASE_8_1_VERIFICATION.md) |

---

## 1. Git

- Tip at start of this pass: `a20f740`
- Unrelated product-editor WIP was **stashed** (`stash@{0}` / `stash@{1}`) so deploy used Phase 8.1 tip only
- `main` untouched

---

## 2. Local routes (after restarting uvicorn on develop tip)

Stale process was the original local 404 on `/install` — OpenAPI lacked Phase 8.1 routes until restart.

| Method | Path | Status |
|---|---|---|
| GET | `/api/v1/integrations/shopify/install` | **303** (missing HMAC → frontend redirect) |
| GET | `/api/v1/integrations/shopify/callback` | **303** |
| POST | `/api/v1/integrations/shopify/claim-install` | **401** (auth required) |
| POST | `/api/v1/integrations/shopify/webhook` | **401** (HMAC required) |
| POST | `/api/v1/integrations/shopify/webhooks/orders-create` | **401** (HMAC required) |
| GET | `/health/live` | **200** |

OpenAPI includes `shopify/install`, `claim-install`, and singular `/webhook`.

---

## 3. Cloudflare routing

| Finding | Evidence |
|---|---|
| Agent | Windows service `cloudflared` (auto-start), `tunnel run --token-file C:\ProgramData\cloudflared\token` |
| Local backend | `uvicorn` on `0.0.0.0:8000` (restarted with Phase 8.1 tip) |
| Path-scoped ingress | Only **some** paths reach FastAPI |

### Public path map (FastAPI headers = `x-request-id` / rate-limit)

| Path | Public result | Hits FastAPI? |
|---|---|---|
| `/api/v1/integrations/shopify/callback` | GET **303**, POST **401** | **Yes** |
| `/api/v1/integrations/aliexpress/callback` | GET **303** | **Yes** |
| `/api/v1/integrations/aliexpress/webhook` | GET **405** / POST **200** | **Yes** |
| `/api/v1/integrations/shopify/install` | **404** bare CF | **No** |
| `/api/v1/integrations/shopify/webhook` | **404** bare CF | **No** |
| `/api/v1/integrations/shopify/webhooks/*` | **404** bare CF | **No** |
| `/api/v1/integrations/shopify/connect` | **404** bare CF | **No** |
| `/health/live` | **404** bare CF | **No** |

**Conclusion:** `api.whiteto.com` is **not** a full reverse proxy to localhost:8000. It is a **path allowlist** on a remotely managed Cloudflare Tunnel. Restarting the local backend alone cannot fix public `/install` 404 — Zero Trust Public Hostname (or equivalent ingress) must add paths.

### Required Cloudflare Zero Trust changes (human)

In Cloudflare Zero Trust → Networks → Tunnels → (this tunnel) → Public Hostname for `api.whiteto.com`, either:

**Preferred:** one hostname rule service `http://localhost:8000` with path `*` / empty (full API), **or**

**Minimum Shopify paths** (mirror AliExpress webhook already forwarded):

| Path | Service |
|---|---|
| `/api/v1/integrations/shopify/install` | `http://localhost:8000` |
| `/api/v1/integrations/shopify/webhook` | `http://localhost:8000` |
| `/api/v1/integrations/shopify/webhooks/*` | `http://localhost:8000` |
| Keep existing | `/api/v1/integrations/shopify/callback` |

Do **not** change unrelated DNS records for `whiteto.com` apex/frontend.

This repository cannot edit remotely managed tunnel ingress (token-only agent; no local `config.yml`).

---

## 4. Webhook configuration alignment

| Item | Value |
|---|---|
| Preferred separate endpoint in code | `…/shopify/webhooks/{topic}` or shared `…/shopify/webhook` |
| Publicly reachable webhook-capable route today | **POST** `…/shopify/callback` (explicit shared webhook handler + HMAC) |
| Local `.env` update this pass | `SHOPIFY_WEBHOOK_CALLBACK_BASE=https://api.whiteto.com/api/v1/integrations/shopify/callback` |

Rationale: user rule allows callback only when it is **explicitly** designed for webhook POST + HMAC — it is (`shopify_webhook_via_callback`). Separate `/webhook(s)` remain preferred once Cloudflare forwards them; then move the base.

---

## 5. Partner Dashboard (exact values)

| Setting | Value |
|---|---|
| **App URL** | `https://api.whiteto.com/api/v1/integrations/shopify/install` |
| **Allowed redirection URL** | `https://api.whiteto.com/api/v1/integrations/shopify/callback` |
| **Embedded app** | **Disabled** (standalone OAuth) |
| **Scopes** | `read_products,write_products,read_inventory,write_inventory,read_orders,read_locations` |
| **Distribution** | Custom distribution install link **or** Public listing — **cannot verify which is selected from the repo** |
| **Custom install link** | Required if distribution is Custom — generate for `mriy3s-zv.myshopify.com` |

Until Cloudflare forwards `/install`, App URL installs will 404 even with a correct Dashboard value.

---

## 6. Live OAuth round trip (typed Connect)

Typed Connect does **not** need public `/install` (frontend uses `NEXT_PUBLIC_API_URL=http://localhost:8000`). Callback must be public (it is).

| Step | Result |
|---|---|
| `POST /shopify/connect` for `mriy3s-zv.myshopify.com` | **201** — authorize URL opened in default browser |
| Merchant consent | **Awaiting human** — Shopify login/approve in the opened window |
| Callback / encrypt / DB / webhooks / UI / disconnect | **Pending consent** |

**Action for Addi:** complete Shopify login + approve in the browser window that opened. Then say so in chat so post-consent checks can run (status, encryption presence, webhook registration timestamp, disconnect/reconnect).

M17 stays **open** until that completes.

---

## 7. Gates

Re-run after this pass (or cite prior green if unchanged): see follow-up commit message / completion update.
