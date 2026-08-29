# Processing register

Every purpose the software actually serves, with the data it touches and the
evidence for each claim. Lawful bases are **proposed** — an engineer can
establish what the code does, not which basis a controller relies on.

Two rules were applied throughout, because both are common ways a register
becomes fiction:

* **"Legal obligation" is not used for a contractual requirement.** eBay
  requires subscribers to handle marketplace account deletion notifications;
  that is a contract with eBay, not a statutory duty on DESIRLY LIMITED. It is
  marked as legitimate interests with the contractual driver named, and flagged
  for legal confirmation.
* **Consent is not claimed at all.** An earlier draft called the marketplace
  OAuth authorisation "consent". Review corrected that, rightly: clicking
  *Authorise* at eBay is a **technical permission grant to that provider**, not
  UK GDPR Article 6(1)(a) consent to DESIRLY LIMITED. Real consent has
  requirements this product does not implement — a freely given choice that can
  be refused without losing the service, a withdrawal that stops the processing
  it authorised, and a record of what was consented to and when. Connecting a
  sales channel is *necessary to provide the service the merchant asked for*, so
  the basis is **contract**, subject to final legal review.

---

## Controller and processor roles

| Situation | Role | Basis for the classification |
|---|---|---|
| Platform accounts, authentication, sessions, security | **Controller** | DESIRLY LIMITED decides the purposes and means; the merchant has no say in how sessions or password hashing work |
| Workspace and subscription administration | **Controller** | Same |
| Merchant's catalogue, drafts, pricing rules, store connections | **Controller** for the account relationship; the content is the merchant's business data | The merchant supplies it and directs its use |
| **Buyer / order data synced from a merchant's sales channel** | **Processor**, acting on the merchant's instructions | The buyer's relationship is with the merchant. DESIRLY LIMITED stores and displays it so the merchant can fulfil orders, and does nothing else with it. **Requires an Article 28 DPA — see blocker 3.** |
| eBay marketplace account deletion notifications | **Controller** for the compliance record | The obligation attaches to DESIRLY LIMITED as the eBay application subscriber, and the record exists to evidence its own compliance |

**Not settled by the software.** The processor classification for buyer data is
supported by how the code behaves — order fields are stored, displayed and
deleted with the workspace, never aggregated, profiled or reused. It is *not*
supported by a contract, because no DPA is offered yet. Until one exists, the
classification is a description of behaviour rather than an agreed legal
position.

---

## Purposes

Each row: what is processed, where it comes from, why, the role, the proposed
basis, who receives it, how long it lasts, what erasure does, and the evidence.

### 1. Account registration and authentication

* **Data** — email address, first and last name, Argon2id password hash, active
  and verified flags, last sign-in time.
* **Source** — the person registering.
* **Purpose** — create and secure an account.
* **Role** — controller. **Proposed basis** — performance of a contract.
* **Recipients** — none outside the production server.
* **Retention** — for the life of the account. Sessions expire independently.
* **Erasure** — `platform-user` scope: identifying fields replaced, password
  hash cleared, account deactivated, and **all seventeen columns referencing the
  user** either deleted or cleared. The row survives so orders and audit
  references stay intact. Colleagues and workspace data are untouched.
* **Evidence** — `app/models/user.py`, `app/core/password.py`,
  `app/services/data_subject_erasure.py`.

### 2. Session management

* **Data** — refresh tokens stored as SHA-256 hashes with expiry and revocation
  state; access tokens held only in the browser's memory.
* **Source** — generated at sign-in.
* **Purpose** — keep a person signed in without re-entering a password.
* **Role** — controller. **Proposed basis** — performance of a contract.
* **Retention** — access token 15 minutes; refresh token 30 days, revoked on
  sign-out and rotated on every use with reuse detection.
* **Erasure** — rows deleted outright.
* **Evidence** — `SecuritySettings.access_token_ttl_minutes`,
  `refresh_token_ttl_days`, `app/models/refresh_token.py`,
  `frontend/lib/auth/token-store.ts`.

