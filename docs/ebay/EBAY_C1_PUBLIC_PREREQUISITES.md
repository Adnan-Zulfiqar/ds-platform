# EBAY-C1.1 — public prerequisites and the privacy audit behind them

The eBay portal will not accept a RuName without a reachable privacy policy, and
the EBAY-C1 production preflight found there was none: no `/privacy` route
anywhere in the release, and `https://whiteto.com/privacy` returning 404 from an
unrelated application on the root domain.

This milestone adds the policy, makes it discoverable, and corrects the portal
handoff contract. It deploys nothing.

---

## The audit the policy is built on

Written from the implementation, not from a template. Each row was checked
against the named source; the policy says only what these rows support.

| Category | What is actually stored or done | Evidence |
|---|---|---|
| Account | Email, first and last name, Argon2id password hash, active/verified flags, last sign-in time | `app/models/user.py` |
| Workspace | Name, slug, status, active flag, timezone, default currency | `app/models/tenant.py` |
| Session security | Refresh tokens stored **as hashes** with expiry and revocation; email-verification tokens likewise; role assignments | `app/models/refresh_token.py`, `email_verification.py`, `role.py` |
| Shopify | Shop domain, shop id, encrypted access token, granted scopes, sync status | `app/models/shopify.py` |
| AliExpress | App key, encrypted app secret, encrypted access and refresh tokens, expiry, status | `app/models/integration.py` |
| **eBay (C1)** | Immutable `ebay_user_id`, display `ebay_username`, marketplace, account type, **encrypted** access and refresh tokens, expiries, granted scopes, connection status | `app/models/ebay.py` |
| eBay compliance ledger | Notification id, topic, schema version, event/publish timestamps, identity digest, statuses, receipt and erasure counts — **no personal data by design** | `app/models/ebay.py` |
| Products / drafts | Supplier catalogue data, pricing, shipping, SEO and AI fields; `requested_by_user_id`, `created_by_user_id` | `app/models/product.py` |
| Stores | Name, platform, storefront URL, encrypted credentials, sync flags, `connected_by_user_id` | `app/models/store.py` |
| **Orders** | `buyer_name`, `recipient_name`, `recipient_phone`, city, province, postal code, country, items, amounts, shipments — **buyer personal data**, populated only when order sync runs | `app/models/order.py` |
| Notifications | User id, kind, title, body, payload, read state | `app/models/notification.py` |
| Cookies | Exactly one: `droppilot_refresh`, `httponly=True`, `secure` (default true), `samesite=lax`, scoped to the auth path | `app/api/v1/auth/router.py`, `app/core/config.py` |
| Browser storage | Exactly one functional key — the last selected ship-to country. Access token is held **in memory only** | `frontend/lib/countries.ts`, `frontend/lib/auth/token-store.ts` |
| Analytics / trackers | **None implemented.** `frontend/app/error.tsx` carries a comment that an error reporter would go there; none is wired | repository-wide search |
| Logs | Method, path, status, duration, client IP, user agent, request id. No bodies, no credentials | `app/middleware/request_context.py` |
| AI provider | Defaults to `stub` — generates locally, sends nothing. OpenAI/Anthropic used only if an operator configures a key | `app/core/config.py`, `app/ai/` |
| FX provider | Defaults to `unavailable`. Open Exchange Rates only if enabled; exchanges currency codes, no personal data | `app/core/config.py` |
| Object storage | S3 settings exist (default region `eu-west-1`) but **no code uses them** | repository-wide search |
| Outbound email | **Not implemented** | repository-wide search |
| Deletion | `soft_delete` (sets `deleted_at`) and `hard_delete` on the base repository. eBay disconnect **hard-deletes** the row and its ciphertext. Marketplace deletion notifications hard-delete the matching connection across every tenant, idempotently | `app/repositories/base.py`, `app/integrations/ebay/connection.py`, `app/integrations/ebay/deletion.py` |
| Self-service account deletion | **No endpoint exists.** Erasure is by request | `app/api/v1/` search |

---

## Unresolved decisions — an operator must settle these

The policy uses accurate, qualified language wherever the code does not settle a
question. It does not invent an answer. These remain open:

1. **Hosting and transfer location.** Not encoded in the application. The policy
   says the location is a deployment decision and offers to disclose it on
   request. Needs a factual answer before publication.
2. **Concrete retention periods.** Only deletion *behaviour* is implemented; no
   per-category retention schedule exists. The policy states the criteria and
   commits to deletion on request.
3. **Lawful basis per activity.** The policy names the categories likely to
   apply and says explicitly that the definitive mapping cannot be determined
   from the software. A controller must decide this.
4. **Controller identity and registered address.** The policy identifies
   "DropPilot AI" and the contact address only. A registered company name,
   address and any ICO registration number should be added.
5. **No self-service deletion control.** Erasure is handled on request. If the
   product later gains a delete-my-account control, this section changes.
6. **Buyer data responsibility.** Where order sync is used, the operator is
   processing their customers' data through this service. The controller/processor
   relationship needs to be settled in the terms, not only the privacy policy.
7. **Subprocessor list.** The policy describes provider *categories* supported by
   evidence. A published list with names and locations is still needed.

> **Operator approval is required before this policy is published in
> production.** It is an accurate technical first draft written from the
> implementation; it is not legal advice and has not had professional legal
> review. That review should happen before the eBay portal is pointed at it.

