# Phase 8.1 — final verification report

| | |
|---|---|
| Date | 2026-08-03 |
| Branch | `develop` (**not** `main`) |
| Prior tag | `phase-8-1-complete` → `315d65f` (**not moved**) |
| This follow-up tip | see `git rev-parse develop` after push |

**Status:** Implementation-complete. **Not** live-OAuth-complete (M17 remains open). Frontend build and Playwright Shopify/integrations/shell gates are now verified on this machine.

**Live deploy addendum:** [PHASE_8_1_LIVE_DEPLOY.md](PHASE_8_1_LIVE_DEPLOY.md) — local Phase 8.1 routes are live after uvicorn restart; public `/install` still 404 due to path-scoped Cloudflare Tunnel (not a missing code deploy).

---

## 1. Partner Dashboard configuration checklist

Required values (from DropPilot code + local `.env` / production probes):

| Setting | Required value | Status |
|---|---|---|
| **App URL** | `https://api.whiteto.com/api/v1/integrations/shopify/install` | **Blocked by tunnel** — local backend serves route (303); public GET still **404** (Cloudflare path allowlist). See LIVE_DEPLOY. |
| **Allowed redirection URL(s)** | `https://api.whiteto.com/api/v1/integrations/shopify/callback` | **Cannot verify from repository** (Dashboard only). Matches `SHOPIFY_CALLBACK_URL` in local `.env`. Public GET callback → **303** (route alive). |
| **Client ID** | Same app as `SHOPIFY_API_KEY` tip `a7dd…a53a` | **Correct** in local `.env` (len 32). Dashboard match **cannot verify from repo**. |
| **Client Secret** | Same app as `SHOPIFY_API_SECRET` (`shpss_…`) | Present locally; **never printed**. Dashboard match **cannot verify from repo**. |
| **Scopes** | `read_products,write_products,read_inventory,write_inventory,read_orders,read_locations` | **Correct** in `.env` / code defaults. Dashboard enablement **cannot verify from repo**. |
| **Distribution** | Custom install link and/or Public App Store | **Cannot verify from repository** |
| **Installation method** | App URL (public) + DropPilot Connect (typed `*.myshopify.com`) | Code supports both; public path blocked by production **404** on `/install` |
| **Embedded app** | Off (standalone OAuth; no App Bridge) | **Correct** in code — no embedded session-token path |

Do **not** change application code to paper over Dashboard misconfiguration.

---

## 2. Live OAuth 403 diagnosis

### Trace (safe fields only)

| Step | Result |
|---|---|
| Production `GET …/shopify/install` | **404** — App URL entry not deployed |
| Local typed Connect (`POST …/connect`) | Historically **201**; authorize URL built with Client ID tip `a7dd…a53a`, scopes above, redirect = callback URL |
| Authorize URL (no session, no follow) | **303** → shop `/admin/auth/login?redirect=…/oauth/authorize…` (URL shape accepted) |
| Authorize URL (curl follow, no merchant cookies) | Final **403** on `admin.shopify.com/store/mriy3s-zv/oauth/authorize` |
| Direct `admin.shopify.com/…/oauth/authorize` | **403** without session |
| Callback / code exchange / encrypted token / DB | **Not reached** in this session (no consent) |

### Root cause (evidence-based)

1. **Unauthenticated probes of `admin.shopify.com` OAuth always 403** without a Shopify merchant session. That alone does **not** prove Partner misconfiguration.
2. **Prior browser “Unauthorized Access”** (see `SHOPIFY_CONNECTION_DEBUG_REPORT.md`) still points to Partner/install eligibility or Allowed redirection mismatch — **cannot confirm Dashboard screens from this repo**.
3. **Public App URL cannot work until `/install` is deployed** (current production **404**).
4. **Not caused by** DropPilot route logic for typed Connect start, HMAC helpers, or Client ID absence in `.env`.

**Merchant action required for a real round trip:** open DropPilot Integrations while signed in as admin → Connect Shopify → enter `mriy3s-zv.myshopify.com` → complete Shopify consent in the browser. After production deploy, also set Partner **App URL** to the install route above.

---

## 3. Live OAuth round trip

| Step | Status |
|---|---|
| Install route opens (production) | **Fail** — 404 |
| Consent screen / approve | **Not verified** — needs human browser |
| Callback, state consume, code exchange, encrypt, tenant bind | **Not verified** |
| Webhooks registered | **Not verified** live |
| UI connected / disconnect / reconnect | **Not verified** live |

**M17 remains open.**

---

## 4. Webhook routing

| Item | Finding |
|---|---|
| `SHOPIFY_CALLBACK_URL` | OAuth only (GET). Also accepts POST as **shared** webhook receiver when base points at callback. |
| Local `SHOPIFY_WEBHOOK_CALLBACK_BASE` | `…/shopify/webhook` (singular) |
| Code | Accepts base ending `/webhooks`, `/callback`, or `/webhook`; registers topics accordingly |
| Production probes | POST `/callback` → **401** (HMAC expected — handler alive). GET/POST `/webhook`, `/webhooks`, `/webhooks/orders-create` → **404** (not exposed / not deployed) |
| Recommendation | Until tunnel exposes `/webhooks/*` or `/webhook`, set webhook base to the **callback** URL so registrations hit a live POST handler. Do not invent a combined OAuth+webhook redesign beyond the existing shared-callback path. |
| Unit/integration | Idempotent registration helpers + uninstall claim release covered in pytest |

---

## 5. Next.js build root cause (fixed for this machine)

**Cause:** multiple stuck `next build` processes **and** `npm run start:e2e` holding a lock on `.next/standalone`, so subsequent builds froze after the Next.js banner.

**Fix applied:** stop project e2e/build Node processes; delete `.next`; rebuild.

**Result:** `npm run build` **exit 0** — compiled successfully; 21 static pages; routes listed including `/settings/integrations`.

---

## 6. Quality gates (this session)

| Gate | Result |
|---|---|
| `ruff check` / `ruff format --check` | Pass |
| `mypy app` | Pass (158 files) |
| `pytest` | **767 passed** |
| `npm run lint` / `typecheck` | Pass |
| `npm run build` | **Pass (exit 0)** |
| Playwright Shopify OAuth (chromium + mobile-chrome, workers=1) | **26 passed**, 0 failed, 0 skipped |
| Playwright integrations + shell (chromium + mobile-chrome, workers=1) | **62 passed**, 0 failed, 0 skipped |
| Full Playwright suite (all specs) | **Not run** this session (time/rate-limit risk) |
| Live Shopify consent / token / DB | **Not verified** |

### Skip categories

- No intentional `test.skip` in the Shopify OAuth file for this green run.
- Full-suite not executed: would amplify auth rate limits (M13 / A-06); targeted Shopify + integrations + shell covers the Phase 8.1 surface.

---

## 7. Tag policy

- Existing `phase-8-1-complete` was created **before** frontend build, Playwright re-run, and live OAuth were fully verified.
- That tag was **not moved** and must not be force-updated.
- Repo convention uses `phase-N-complete` only — **no** `phase-*-verified` tag created.

---

## 8. Honest readiness

| Area | Score | Notes |
|---|---|---|
| Implementation (develop) | 8/10 | App URL + claim + hardening present |
| Automated gates | 9/10 | Build + pytest + targeted Playwright green |
| Production deploy of Phase 8.1 routes | 2/10 | `/install` and webhook paths 404 publicly |
| Live Partner OAuth | 2/10 | M17 — needs human consent + Dashboard confirm |
| **Overall** | **~6.5/10** | Implementation-complete; not live-verification-complete |