### 3. Email verification

* **Data** — hashed verification token, expiry, the user it belongs to.
* **Retention** — 24 hours (`security.email_verification_ttl_hours`).
* **Erasure** — deleted.
* **Evidence** — `app/models/email_verification.py`, `app/core/config.py:456`.
* **Note** — there is **no password-reset flow server-side**. The
  `/forgot-password` page exists in the frontend; no API route or reset-token
  store backs it, so no reset tokens are held.

### 4. Workspace and tenant administration

* **Data** — workspace name, slug, status, timezone, default currency, role
  assignments.
* **Role** — controller. **Proposed basis** — performance of a contract.
* **Retention** — life of the workspace.
* **Erasure** — role grants are **physically deleted**, never soft-deleted: a
  soft-deleted grant still grants.
* **Evidence** — `app/models/tenant.py`, `app/models/role.py`.

### 5. Marketplace OAuth connections (eBay, Shopify, AliExpress)

* **Data** — provider account identifier, display name, marketplace/shop
  identifiers, granted scopes, connection status, and **encrypted** access and
  refresh tokens.
* **Source** — the provider, after the merchant authorises.
* **Purpose** — act on the merchant's behalf against their sales channel.
* **Role** — controller for the connection record.
* **Proposed basis** — **performance of a contract**. Connecting the sales
  channel is what the merchant signed up for; without it the service does
  nothing. The OAuth authorisation is a technical permission granted to the
  marketplace, not GDPR consent to us — see the note at the top of this
  document.
* **Recipients** — the marketplace concerned, and only when connected.
* **Retention** — until disconnected.
* **Erasure** — two distinct paths, deliberately:
  * **Disconnect** (merchant-initiated, in the UI) — hard delete of the row
    including the ciphertext, for all three providers. Verified:
    `EbayConnectionService.disconnect` → `connections.hard_delete`;
    `AliExpressService.disconnect` → `connections.hard_delete`;
    `ShopifyService.release_shop` → `session.delete(connection)` plus
    best-effort remote revocation.
  * **Workspace closure** — deletes all three, eBay included.
  * **Platform-user erasure does _not_ delete these.** The connection belongs to
    the workspace, not to whoever clicked Connect; only the `user_id` reference
    is cleared.
* **Evidence** — `app/integrations/{ebay,shopify,aliexpress}/`,
  `app/models/{ebay,shopify,integration}.py`, `app/core/encryption.py`.

### 6. Product, draft and store operations

* **Data** — supplier catalogue data, pricing, shipping, SEO and AI fields,
  plus `requested_by_user_id` / `created_by_user_id` / `connected_by_user_id`.
* **Role** — controller for the account link; the catalogue itself is the
  merchant's business data.
* **Proposed basis** — performance of a contract.
* **Retention** — life of the workspace.
* **Erasure** — user references cleared; catalogue retained as the workspace's
  business record.
* **Evidence** — `app/models/product.py`, `app/models/store.py`.

### 7. Order and buyer information

* **Data** — `buyer_name`, `recipient_name`, `recipient_phone`, city, province,
  postal code, country code, line items, amounts, shipments.
* **Source** — the merchant's connected sales channel. **The buyer never
  interacts with DropPilot AI.**
* **Purpose** — let the merchant see and fulfil their own orders.
* **Role** — **processor**, on the merchant's instructions.
* **Proposed basis** — the merchant's basis, not ours. DESIRLY LIMITED needs an
  Article 28 agreement rather than a basis of its own.
* **Retention** — life of the workspace; no separate schedule exists.
* **Erasure** — **not implemented, and deliberately not approximated.** `orders`
  carries no buyer identifier — no email, no external buyer id — so a buyer can
  only be matched on a low-entropy name. An earlier version cleared the buyer
  fields on *every* order in the tenant to compensate, destroying uninvolved
  customers' records; that is now forbidden and tested against. A shopper's
  request is referred to the merchant. Exact buyer erasure is a launch blocker
  requiring a stable identifier on `orders` first.
