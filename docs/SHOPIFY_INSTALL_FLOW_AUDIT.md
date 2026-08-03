# Shopify install flow audit — AutoDS-like UX vs protocol reality

| | |
|---|---|
| Date | 2026-08-03 |
| Branch | `develop` |
| Status | **Audit only — no install-flow code change** |
| Official sources | [Authorization code grant](https://shopify.dev/docs/apps/build/authentication-authorization/access-tokens/authorization-code-grant), [App Store requirements §2.3.1](https://shopify.dev/docs/apps/launch/shopify-app-store/app-store-requirements) |

## 1. Verdict (read this first)

**You cannot remove the shop-domain step and still “redirect straight to Shopify OAuth” with the current DropPilot architecture and a typical custom/Partner app.**

Shopify’s authorization URL is shop-scoped:

```text
https://{shop}.myshopify.com/admin/oauth/authorize?client_id=…&scope=…&redirect_uri=…&state=…
```

Without `{shop}`, that URL does not exist. There is **no** supported public authorize host that lets DropPilot start OAuth without knowing the shop (Shopify staff have stated that `admin.shopify.com/admin/oauth/authorize` is not for public installs).

AutoDS / DSers / Printful **do not** invent an OAuth URL without a shop. They get the shop from Shopify:

1. Merchant installs from the **Shopify App Store** (or a Shopify install link).
2. Shopify hits the app’s **App URL** with signed `shop`, `hmac`, `timestamp`.
3. The app then redirects to `{shop}/admin/oauth/authorize` (or uses Shopify managed install / token exchange for embedded apps).

That is a **different product architecture** (public / App Store app + App URL entrypoint + account-linking), not a UI tweak on today’s “type your `.myshopify.com`” custom-app flow.

**Therefore:** no production code change in this pass that removes the domain field. Faking “Connect Shopify” without a shop would either break OAuth or invent unsupported behaviour.

**Follow-up plan:** [SHOPIFY_PRODUCTION_INSTALL_PLAN.md](SHOPIFY_PRODUCTION_INSTALL_PLAN.md)
(Partner checklist, webhook base mismatch, migration steps).

---

## 2. Audit of the current implementation

### 2.1 What DropPilot implements today

| Aspect | Current state |
|---|---|
| App model assumed | **Partner / custom app** (config docstring, `.env.example`) |
| Merchant credentials | **None** — only store domain; `SHOPIFY_API_KEY` / `SHOPIFY_API_SECRET` are platform env |
| Connect | Authenticated `POST /api/v1/integrations/shopify/connect` with `{ shop }` |
| Authorize URL | Built in `build_authorization_url` → `https://{shop_domain}/admin/oauth/authorize?…` |
| Tenant binding | Redis `state` written at connect time (`tenant_id`, `user_id`, `shop_domain`) |
| Callback | `GET …/shopify/callback` — HMAC → consume state → shop match → token exchange → encrypt token → store row |
| Webhooks | Registered after successful OAuth (best-effort) |
| App Store install | **Not implemented** |
| App URL install handshake | **Not implemented** (no handler for Shopify’s `?shop=&hmac=&timestamp=` install GET) |
| Embedded / App Bridge / session tokens | **Not implemented** |
| Live Partner OAuth | **Unverified** (M17) |

Key files: `backend/app/integrations/shopify/auth.py`, `service.py`, `api/v1/integrations/router.py`, `frontend/components/integrations/shopify-card.tsx`.

### 2.2 Why the UI asks for a shop domain

Not a DropPilot invention — protocol + current entrypoint:

1. **Authorize and token endpoints are on the shop host** (official authorization-code-grant docs).
2. DropPilot’s **only** install entrypoint is “merchant already logged into DropPilot → click Connect → we must build the authorize URL **now**.”
3. At that moment Shopify has not told us which shop is installing, so the merchant must supply `*.myshopify.com` (normalised in `normalise_shop_domain`).
4. Redis state also stores `shop_domain` so the callback can reject shop mismatch (CSRF + binding integrity).

### 2.3 Does the current Shopify App configuration support X?

We **cannot** read the Partner Dashboard from this repo. From **code and docs only**:

| Capability | Supported by current code? | Notes |
|---|---|---|
| Custom / Partner app OAuth with typed shop | **Yes (designed for this)** | Domain field is required |
| Direct install link that already includes shop | **Partially, outside DropPilot UI** | Shopify can generate install links that land with `shop`; we have no App URL handler to receive them |
| Shopify App Store installation | **No** | No App URL install verification → authorize redirect; listing/review not in scope |
| Embedded app | **No** | No App Bridge, no token exchange, no iframe escape |
| Merchant-supplied API keys | **Correctly rejected** | Never asked |

Production callback target you named (`https://api.whiteto.com/api/v1/integrations/shopify/callback`) is compatible with the **existing** callback route **if** that URL is allow-listed as the app’s Allowed redirection URL and `SHOPIFY_CALLBACK_URL` matches it exactly. That does **not** remove the need for `{shop}` when starting authorize from DropPilot.

---

## 3. Official Shopify constraints (not optional)

### 3.1 Authorization code grant requires `{shop}`

From [Implement authorization code grant manually](https://shopify.dev/docs/apps/build/authentication-authorization/access-tokens/authorization-code-grant):

- Install via App Store or installation link → Shopify sends `GET` to your **App URL** with `shop`, `timestamp`, `hmac`.
- You verify HMAC, then redirect to:

  `https://{shop}.myshopify.com/admin/oauth/authorize?client_id=…&scope=…&redirect_uri=…&state=…`

- Token exchange: `POST https://{shop}.myshopify.com/admin/oauth/access_token`.

**Implication:** “Redirect to OAuth without knowing the shop” is only possible if **Shopify already told you the shop** (App URL / install link / embedded session), not if DropPilot invents the authorize URL from a blank Connect click.

### 3.2 App Store requirement 2.3.1

From [App Store requirements](https://shopify.dev/docs/apps/launch/shopify-app-store/app-store-requirements) §2.3.1:

> Apps must be installed and initiated only on Shopify services. Your app must not request the manual entry of a myshopify.com URL or a shop's domain during the installation or configuration flow.

So for a **public App Store** listing, today’s DropPilot domain field would be **rejected** on review — but the fix is **not** “build authorize without shop”; it is “start install on Shopify, receive shop from Shopify.”

### 3.3 How AutoDS-like products satisfy both

| Step | Who knows the shop? |
|---|---|
| Merchant clicks Install on App Store / Shopify | Shopify |
| Browser hits App URL `?shop=…&hmac=…` | App learns shop |
| App redirects to `{shop}/admin/oauth/authorize` | App already has shop |
| Merchant consents | Shopify |
| Callback with `code` + `shop` | App exchanges token |
| In-app onboarding: “Link to DropPilot account” | App binds shop → tenant **after** install |

External SaaS (DropPilot) typically: **App Store install first**, then **login/link** inside the embedded or standalone app — not “type shop into DropPilot then OAuth.”

---

## 4. Recommended production architecture

### 4.1 Target (AutoDS-class)

```text
DropPilot UI "Connect Shopify"
        │
        ▼
Redirect to Shopify App Store listing / install surface
        │
        ▼
Shopify → GET App URL ?shop&hmac&timestamp
        │
        ▼
Verify HMAC → issue state (pending DropPilot tenant link) → authorize redirect
        │
        ▼
Callback (https://api.whiteto.com/api/v1/integrations/shopify/callback)
        │
        ▼
Exchange token → encrypt → shopify_connections
        │
        ▼
If DropPilot session present: bind to current tenant
Else: prompt login / “claim this shop” with signed one-time link
        │
        ▼
Register webhooks
```

Preserve: platform secrets only, Fernet tokens, global unique `shop_domain`, tenant-scoped repos, webhook HMAC.

### 4.2 Required Shopify-side work (checklist)

1. Confirm app type in Partner / Dev Dashboard: **public** app intended for App Store (or limited visibility), not only a custom app installable on one store.
2. Set **App URL** to a DropPilot endpoint that handles install handshake (new route — not today’s authenticated `POST /connect`).
3. Set **Allowed redirection URL(s)** to exactly  
   `https://api.whiteto.com/api/v1/integrations/shopify/callback`  
   (and staging equivalents).
4. Align `SHOPIFY_CALLBACK_URL` / `SHOPIFY_API_KEY` / `SHOPIFY_API_SECRET` / scopes with the Dashboard.
5. Submit App Store listing; pass review including **§2.3.1** (no manual shop URL).
6. Optionally: embedded admin + [Shopify managed installation](https://shopify.dev/docs/apps/build/authentication-authorization/app-installation) / [token exchange](https://shopify.dev/docs/apps/build/authentication-authorization/access-tokens/token-exchange) for in-admin UX (larger migration).

### 4.3 DropPilot code work (only after public-app / App URL exists)

| Work | Purpose |
|---|---|
| `GET` App URL handler | Verify install HMAC; read `shop`; start OAuth without merchant typing domain |
| Pending-install / claim flow | Bind shop to DropPilot tenant when install starts outside a logged-in session |
| Change Connect button | Link to App Store / install surface when public; keep domain field **only** for explicit custom-app / staging mode if ever needed |
| Tests | HMAC install request, claim binding, tenant isolation, no fake authorize without shop |
| Docs | Replace “type domain” as the production path |

**Do not** implement “Connect with no shop → hardcode or guess authorize host.”

### 4.4 What stays valid today (custom / pre–App Store)

Until the public app + App URL path ships:

- Keep domain collection for **custom / development-store** installs (honest OAuth).
- Keep never asking for API keys/secrets.
- Keep encryption, uniqueness, webhooks.
- Document that AutoDS UX is blocked on **distribution model**, not on a missing `window.location` trick.

---

## 5. Implementation changes in this pass

**None** for removing the domain field.

Rationale: not technically valid against Shopify’s authorization-code-grant URL shape and DropPilot’s current entrypoint. Implementing a fake flow would violate “Do not fake the OAuth flow.”

---

## 6. Tests

No new production tests in this pass (no code change).

When the App URL install path is implemented, minimum coverage:

- Install GET with valid/invalid HMAC.
- State issued without merchant-typed shop; callback shop matches.
- Claim/bind to authenticated tenant; foreign tenant cannot claim.
- Connect UI does not collect domain in App Store mode.
- Existing domain-based connect remains behind a documented custom/dev flag if retained.

Current e2e (`frontend/tests/e2e/shopify-oauth.spec.ts`) correctly stubs consent and asserts shop-scoped authorize URLs — consistent with protocol reality.

---

## 7. Root cause summary

| Question | Answer |
|---|---|
| Why ask for store URL? | Authorize/token URLs require `{shop}`; DropPilot starts OAuth from its own UI without a prior Shopify install request. |
| Can we remove it with current config? | **No**, not without unsupported hacks. |
| What do AutoDS-like apps do? | Install starts on Shopify; `shop` arrives signed on App URL; then authorize. |
| What must we build? | Public/App Store (or install-link) distribution + App URL handshake + tenant claim/link — then remove the domain field from the **production** Connect UX. |

---

## 8. Related debt

- **M17** — live Partner OAuth still unverified; this audit does not close it.
- This document is the architectural gate for any future “remove shop domain” UI work.
