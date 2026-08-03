# Shopify production install plan — AutoDS-style migration

| | |
|---|---|
| Date | 2026-08-03 |
| Status | **Architecture decision document — no OAuth code changes** |
| Depends on | [SHOPIFY_INSTALL_FLOW_AUDIT.md](SHOPIFY_INSTALL_FLOW_AUDIT.md) |
| Official refs | [Authorization code grant](https://shopify.dev/docs/apps/build/authentication-authorization/access-tokens/authorization-code-grant), [App Store §2.3.1](https://shopify.dev/docs/apps/launch/shopify-app-store/app-store-requirements), [Select distribution](https://shopify.dev/docs/apps/launch/distribution/select-distribution-method) |

---

## 1. Current Shopify app mode (what we can prove)

**We cannot open the Partner Dashboard from this repository.** The following is inferred from code, `.env`, production probes, and prior live debugging — not from Dashboard screenshots.

| Label | Applies? | Evidence |
|---|---|---|
| **Partner app** (Dev Dashboard / Partners Client ID + secret) | **Yes (working assumption)** | `SHOPIFY_API_KEY` (32-char Client ID `a7dd…a53a`) + `SHOPIFY_API_SECRET` (`shpss_…`) drive OAuth authorize + token exchange |
| **Custom distribution** (single-merchant / Plus org install link) | **Unknown** | Requires Dashboard “Distribution” selection; code supports shop-typed OAuth either way |
| **Public / App Store app** | **Not supported by DropPilot code** | No App URL install handler for `?shop&hmac&timestamp`; Connect UI requires typed domain |
| **Embedded app** | **No** | No App Bridge, no session tokens, no token exchange |
| **Admin “Develop apps” store custom app** | **Not this OAuth path** | Those apps issue Admin API tokens in-store without DropPilot’s authorize URL |

**Practical mode today:** Partner (or equivalent) app used as a **standalone, non-embedded, shop-domain-first OAuth install** from DropPilot’s Integrations UI.

**Live OAuth status (re-checked 2026-08-03):**

| Step | Result |
|---|---|
| Credentials present / status `configured` | Yes |
| `POST /connect` → authorize URL | 201 |
| Authorize for `mriy3s-zv.myshopify.com` (unauthenticated probe) | **403** |
| Completed `shopify_connections` | **0** (M17 still open) |

Browser consent is authoritative; probe 403 matches the earlier “Unauthorized Access” failure mode until Dashboard install eligibility + Allowed redirection URL are confirmed.

---

## 2. Why AutoDS flow is different

| | DropPilot today | AutoDS-style |
|---|---|---|
| Who starts install | Merchant inside DropPilot | Merchant on **Shopify** (App Store or install link) |
| How shop is known | Merchant types `*.myshopify.com` | Shopify sends `shop` (+ `hmac`, `timestamp`, often `host`) to **App URL** |
| Authorize URL | DropPilot builds it immediately | App builds it **after** verifying the install GET |
| App Store §2.3.1 | Domain field would fail public review | Install initiated on Shopify — no typed shop URL |
| Tenant bind | Redis `state` written while DropPilot session is active | Need **claim / link** if install starts without DropPilot login |

AutoDS does **not** skip `{shop}` in OAuth. It receives `shop` from Shopify first.

**Therefore:** “Connect Shopify” cannot become a blind redirect to a generic Shopify login until an App URL (or install-link → App URL) path exists and Partner distribution supports it.

---

## 3. Configuration audit (env + production)

### 3.1 Environment (local `.env`, secrets masked)

| Variable | Value / status |
|---|---|
| `SHOPIFY_API_KEY` | set (`a7dd…a53a`) |
| `SHOPIFY_API_SECRET` | set (`shpss_…`) |
| `SHOPIFY_SCOPES` | `read_products,write_products,read_inventory,write_inventory,read_orders,read_locations` |
| `SHOPIFY_API_VERSION` | `2025-01` |
| `SHOPIFY_CALLBACK_URL` | `https://api.whiteto.com/api/v1/integrations/shopify/callback` |
| `SHOPIFY_FRONTEND_RETURN_URL` | `http://localhost:3000/settings/integrations` |
| `SHOPIFY_WEBHOOK_CALLBACK_BASE` | `https://api.whiteto.com/api/v1/integrations/shopify/webhook` ← **singular** |

### 3.2 Partner Dashboard checklist (must be verified by a human)

| Setting | Required value | How to verify |
|---|---|---|
| **App URL** | A DropPilot URL that will handle install GET (future). Today code has **no** dedicated App URL route. Using bare `https://api.whiteto.com` is insufficient (root returns 404). | Dashboard → App setup |
| **Allowed redirection URL(s)** | **Exactly** `https://api.whiteto.com/api/v1/integrations/shopify/callback` | Must match `SHOPIFY_CALLBACK_URL` character-for-character |
| **Distribution** | Custom install link **or** Public (App Store) | Dashboard → Choose distribution ([docs](https://shopify.dev/docs/apps/launch/distribution/select-distribution-method)); **immutable once chosen** |
| **Client ID / secret** | Same app as `.env` | Compare Client ID to `a7dd…a53a` |
| **Scopes** | At least the list in §3.3 | Enabled in Dashboard and requested in authorize URL |

### 3.3 Scopes

**Configured / requested today:**

| Scope | Purpose |
|---|---|
| `read_products` / `write_products` | Catalogue publish / update |
| `read_inventory` / `write_inventory` | Inventory push |
| `read_orders` | Order import + order webhooks consumption |
| `read_locations` | Inventory location resolution |

**Webhooks:** Registration uses Admin API `POST /webhooks.json` with the offline token after OAuth. Topics registered in code: `products/create`, `products/update`, `inventory_levels/update`, `orders/create`, `orders/updated`, `app/uninstalled`. No separate “webhook scope” beyond access to those resources.

**Gaps to confirm in Dashboard:** scopes above enabled; any protected customer data requirements if scopes expand later.

### 3.4 Webhook base mismatch (confirmed — do not “fix” until decision)

Code behaviour (`webhook_delivery_address`):

- If base **ends with** `/webhooks` → register `…/webhooks/{topic-with-hyphens}`.
- **Otherwise** → register **the same base URL for every topic** (topic from `X-Shopify-Topic`).

Router reality:

| Path | Method | Exists? |
|---|---|---|
| `/api/v1/integrations/shopify/webhooks/{topic}` | POST | Yes in code |
| `/api/v1/integrations/shopify/callback` | GET OAuth / POST webhook | Yes in code |
| `/api/v1/integrations/shopify/webhook` (singular) | — | **No route** |

Production probes (2026-08-03):

| Request | Result |
|---|---|
| `GET …/callback` | **303** (alive) |
| `POST …/callback` | **401** (HMAC expected — handler alive) |
| `GET/POST …/webhook` | **404** |
| `GET/POST …/webhooks…` | **404** (likely tunnel/nginx only exposes callback) |

**Current `.env` base ends with `/webhook` (singular)** → registration would point Shopify at a **404** URL. That is a configuration bug relative to both code and production routing.

**Safe production options (pick one later):**

1. **Tunnel / path-scoped host:** set `SHOPIFY_WEBHOOK_CALLBACK_BASE` to the **callback** URL (`…/shopify/callback`) so POSTs hit the shared webhook-via-callback handler.
2. **Full API host:** set base to `…/shopify/webhooks` **and** ensure nginx/tunnel forwards `/api/v1/integrations/shopify/webhooks/*`.

Do **not** change code until that ops choice is confirmed.

---

## 4. Target AutoDS-style architecture

```text
[DropPilot] Connect Shopify
        │
        ▼
Redirect merchant to Shopify-owned install
  (App Store listing OR custom install link)
        │
        ▼
Shopify → GET {App URL}?shop&hmac&timestamp[&host]
        │
        ▼
DropPilot: verify HMAC → create state
  (tenant claim token / pending install)
        │
        ▼
302 → https://{shop}/admin/oauth/authorize?client_id&scope&redirect_uri&state
        │
        ▼
Shopify consent → GET callback?code&shop&state&hmac
  https://api.whiteto.com/api/v1/integrations/shopify/callback
        │
        ▼
Exchange code → encrypt token → shopify_connections
  bind or claim tenant → register webhooks
```

Preserve: platform secrets only, Fernet tokens, global unique `shop_domain`, tenant-scoped repos, webhook HMAC + replay rules.

---

## 5. Exact Partner Dashboard changes needed

Before any “remove shop URL” UI work:

1. Confirm **distribution** (Custom vs Public). Prefer **Public** (or limited-visibility listing) for multi-tenant AutoDS UX; Custom only if one merchant / Plus org.
2. Set **Allowed redirection URL** exactly to  
   `https://api.whiteto.com/api/v1/integrations/shopify/callback`.
3. Set **App URL** to a **new** DropPilot install endpoint (to be built), e.g.  
   `https://api.whiteto.com/api/v1/integrations/shopify/install`  
   — **not** bare `https://api.whiteto.com` (404 today).
4. Align Client ID/secret with `.env`.
5. Enable scopes listed in §3.3.
6. For custom distribution: generate install link for `mriy3s-zv.myshopify.com` (or target shop) and test in a logged-in merchant browser until consent succeeds.
7. For public: create listing; satisfy §2.3.1 (no typed shop domain on install).

---

## 6. Code changes required **after** configuration is confirmed

### Backend (new work — not started)

| Change | Why |
|---|---|
| `GET …/shopify/install` (App URL) | Verify install HMAC; read `shop`; issue Redis state; redirect to authorize |
| Pending-install / claim API | Bind shop → DropPilot tenant when OAuth starts without a DropPilot session |
| Optional: keep `POST /connect` behind `SHOPIFY_ALLOW_TYPED_SHOP=true` | Staging / custom-only fallback |
| Webhook base ops fix | Env only once path strategy chosen (§3.4) |

### Frontend (new work — not started)

| Change | Why |
|---|---|
| Connect button → App Store / install URL when public mode | AutoDS entry |
| Remove domain field **only** when App URL install is live | §2.3.1 + protocol |
| Claim / “Link this Shopify store” screen | Tenant binding |

### Explicitly out of scope until then

- Removing the domain input.
- Redirecting Connect to a generic Shopify login without `shop`.
- Inventing `admin.shopify.com/oauth/authorize` as a public start URL.

---

## 7. Can “Connect Shopify” become a direct redirect?

| Condition | Answer |
|---|---|
| Today (typed-shop OAuth only) | **No** — would be unsupported / fake |
| After App URL handler + Partner App URL + working install link or App Store | **Yes** — button opens Shopify-owned install; shop arrives on App URL |
| Custom distribution only | Button can open the **generated install link** (shop already baked by Shopify); still no typed domain in DropPilot |

---

## 8. Migration steps (ordered)

1. **Human:** Complete Partner checklist (§5); screenshot App URL, redirect URLs, distribution.
2. **Ops:** Fix `SHOPIFY_WEBHOOK_CALLBACK_BASE` to callback **or** ensure `/webhooks/*` is publicly reachable (§3.4).
3. **Verify:** Browser OAuth for one shop → `shopify=connected`, row in DB, webhooks registered (closes M17 for that path).
4. **Build:** App URL install + claim flow + tests.
5. **Switch UI:** Connect → Shopify install surface; hide domain field in production mode.
6. **Submit / maintain** App Store listing if public.

---

## 9. Security considerations

| Topic | Requirement |
|---|---|
| Install GET | Verify `hmac` before trusting `shop` |
| State | One-time Redis; bind tenant securely (session cookie or signed claim token) |
| Shop uniqueness | Keep global unique `shop_domain`; reject cross-tenant takeover |
| Tokens | Fernet encrypt; never log code/token/secret |
| Callback | Existing HMAC + state + shop match remain |
| Webhooks | HMAC + replay; fail-closed for mutating topics |
| App URL | HTTPS only; no open redirects |

---

## 10. Remaining blockers

1. **Partner Dashboard facts unknown** — distribution, App URL, exact Allowed redirection URLs not readable from repo.
2. **Live authorize still failing** for the known store (403 / Unauthorized) — OAuth never completes (M17).
3. **No App URL handler in DropPilot** — AutoDS entry impossible in code today.
4. **Webhook base misconfigured** (`…/webhook`) vs routes/production (**404**).
5. **Production may only expose callback path** — topic webhooks 404 until tunnel/nginx updated.
6. **Frontend return URL still localhost** in `.env` — fine for local UI; production frontend must be set for real merchants.

---

## 11. Decision gate

**Do not remove the shop URL input and do not change OAuth start code until:**

1. Dashboard settings are confirmed in writing (or screenshots), and  
2. Architecture choice is explicit: **Custom install link** vs **Public App Store**, and  
3. App URL endpoint design is approved.

This document is the plan; implementation waits on that confirmation.