* **Evidence** — `app/models/order.py` lines 277–287,
  `data_subject_erasure.py`.
* **Populated only when order sync runs.**

### 8. Security, rate limiting and abuse prevention

* **Data** — client IP address, request metadata, login attempt counters,
  lockout state.
* **Role** — controller. **Proposed basis** — legitimate interests (keeping the
  service available and accounts unbreached). **A balancing test still needs to
  be recorded.**
* **Retention** — rate-limit counters (`ratelimit:ip:{client_ip}`) expire on a
  60-second window; login throttling keeps two counters,
  `login:email:{sha256(address)[:32]}` and `login:ip:{sha256(client_ip)[:32]}`,
  on a 300-second window extended to a 900-second lockout; cache entries
  (`t:{tenant}:*`) default to 300 seconds. All in Redis, all expiring
  automatically. **The address and the IP are hashed before use as keys**, so the
  key space holds neither even in `MONITOR` output.
* **Erasure** — the email counter is deleted during platform-user erasure. The
  IP-derived counters are not: an address is not a person, it is shared behind
  NAT and reassigned by ISPs, and they expire within 900 seconds regardless.
* **Evidence** — `security.rate_limit_window_seconds`,
  `login_attempt_window_seconds`, `login_lockout_seconds`,
  `redis.default_ttl_seconds`, `app/core/client_ip.py`.

### 9. Application and HTTP logs

* **Data** — method, path, status, duration, **client IP address**, user agent,
  request id. No request bodies, no credentials, no tokens — the logger redacts
  on a broad substring allowlist.
* **Role** — controller. **Proposed basis** — legitimate interests.
* **Retention** — **the application writes to standard output and opens no
  file.** The current production deployment does not redirect that stream to
  disk, so no application log is persisted. Where a deployment does persist it,
  `LOG_RETENTION_DAYS` caps rotated files at 30 days.
* **Evidence** — `app/core/logging.py` (`StreamHandler(sys.stdout)`),
  `app/middleware/request_context.py`, `app/core/log_retention.py`. See
  [LOG_RETENTION.md](LOG_RETENTION.md).

### 10. eBay marketplace account deletion compliance

* **Data** — eBay's notification reference, topic, schema version, event and
  publish timestamps, a non-personal identity digest, outcome counters.
* **Purpose** — evidence that a deletion notification was received and acted on.
* **Role** — controller for this record.
* **Proposed basis** — **legitimate interests**, driven by the eBay Developers
  Program agreement. Deliberately *not* "legal obligation": the requirement
  comes from a contract with eBay, and whether a statutory duty also applies is
  a question for legal review, not for this document.
* **Retention** — **indefinite, by design.** The record is the proof of
  compliance; deleting it would defeat its purpose. It contains no username, no
  user identifier and no payload.
* **Evidence** — `app/models/ebay.py`, `app/integrations/ebay/compliance.py`,
  `app/integrations/ebay/schemas.py` (the digest covers only topic, schema
  version, notification id and event date).

### 10a. Federated sign-in (Google)

* **Data** — Google's immutable subject identifier, the verified email address,
  and the display name. `provider_email` is kept for display only; nothing
  matches on it.
* **Source** — a signed ID token from Google, verified server-side against
  Google's published keys.
* **Purpose** — let a person sign in without a password.
* **Role** — controller. **Proposed basis** — performance of a contract.
* **Recipients** — Google, which is an independent controller for its own
  account data.
* **Retention** — until the identity is unlinked or the account is erased.
* **Erasure** — `user_identities` rows are **deleted** during platform-user
  erasure. Leaving one behind would let the Google account sign back into an
  erased user, so it is declared in `USER_REFERENCES` and the completeness guard
  fails if a future table is not.
* **Never stored** — no Google ID token, access token or refresh token. Scopes
  are limited to `openid email profile`; no Gmail, Drive or contacts access is
  requested.