This note is deliberately here and not on the public page: a policy that opens by
telling readers it might be wrong is worse than no policy, and the caveat belongs
to the operator publishing it, not to the person reading it.

---

## The page

`frontend/app/privacy/page.tsx` — a Server Component with no hooks, no fetching
and no client state, so it renders statically.

`/privacy` is added to `PUBLIC_ROUTES` in `frontend/middleware.ts`. eBay fetches
the URL with no session; a policy behind the auth gate would fail RuName
validation.

Linked from the shared auth layout — so sign-in, register and forgot-password all
carry it — and from the integrations page, which is where a merchant decides to
hand marketplace data over.

### It renders outside the authentication provider

The first version of this milestone put `/privacy` outside the *auth gate* but
left it inside `AuthProvider`, which lives in the root layout and issues
`POST /auth/refresh` from a mount effect. The public policy page therefore
performed an authentication bootstrap on every visit. A test exempted that call;
the exemption was the wrong answer, and a reviewer rejected it.

`AuthProvider` now mounts in `app/(app)/layout.tsx`, a group wrapping both
`(auth)` and `(protected)`. Route groups do not appear in URLs, so every path is
unchanged. `/privacy`, `/unauthorized`, `/`, and the root error and not-found
boundaries sit outside that group and mount no session provider at all.

**Why a shared parent rather than the provider in each group.** Mounting it in
`(auth)` and again in `(protected)` looked like the smaller change and passed the
privacy tests, but it broke sign-in. Sibling route groups do not share a layout
instance, so crossing from the sign-in form to the dashboard remounted the
provider and fired a second session restore. `router.replace` then navigated away
while that request was in flight. Refresh tokens rotate on use with reuse
detection, so the server had already issued a replacement whose `Set-Cookie` the
aborted response never delivered — the browser kept a spent token and the next
request returned 401, dropping the user back on the login page. It reproduced
four times out of four. A common ancestor keeps one provider instance across that
navigation, which is what the root layout used to provide.

The acceptance contract for the page is now: no `/auth/*` request, no mutating
API request, no DropPilot cookie, no dependency on the API being reachable, and
complete server-rendered content with JavaScript disabled. Each of those is a
test in `frontend/tests/e2e/privacy.spec.ts`, and
`frontend/tests/e2e/auth-provider-boundary.spec.ts` holds the other half of the
boundary — that sign-in, registration, protected routes, session restore and
logout all still work.

### Canonical metadata is deliberately absent

The page sets `title` and `robots`. It sets **no canonical URL**, and none was
added. A canonical has to name the public origin, and that origin
(`https://app.whiteto.com`) does not resolve yet — it is one of the outstanding
deployment blockers below. Emitting a canonical pointing at a host that does not
exist is worse than emitting none. The repository has no `metadataBase` or
site-URL convention to derive it from either, so adding one would mean inventing
configuration. It should be added in the same change that makes the public host
real.

### It is the only indexable route in the application

The root layout sets `robots: { index: false, follow: false }`, with a comment
explaining that the application is behind authentication and has nothing public
worth indexing. That was accurate until this milestone; `/privacy` is now the
exception, and it overrides the default in its own `metadata`.

The reasoning is not SEO. eBay fetches the URL directly and a `noindex` would
not have stopped it. But the entire purpose of this route is to be readable by
someone who has no account and was not sent a link — a policy that instructs
search engines to ignore it is not meaningfully *published*. The root default
stays as it is, because it remains correct for every other route.

A regression test asserts the page's `robots` meta does not contain `noindex`,
so a future change to the root layout cannot silently pull this page back behind
the default.

---

## Portal handoff — corrected

| Field | Value |
|---|---|
| Display title | `DropPilot AI` |
| RuName (production) | `Auto_Pilot` |
| Auth accepted | `https://api.whiteto.com/api/v1/integrations/ebay/callback` |
| Auth declined | `https://api.whiteto.com/api/v1/integrations/ebay/callback` |
| Privacy policy | `https://app.whiteto.com/privacy` |
| Frontend return | `https://app.whiteto.com/settings/integrations` |

Required production configuration (values live in the deployment, never in Git):

```
NEXT_PUBLIC_API_URL=https://api.whiteto.com        # inlined at BUILD time
EBAY_FRONTEND_RETURN_URL=https://app.whiteto.com/settings/integrations
CORS_ORIGINS=<must include https://app.whiteto.com>
EBAY_REDIRECT_URI_NAME=Auto_Pilot
```

---

## Still outstanding for a C1 production release

This milestone does not resolve these; they were identified during the C1
preflight and remain blockers for the deployment itself:

1. **`app.whiteto.com` does not resolve.** The privacy URL, the frontend return
   URL and the merchant-facing application all depend on that host existing.
   Requires a DNS/tunnel change with established Cloudflare ownership.
2. **Production `NEXT_PUBLIC_API_URL` is a loopback address**, so a build made
   from it would be broken for every external visitor.
3. **Production `CORS_ORIGINS` omits the public frontend origin.**
4. **Production has no frontend build, dependencies, environment file or startup
   authority** — see the deployment section of
   [`EBAY_C1_SELLER_CONNECTION.md`](EBAY_C1_SELLER_CONNECTION.md).