* **Evidence** — `app/models/identity.py`, `app/integrations/google/`,
  `app/services/google_auth.py`, migration `0031`.

### 10b. Password reset by one-time code

* **Data** — the address the code is sent to; a keyed HMAC of the six-digit
  code; a keyed HMAC of the reset ticket; an attempt counter. Hashed forms of
  the address and client IP as rate-limit key names.
* **Purpose** — let somebody who has lost access regain it.
* **Role** — controller. **Proposed basis** — performance of a contract, with a
  legitimate interest in the surrounding abuse controls.
* **Recipients** — Resend, which delivers the message.
* **Retention** — the challenge expires after **10 minutes**, the ticket after
  **10 minutes**, the resend cooldown after **60 seconds**, and the hourly
  request counters after **1 hour**. All in Redis, all expiring automatically.
  Nothing is written to the database except the resulting password hash.
* **Never stored** — the code itself, in any form that could be reversed. It is
  kept as a domain-separated HMAC under a dedicated secret
  (`SECURITY_OTP_HMAC_KEY`), because a bare SHA-256 of six digits is a table
  lookup.
* **Decision recorded: a Google-only user may set a password this way.** Losing
  access to a Google account should not mean losing the workspace, and the code
  goes to the address Google itself verified. Refusing would also be an
  account-existence oracle.
* **Evidence** — `app/services/password_reset.py`,
  `app/integrations/email/`, `app/api/v1/auth/router.py`.

### 11. Support and privacy requests

* **Data** — whatever the person includes in their email.
* **Purpose** — answer them; carry out rights requests.
* **Role** — controller. **Proposed basis** — legal obligation for statutory
  rights requests (this one genuinely is), legitimate interests otherwise.
* **Retention** — in the `privacy@whiteto.com` mailbox. **No retention schedule
  exists for the mailbox** — an open operator decision.
* **Evidence** — [RETENTION_AND_ERASURE.md](RETENTION_AND_ERASURE.md) runbook.

### 12. Optional AI and currency features

* **AI** — defaults to `STUB`, which generates locally and **sends nothing
  anywhere**. If an operator configures an external provider, the text submitted
  for optimisation is sent to it. Listing text is not normally personal data,
  but nothing stops a merchant typing something personal into it.
* **FX** — defaults to `unavailable`. If enabled, **only currency codes** are
  exchanged; no personal data.
* **Object storage** — S3 settings exist (`S3_` prefix, `S3_REGION` default
  `eu-west-1`) but **no application code uses them**.
* **Status** — all three are **disabled** in production today.
* **Evidence** — `app/core/config.py` (`AISettings`, `FxSettings`,
  `StorageSettings`), `app/ai/`.

### 13. Cookies and browser storage

* **Cookies** — exactly one: `droppilot_refresh`. `HttpOnly`, `SameSite=Lax`,
  `Secure` in deployed environments, scoped to the authentication path, max-age
  matching the 30-day refresh lifetime. **Strictly necessary**; no consent
  banner is required for it and none is shown.
* **Browser storage** — one functional key, the last selected ship-to country.
  It stays in the browser and is never sent as personal data. The access token
  lives in a JavaScript variable only.
* **Analytics, advertising, tracking, third-party error reporting** — **none
  implemented.** Verified by repository search: no SDK in `package.json`; the
  only "Sentry" reference is a comment in `frontend/app/error.tsx` marking where
  one would go.
* **Evidence** — `app/api/v1/auth/router.py:63`, `app/core/config.py:406–409`,
  `frontend/lib/countries.ts`, `frontend/lib/auth/token-store.ts`.

---

## Still requiring approval

1. The definitive purpose-to-basis mapping above.
2. A recorded legitimate-interests balancing test for rows 8, 9 and 10.
3. The Article 28 DPA for row 7.
4. Whether row 10's driver is contractual only, or also statutory.
5. A retention schedule for the privacy mailbox (row 11).
