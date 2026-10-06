# Changelog

All notable changes to DropPilot AI.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project uses phase tags rather than semantic versions until the first
production release.

---

## [Unreleased]

### Added - Track F4: supplier-order panel and Fulfilment settings

- **Order page.** Shopify, eBay and WooCommerce orders show the AliExpress
  order behind them: the state, review reasons in plain sentences, **Place
  on AliExpress**, a **Pay on AliExpress** link once placed, the tracking
  number, and **Send tracking to the store** for a single parcel. The panel
  refreshes on its own while the order is being sent. Viewers see the state
  without actions.
- **Settings -> Fulfilment.** The *Auto-order* and *Auto-tracking* switches
  (off by default) and the optional fallback shipping method.

### Added - Track F3: automatic ordering and tracking sync

- **Automatic mode.** With *Auto-order* on, every paid Shopify, eBay or
  WooCommerce order that arrives (import or webhook) is reviewed and, if it
  maps cleanly, placed on AliExpress. An order that already has a supplier
  order (placed, failed, in review) is never touched automatically.
- **Tracking sync.** Every three hours DropPilot asks AliExpress
  (`aliexpress.ds.order.tracking.get`) for each placed order's tracking
  number and stores it. With *Auto-tracking* on it is sent to the store
  through the same "mark shipped" code as the manual forms, and the buyer
  gets the store's shipping email. A store refusal is recorded and the
  number kept.
- **One parcel only.** An order split across several AliExpress sellers is
  never given one tracking number automatically.
- **Manual push.** `POST /orders/{id}/supplier-order/push-tracking`.
- **Not verified live:** no real tracking number has been read from
  AliExpress yet.

### Fixed - Track F1: Shopify order lines, and the AliExpress refresh scope (PR #87)

- **Shopify orders were stored with no lines**, so nothing could be ordered
  from the supplier. Each import now keeps the lines, linked to the exact
  catalogue variant through the listing's variant map (migration `0049`
  adds `order_items.variant_id`).
- **The 6-hourly AliExpress refresh sent Shopify, eBay and WooCommerce
  order ids to AliExpress.** It now looks only at AliExpress orders.
- This entry was missing from #87 itself and is added here.

### Added - Track F2: place the AliExpress order behind a channel order

- **"Place on AliExpress".** `POST /orders/{id}/supplier-order` queues the
  order; a background task places it with `aliexpress.ds.order.create`.
  The order AliExpress creates is unpaid; the merchant pays it there.
- **Review before placing.** Only paid Shopify, eBay and WooCommerce orders
  with a complete address, outside Brazil and Chile, whose every line maps
  to exactly one AliExpress SKU. Anything else waits in `needs_review` with
  reasons.
- **No double orders.** The task never retries; only `queued` rows are
  placed; a row left `placing` after a crash waits for the merchant.
- **Switches.** `GET/PUT /orders/fulfilment/settings`: *Auto-order*,
  *Auto-tracking* and a fallback shipping method, all off by default (used
  from F3).
- **Migration.** `0050` adds `supplier_orders` and `fulfilment_settings`.
- **Not verified live:** no real order has been placed (D-017).

### Tests - inventory sync and the mailer

- `InventorySyncService` had no direct test: a stock move is recorded once
  and queued for the channels, a no-op refresh records nothing, one failing
  product does not fail the run, an outright failure is marked and
  reported, and two syncs cannot overlap.
- `LoggingMailer` never logs an address or a body; only the domain, the
  subject and the body length.

### Added - Shopify's mandatory privacy webhooks

- **`customers/redact`, `shop/redact` and `customers/data_request`** are
  now handled on the existing HMAC-verified Shopify receiver. Shopify
  requires all three for app approval; until now they were acknowledged by
  the generic handler and did nothing.
- **Redaction is exact.** Shopify names the order ids; DropPilot blanks the
  buyer's name, phone and address on precisely those orders, in the one
  workspace and store the shop belongs to. `shop/redact` blanks every
  order of that store and still works after uninstall has removed the
  connection. The country code stays.
- **A data request is referred.** The merchant is the controller, so the
  workspace gets an in-app notification carrying the Shopify customer and
  order ids (never the email or phone) and replies through Shopify.
- **Owner step:** the compliance webhook URL is set in the Partner
  Dashboard, not subscribed by the app. See `docs/PHASE_8_1_LIVE_DEPLOY.md`.

### Changed - the draft editor no longer calls the API directly

- The editor's conflict reads and its publish call went through
  `apiClient` inside the component, against the rule that `services/`
  owns every endpoint. They now use `fetchDraft` and a new `publishDraft`
  in `services/drafts.ts`. The conflict read still bypasses the query
  cache on purpose; that reason is now on the service function. No
  behaviour change.

### Fixed - the billing page could show the trial after a paid checkout

- On return from Stripe Checkout the page reads the status and syncs the
  subscription at the same time. If the plain read landed second it
  overwrote the paid state with the stale trial one. The sync now cancels
  the in-flight read before writing its result. Found as a flaky e2e in
  CI (`billing.spec.ts`), but it was a real race.

### Fixed - scheduled tasks that never reached the channels

- **`shipment.refresh` failed on every run** with `TypeError: multiple
  values for argument 'limit'`: it passed its own `self` to a bound task.
  It now calls the order refresh correctly, with the same limit.
- **Shopify never received automatic price or stock pushes.** The Shopify
  push tasks existed since Phase 8 but nothing enqueued them. A new
  `shopify.push_price_quantity` task joins the channel fan-out, so every
  trigger that reached eBay and WooCommerce now reaches Shopify too.
- **The scheduled inventory sweep pushed only to eBay.** It now uses the
  shared fan-out (eBay, WooCommerce, Shopify).
- **The scheduled pricing recalculation pushed to nothing.** The manual
  endpoint already pushed; the 12-hourly run now does the same.
- **The supplier refresh pushed to nothing.** It now pushes a product whose
  price or stock moved, and only then: an unchanged product costs no call.

### Added - history on the Pricing, Inventory and Automation pages

- **Pricing** lists the last 20 price changes the rules applied (the page
  always said "every change is audited" and never showed the audit).
- **Inventory** lists the last 10 sync runs with their outcome, and the
  last 20 stock movements.
- **Automation** lists the last 20 runs across every rule, by rule name.
- All three API lists existed since Phase 6 without a screen. Read-only,
  no backend change.

### Added - edit, pause and delete for automation and pricing rules

- **Rules were create-only in the UI** although the API has had `PATCH`
  and `DELETE` since Phase 6. Each row on Automation and Pricing now has
  Edit (in place), Pause / Resume and Delete.
- **Delete is two clicks**, no native dialog: a misclick disarms itself
  after a few seconds.
- Editable fields follow what the API accepts for an update: name and
  schedule for an automation rule; name, markup percent and the two price
  guards for a pricing rule. An emptied guard is sent as `null` to clear it.

### Fixed - the Profile link went to a 404

- **Settings -> Profile now exists.** The user menu linked to
  `/settings/profile`, which had no page. The new page shows the signed-in
  user's name, email and verification state, workspace and role, and offers
  two actions the API already had without a UI: resend the verification
  email, and sign out of every device. Password changes link to the
  reset-by-code flow; the API has no edit-name or change-password endpoint,
  so neither is offered.
- **Settings index.** The Profile card is live instead of "Coming soon".

### Added — Track E6c: Settings → Billing

- **Billing page.** Plan cards with the AI add-on, Stripe Checkout,
  prorated plan switching and Stripe's portal (owner only), listing usage and
  the trial end. The page re-reads the subscription on return from Checkout.
- **Settings index.** The Billing card is now live.

### Fixed — supplier product images blocked by the CSP

- **Every imported product image was blocked.** That covered the gallery,
  variant photos, the editor thumbnail and the `<img>` tags in the supplier
  description. The cause was the Content-Security-Policy's `img-src`, which
  listed only Google avatars.
- **The fix allows named hosts.** `img-src` now lists the AliExpress CDNs
  (`*.alicdn.com`, `*.aliexpress-media.com`) and `cdn.shopify.com`. It does
  not allow any `https:` host, because supplier HTML is third-party content
  and could otherwise load tracking pixels.
- **Regression test:** `csp.spec.ts`.

### Added — Track E6b: plan limits and one trial per store

- **Listing limit at publish.** Counted per variant. Republishing is free.
- **AI add-on gate.** AI is refused on trial and on any plan without the
  add-on.
- **Read-only after the trial without a plan.** New imports are refused,
  while refresh, sync and orders keep running.
- **One free trial per store.** A store that already gave one workspace a
  trial ends the trial of any later workspace that connects it. Only a
  one-way hash of the store's identity is stored.
- **402 errors.** `listing_limit_reached`, `ai_addon_required` and
  `billing_inactive`. Nothing is enforced without Stripe configured.
- **Migration.** `0048` adds `trial_fingerprints`.

### Added — Track E6a: subscription billing core (Stripe)

- **Plans.** Starter $12 (200 listings), Growth $30 (450) and Pro $70
  (1000), each with an unlimited-AI add-on ($8, $12 and $17).
- **Trial.** 30 days free, during which Stripe starts charging only when the
  trial ends.
- **Endpoints.** Stripe Checkout and customer portal, plan and add-on
  changes, an explicit sync on return from Checkout, and a signed webhook
  that re-reads the subscription from Stripe (D-016).
- **Migration.** `0047` adds `tenant_subscriptions`.
- **Not yet enforced.** Limits arrive in E6b.

### Added — Track E5d: platform operator console

- `/platform`: operator sign-in (password + one-time code), workspace list
  with health counts, suspend/reactivate with a reason, and the audit trail.
  It uses its own API client: no tenant token, no refresh, and the token
  is kept in memory only.

### Added — Track E5c: workspace health counts

- Operators see a workspace's failed order and inventory syncs, failed
  notification emails (last 24 hours) and listings in error, as counts
  only.

### Added — Track E5b: workspace directory, suspend and reactivate

- Platform operators can list every workspace, with user and
  connected-store counts and search. They can suspend or reactivate a
  workspace with a required, audited reason; suspension takes effect within
  one access-token lifetime. Operators also get a recent-actions audit view.
  No tenant-owned data is exposed (D-015).

### Added — Track E5a: platform operator identity

- **Separate operator identity.** Operators have their own accounts
  (`platform_admins`, migration `0046`), signing in with a password plus a
  TOTP code. Platform tokens use their own audience, so tenant routes never
  accept them and platform routes never accept tenant tokens.
- **Off by default.** `/api/v1/platform/*` answers 404 unless
  `PLATFORM_ADMIN_ALLOWED_CIDRS` lists the caller's network.
- **Audit.** Every sign-in is audited, including refusals.
- **Accounts** are created only with `scripts/create_platform_admin.py`
  (D-015).
- **New guard test.** Any repository that escapes tenant scoping without
  being on CLAUDE.md §4's list now fails a test.

### Changed — AliExpress import without merchant OAuth; clearer callback failures

- **Paste-to-import no longer requires merchant OAuth.** Product fetch uses
  `ALIEXPRESS_CATALOG_ACCESS_TOKEN` (the platform dropshipper grant) via
  `AliExpressService.client_for_catalog()`, falling back to the merchant
  connection. Merchant connect is for orders and tracking. With neither, the
  import fails with `aliexpress_catalog_not_configured` (503), not
  "connect AliExpress". Home, import dialog and Integrations copy follow.
- **The OAuth callback says why it failed.** An unknown or expired state
  now redirects with `?aliexpress=expired` ("Authorization not
  recognised"). That is also what a callback routed to a different
  DropPilot server looks like, which was the cause on the whiteto setup;
  see `docs/operations/WHITETO_LOCAL_CLOUDFLARE.md`. Failures log a stable
  reason code. A pending connection shows its `last_error` and explains a
  callback that never arrived.

### Added — Track E7 W4b: live WooCommerce orders

- Connecting a WooCommerce store also registers `order.created` and
  `order.updated` webhooks when `WOOCOMMERCE_WEBHOOK_CALLBACK_BASE` is
  https. Each delivery is verified by the store's own HMAC secret, then the
  order is re-fetched from the store and upserted (D-014). Reconnect and
  disconnect remove old webhooks. A store that refuses webhooks still
  connects, and says why.

### Added — Track E7 W5: mark WooCommerce orders shipped

- A WooCommerce order's page offers "Mark shipped on WooCommerce". It sets
  the order to `completed` and adds an order note with the carrier, the
  tracking number and the link (shown to the customer if chosen), then
  records the shipment. Repeating a tracking number sends nothing. The form
  is shared with the Shopify one.

### Added — Track E7 W4a: import WooCommerce orders

- "Import recent orders" on each connected WooCommerce store imports the
  orders changed in the last 7 days (paged) into Orders, with recipient,
  address, totals and line items. Line items are linked to their draft by
  the W2 SKU. WooCommerce statuses are mapped to fulfilment and payment
  statuses, and checkout drafts are skipped. Order ids are stored as
  `<store id>:<order id>`, so two stores' order numbers never collide.
  Migration `0045` adds `'woocommerce'` to `order_source`. Live order
  webhooks are W4b.

### Added — Track E7 W3: WooCommerce price and stock follow the draft

- After a committed edit, pricing apply, or inventory or supplier sync, a
  published WooCommerce product receives the absolute price and stock. A
  refusal or wrong currency is recorded on the listing; an outage retries.
  The after-commit hook moved from the eBay task module to
  `tasks/integrations/channels.py` and now queues eBay and WooCommerce
  together. There is no scheduled WooCommerce sweep yet (needs approval,
  B-016).

### Added — Track E7 W2: publish to WooCommerce

- Review & publish now offers connected WooCommerce stores. A one-variant
  draft becomes a simple product with the merchant's price, managed stock,
  SKU `dp-<product id>`, and images on the first publish. A retry adopts the
  existing product (by recorded id, then by SKU) instead of duplicating it.
  The store's own refusal message reaches the merchant. Only stores
  connected through the verified path can publish.

### Added — Track E7 W1: connect WooCommerce stores

- **Settings → Integrations → WooCommerce.** Paste the site address and REST
  API keys. The keys are checked against the store before saving, which also
  records the store's verified currency. They are encrypted on the store row
  and never returned. Disconnect forgets them. Publishing, stock and orders
  follow in W2–W5.
- Calls to a merchant-supplied address go through the same SSRF contract as
  image fetches. The contract is now a shared `pin_https_target`: https
  only, globally routable addresses only, redirects refused.

### Fixed

- Workspace closure now clears credentials stored on store rows. Before
  Track E7 it deleted the three connection tables but left
  `stores.encrypted_credentials` in place.
- Publish readiness checks Shopify as a named case. A future channel added
  without rules of its own is refused, instead of being checked against
  Shopify's rules.

### Added — Track E4 team invitations

- Owners and admins invite colleagues as admin, member or viewer from
  **Settings → Team**. A one-time link valid for 7 days lets the recipient set
  a password, accept the terms and join, signed in. Re-inviting rotates the
  link, and revoking kills it. Migration `0044` adds `user_invitations`
  (additive). An address that already has a DropPilot account cannot accept
  until login is tenant-qualified (decision D-013). Verified with the
  recording email provider only.

### Added — Track E3 notifications by email

- Notifications (except `info`) are emailed from an outbox by a 2-minute
  beat task: user-targeted ones to that user, workspace ones to owners and
  admins. Each user picks kinds under **Settings → Notifications**; the
  default is failures only. Migration `0043` (additive). New setting
  `EMAIL_APP_BASE_URL` for links. Verified with the recording provider only.

### Added — Track E2 sale fees (M24C)

- Pricing rules take a **sale fee as a % of the selling price** (marketplace
  and payment fees). Prices are grossed up so markup, target margin and
  profit floors hold after the fee; reported profit and margin are net.
  Migration `0042` (nullable: existing rules price exactly as before).

### Added — Track E1 Shopify fulfilment push

- Mark a Shopify order shipped from its order page: carrier, tracking
  number, optional link, notify choice, sent with `fulfillmentCreate` to the
  order's open fulfillment orders; once per tracking number. New default
  scopes `read_/write_merchant_managed_fulfillment_orders` — **stores
  connected earlier must reconnect**. Verified with a faked Shopify client
  only.

### Added — scheduled eBay jobs (B-011, owner-approved)

- Hourly eBay order import (last two days) and a six-hourly price/stock
  backstop, each fanning out one task per connected workspace through the
  new ids-only `EbayConnectedTenantsSweep` (CLAUDE.md §4 list updated).

### Fixed — review of EBAY-C2 to C6 and the OpenAI provider

Three independent code reviews of the new eBay and AI code (2026-10-03);
every finding below was confirmed against the code before it was fixed.

- **Reconnecting as a different eBay seller** now erases the previous
  seller's listing defaults and eBay listing rows (as disconnect does), and
  readiness requires defaults from the current connection. Before, publishes
  could carry the old seller's policy ids, and the old seller's eBay deletion
  notice could no longer find their rows.
- **One inventory item per marketplace.** The eBay SKU is now
  `dp-<country>-<product id>`; one SKU on two marketplaces let a second
  publish rewrite the first live listing and collapsed C4's per-listing push.
- **The application token failing** (network, bad platform credentials, 429)
  is a "try again" readiness blocker, not "reconnect eBay" or a 500.
- **Repricing from the Pricing tab** (bulk and draft) now reaches eBay.
- **"Mark shipped" locks the order row**, so two concurrent requests cannot
  tell eBay twice.
- **Migration `0041`**: partial index for the eBay buyer eraser, which runs on
  every eBay account-deletion notice.
- Call-limit parsing accepts eBay's capitalised API names; listing-setup and
  eBay-details forms keep their "Saved" state; Review & publish copy follows
  the chosen store's own listing; labels no longer point at non-inputs.

### Added

- **eBay operations (EBAY-C6).** eBay 429s become `ebay_rate_limited`;
  an admin health view shows connection state and the remaining Sell API
  quota; the readiness CLI gains `EBAY_SELLER_CHANNEL`. Runbook and the
  owner's go-live checklist in `docs/ebay/EBAY_C6_OPERATIONS.md`.
- **eBay orders (EBAY-C5).** Import eBay orders into Orders (re-import
  updates), mark an order shipped on eBay with carrier and tracking (once per
  tracking number), and reflect eBay cancellations. Migration `0040`. eBay
  buyers' data on orders is declared and anonymised on eBay's deletion
  notice, matched by username (the only buyer key eBay order data has).
  **Verified against a mocked eBay transport only.**
- **eBay price and stock stay current (EBAY-C4).** After a product edit, a
  supplier refresh or an inventory sync commits, the product's price and
  quantity are sent to its eBay listings (`bulkUpdatePriceQuantity`, absolute
  values); the published product page can send them on demand. Refusals are
  recorded on the listing; eBay outages retry. **Verified against a mocked
  eBay transport only.**
- **Publish a draft to eBay (EBAY-C3).** Review & publish gains "eBay
  details" (eBay's category suggestions and the item specifics the category
  requires) and lists eBay marketplace stores beside Shopify ones. Publish
  puts the inventory item, creates or adopts the offer, and publishes it;
  a retry never makes a second listing. Readiness is now per channel
  (D-C3-1). Migrations `0038` (product category/aspects per marketplace) and
  `0039` (eBay store link on listing defaults; offer id and SKU on
  `store_listings`). eBay listing rows are declared and erased under eBay's
  deletion contract and deleted on disconnect. Single-variant products,
  condition NEW. **Verified against a mocked eBay transport only.**
- **OpenAI provider (`AI_PROVIDER=openai`).** The first real `AIProvider`:
  Chat Completions over the existing `httpx` dependency, transient-only
  retries with full-jitter backoff, `is_synthetic=False` results. Needs
  `AI_OPENAI_API_KEY` and `AI_OPENAI_MODEL`; a missing one is a named
  configuration error, never a stub fallback. Temperature is not sent
  (reasoning models reject it). An auth failure never echoes OpenAI's error
  text, which quotes part of the key. **Verified with a mocked transport
  only — no real OpenAI call has been made (B-002 stays open).**
- **eBay listing setup (EBAY-C2).** The eBay card gains a "Listing setup" panel: the
  seller's shipping, payment and return policies and Inventory API locations,
  read live per marketplace, and the defaults DropPilot will list with.
  Admins can save defaults (each id re-checked against eBay) and create a
  warehouse location. New table `ebay_listing_defaults` (migration `0037`),
  bound to the connection with `ON DELETE CASCADE`, **declared and erased**
  under eBay's account-deletion contract. No new OAuth scope. **Verified
  against a mocked eBay transport only — no real eBay account has been
  read or written.**

### Fixed

- **A blank `KEY=` line in `backend/.env` no longer erases the root
  `.env` value.** A run from source loaded `backend/.env` last, so the
  template's empty AliExpress/Shopify/eBay key lines silently left the app
  with no provider credentials; the Compose stack, which reads only the root
  file, was unaffected. Blank lines in env files now count as unset. An
  empty environment variable still overrides both files.

### Reviewed

- **Cursor independent review of `develop` @ `e63508e` (2026-10-02).**
  Implementation ACCEPTED WITH NON-BLOCKING NOTES; release NOT READY (live
  AI, Shopify and AliExpress unverified; two flakes cause-unproven; owner
  images still contain `/app/.env`). Report kept verbatim in
  `docs/reviews/cursor/`. `phase-9-complete` not tagged; Draft Editor
  Stage 8 not complete. Follow-up drill on `e63508e`: 42/42 digests
  identical, encrypted credential intact.

### Fixed

- **A write's 2xx no longer arrives before its commit (PR #35).** On
  FastAPI 0.141 the request-scoped session committed after the response was
  sent; a client acting on a 201 could hit rows not yet committed (the
  `global-rules-impact` flakes) and a failed commit was reported as success.
  `DbSession` now uses function scope.
- **A removed draft image stays removed (PR #38).** The draft and product
  detail reads returned soft-deleted images; both now use
  `Product.live_images`.
- **Editor readiness and publish outcomes (PRs #37, #38).** The "Before you
  publish" sidebar shows the server's publish blockers; "Open Integrations"
  and "Reload draft" lead where they say; a 409 for unreadable live AI text
  and a publish with no reply are no longer reported as an edit conflict or
  a failure.
- **Dependencies and build context.** pyjwt 2.15.1 and urllib3 2.8.0 for 16
  advisories (PR #34); Next.js 15.5.27 / React 19.0.8 (PR #28), axios 1.20.0
  (PR #29), tiptap 3.31.4 and patched dev transitives (PR #30); nested
  `.env` files kept out of Docker build contexts (PR #32).
- **Claude return review remediation, Stages 5–9 (merged into `develop`
  2026-10-01 via PR #26, merge `2b71f65`; author-verified, Cursor review
  pending).** Published AI text is no longer silently replaced by an
  ordinary publish: `store_listings.content_source` / `content_version_id`
  (migration `0035`) and an explicit, confirmed `replaceAiContent` (E-1).
  Editor actions no longer cause self-made 409s (I-1, G-2); history offers
  no invalid Activate (I-2); tone allowlist (G-3); bulk-run composite store
  FK, Stage 9 schema drift and cancellation requests (migration `0036`;
  B-2, D-1, H-2); non-blocking cancel, bounded republish, time-slice
  continuation, per-item failure policy (H-1..H-5); atomic OAuth state use
  on the Redis 3.0 baseline (C-1); NAT64 / IPv4-compatible SSRF residuals,
  whole-fetch deadline and off-loop DNS (B-3); beat schedule path and real
  health checks, including a Lightsail beat that could not start on its
  read-only filesystem (J-4, K-4). Ledger:
  [REVIEW_REMEDIATION_STAGE_5_9.md](docs/REVIEW_REMEDIATION_STAGE_5_9.md).

### Added

- **Phase 9 Stage 10 — AI Product Studio (PR #36).** `/ai-studio/products/[id]`
  reviews one product: preview, compare with the current draft, approve the
  exact candidate with its token, publish with the post-approval token; the
  Live on Shopify line reads the listing, never approval state. `/ai-studio`
  selects up to 50 drafts and published products for one bulk preview run,
  polls it to a terminal status and links each finished candidate. The
  one-click "Optimize with AI" controls are retired for Studio links; the
  backend route stays. No backend change, no migration. Report:
  [PHASE_9_STAGE_10_COMPLETION.md](docs/PHASE_9_STAGE_10_COMPLETION.md).

- **Phase 9 Stage 9 — Celery bulk pipeline preview (merged into `develop`
  2026-09-21 via PR #24, merge `72e7692`; post-merge CI 35656740281
  10/10).**
  Durable `pipeline_bulk_runs` / `pipeline_bulk_run_items` (migration
  `0034`). One sequential `ai.process_pipeline_bulk_run` task on the
  default queue creates inactive Stage 7 preview candidates only — no
  approve, no publish. PostgreSQL is progress truth. One-active-run is a
  partial unique index. Idempotency key + fingerprint. Lease fencing,
  Txn A / success-only Txn B / fresh classified failure. ConflictError
  is a task retry. Retries 0/1/2 re-raise; ≥3 owner-fail-and-return.
  Reconciler every 5 minutes. Four `RequireAdmin` routes under
  `/api/v1/products/pipeline/runs`. Real RabbitMQ harness plus duplicate
  delivery. No frontend, no Stage 10, no deploy. Report:
  [PHASE_9_STAGE_9_COMPLETION.md](docs/PHASE_9_STAGE_9_COMPLETION.md).

- **Phase 9 Stage 8 — Pipeline API (merged into `develop` 2026-09-20 via
  PR #22, merge `91a7069e`).** Four `RequireAdmin` routes over Stage 7
  `ProductPipelineService`: POST preview (201), GET exact preview (200),
  approve (200), publish (200). Required `expectedUpdatedAt` on
  approve/publish. T0 approval / T1 publish. Lost-approve retry is 200
  no-op returning current T1. NULL/empty image analysis projects as
  `analysis: null`. Existing Shopify publisher only; AI SEO is not
  overlaid. No migration at merge (Alembic head was still `0033`), no
  frontend, no Celery, no deploy. Post-merge CI 35529859035 10/10.
  Report:
  [PHASE_9_STAGE_8_COMPLETION.md](docs/PHASE_9_STAGE_8_COMPLETION.md).

- **Phase 9 Stage 7 — Pipeline (merged into `develop` 2026-09-19 via
  PR #19).** Preview / exact-candidate approve / overlay publish.
  Reviewed head `d42c1a4606d227e79cfb0d1232534606dd39d508`. Merge
  `ffa0d6e37db218c85a1facdc265087553c694e02`. PR CI 35472626657 10/10;
  post-merge CI 35474423524 10/10. No HTTP route, no frontend, no
  migration (Alembic head remains `0033`), no Celery, no deploy. Legacy
  `POST /products/{id}/optimize` still auto-activates unmarked rows.
  Pipeline publish fail-closes synthetic / stub provenance and sanitizes
  description at overlay build. Independent review PASS (BLOCKER 0,
  HIGH 0, MEDIUM 0). Report:
  [PHASE_9_STAGE_7_COMPLETION.md](docs/PHASE_9_STAGE_7_COMPLETION.md).

- **Phase 9 Stage 6 — Image analysis (merged into `develop` 2026-09-19
  via PR #16).** Product-scoped fetch, decode, deterministic
  blur/duplicate checks, and `analyse_image` caption/alt proposals
  persisted as nullable JSONB `product_images.analysis` (migration
  `0033`). No public endpoint, no `ProductImageRead` field, no frontend,
  no merchant-field overwrite, no live vision provider. Pillow pinned at
  `12.3.0`. Reviewed head `ae67907faf1abda197c76e441d62c501ad6bbeae`.
  Merge `96b890d25f43865d41e6ba27896b0923dbb09db2`. PR CI 35447520423
  10/10; post-merge CI 35449956506 10/10. Report:
  [PHASE_9_STAGE_6_COMPLETION.md](docs/PHASE_9_STAGE_6_COMPLETION.md).

- **Phase 9 Stage 5 — Optimization-quality scoring (merged into `develop`
  2026-09-18 via PR #13, merge `e1558d5c`; post-merge CI 35392825501
  10/10).** A deterministic, model-free rubric written on every
  `ProductVersion` at creation: title
  length, plain-text description length, repetition/distinctness, and
  coverage of the merchant's own keywords (rescaled out when none exist),
  0–100 with integer round-half-up. AI versions record `qualityBaseline`
  (the original's recomputed score) and `qualityDelta`. Five new keys in the
  existing JSONB `content` — no migration, no back-fill, no endpoint;
  `ProductVersionRead` exposes them as optional. The scorer imports nothing
  from `app.ai`; `quality_scorer` stays seeded and unwired; `seo_score.py`
  (the merchant listing SEO score, a different concept) is untouched. With
  `StubProvider` every AI version scores a constant, so a delta today
  describes the supplier's original, not an AI improvement. Plan:
  [PHASE_9_STAGE_5_PLAN.md](docs/PHASE_9_STAGE_5_PLAN.md); report:
  [PHASE_9_STAGE_5_COMPLETION.md](docs/PHASE_9_STAGE_5_COMPLETION.md).

- **Phase 9 Stage 4 — Generation services (merged into `develop`
  2026-09-17 via PR #11, merge `c3814e8b`; post-merge CI 10/10).** The
  seeded `seo_optimizer` prompt runs as a third generation through the
  unchanged `PromptService.test_render` → `get_ai_provider` → `StubProvider`
  path, with a `keywords` variable sourced from merchant data
  (`search_topics` → `tags` → `meta_keywords`). Its raw completion is stored
  verbatim under `seoTitle` / `seoDescription` / `keywords` in the
  AI-generated `ProductVersion.content` (identical under the stub; nothing
  parsed or fabricated) and exposed as optional fields on
  `ProductVersionRead`. All-or-nothing with the title and description
  calls; merchant SEO fields, supplier fields, and the M2A editor
  concurrency are never written. No migration. Report:
  [PHASE_9_STAGE_4_COMPLETION.md](docs/PHASE_9_STAGE_4_COMPLETION.md).


- **UX-L2D-07 — Cross-app hardening (on `feature/ux-l2d-dashboard`; merged
  into `develop` 2026-09-17 via PR #9 — see "Phase 1 and Phase 2 integrated"
  under Changed).** Journey, accessibility, responsive (1440/1280/1024/768/390,
  light and dark), vocabulary, dead-UX and performance pass over the accepted
  UX-L2D-01…06 work. Removed the editor's `AI tools` and `History`
  placeholder sections (real actions stay in the More menu); neutralised the
  hard-coded `$`/USD on Analytics (figures shown as recorded — BACKEND
  DEPENDENCY — ANALYTICS CURRENCY); aligned Home's channel words with the
  Integrations vocabulary; reworded the last ambiguous "live" copy;
  catalogue cards from `lg`; named section-strip chevrons; focusable
  skip-link target; non-animating status badges; Home shares the bell's
  notifications request; the editor no longer polls the store list every
  15 s; deleted unreferenced L2A-era header components and helpers. Two
  timing-sensitive assertions made deterministic. New
  `ux-l2d-hardening.spec.ts` (semantics, keyboard reachability, the
  visual matrix, cross-app journeys). Vitest 74/74 locally, not a CI gate.

- **UX-L2D-06 — Channels / Integrations (on `feature/ux-l2d-dashboard`;
  merged into `develop` 2026-09-17 via PR #9).**
  `/settings/integrations` is the canonical connect, repair
  and disconnect surface; `/stores` becomes a supporting record view
  (`MAKE INTEGRATIONS CANONICAL; KEEP STORES AS SUPPORTING ROUTE`). One
  connection-state vocabulary (`lib/channel-state.ts`, 28 Vitest scenarios)
  maps the real status payloads — Shopify `configured`/`status`/
  `webhookHealth`, AliExpress computed `connected`/`isTokenExpired`, eBay
  `configured`/`connected`/`needsReconnect`/`reconnectReason`, `Store.status`
  — to Checking · Status unavailable · Setup unavailable · Not connected ·
  Awaiting authorization · Connected · Needs attention · Reconnect required.
  Shared `ChannelCard`/`ChannelStatusBadge`/`DisconnectDialog`; a channel
  overview strip; disconnect confirmations that state the backend's actual
  consequences; role-aware cards (viewers see state, not buttons that 403);
  Shopify's raw `lastError` no longer rendered; AliExpress "not configured"
  422 becomes an operator-facing state instead of env-var names. The manual
  "Add store" dialog is removed from the merchant UI (it created unauthorised
  `pending` rows that surfaced in the editor's store list); `/stores` keeps
  its route, drops the unexplained health number, and links every row to
  Integrations. Backend-less `channels.spec.ts` (25 × 2 projects) covers
  every state; `integrations.spec.ts` gets the historical main-scoped
  selector and the confirm step. Backend dependency recorded: AliExpress
  status has no `configured` flag.

- **UX-L2D-05 — Product editor lifecycle clarity (on
  `feature/ux-l2d-dashboard`; merged into `develop` 2026-09-17 via PR #9).**
  One pure derivation
  (`lib/editor-lifecycle.ts`, composing the UX-L2D-04 listing authority) now
  drives the header badge, save indicator, primary action, mobile bar,
  post-publish panel and Review & publish. Vocabulary: Not on Shopify /
  Added to Shopify / Visible on your shop (only on
  `onlineStorePublished === true`) / Changes not sent to Shopify (only when
  the saved draft is provably newer than `lastSyncedAt`) / Sending to
  Shopify… / Publish failed / Shopify status unavailable; save state reads
  Unsaved changes / Saving… / Saved in DropPilot / Couldn't save / Saving
  paused. "Draft — not live" and "your live product has not changed" are
  gone (UX-L2D-01 F-4). Header controls navigate (Review & publish, Update
  Shopify, View product, Resolve conflict); only the panel publishes, and
  it separates "Validation passed" from "Published to Shopify". The
  section strip gains edge fades, chevrons and scroll-to-selected (F-9);
  the header's inline actions move to `xl` so 1024 keeps a readable title;
  the mobile bar gives the primary action the width. Conflict UX is
  presentation-only: banner focus on the `none → detected` transition,
  dialogs return focus on close, manual Save withdrawn and publishing
  disabled with the reason while a conflict is open — `expectedUpdatedAt`,
  409 handling, the phases and the autosave gate are unchanged. Vitest is
  added for the pure lifecycle modules (`npm run test:unit`, 46 tests, not
  run by CI); Playwright `editor-lifecycle.spec.ts` (30 × 2 projects,
  backend-less) covers the rendered states; `editor-fixture.ts` gains
  listings/publish responders only.

- **UX-L2D-04 — Catalogue, Drafts and the product page (on
  `feature/ux-l2d-dashboard`; merged into `develop` 2026-09-17 via PR #9).**
  Closes the UX-L2D-01 Critical:
  `/products/[productId]` exists, adapted from the reviewed historical UX-L2C
  page (UX-L2D-GATE-04) — reads `GET /products/{id}` and
  `GET /drafts/{id}/listings`, sends a product with no synced listing to the
  draft editor instead of showing it as published, answers a non-UUID path
  as "Product not found" without calling the API, renders missing and
  foreign ids identically, strips description HTML to text and links out
  only to HTTPS `*.myshopify.com` URLs the server supplied. Products rows
  navigate and say "Published" (the list endpoint's own predicate), not a
  hard-coded Shopify lifecycle. Drafts and Products gain server-side search
  (`q`), sort (`sort_by`/`sort_dir` — the wire names the API reads), paging
  from the server's `meta`, URL-synchronised state (refresh, Back/Forward,
  shareable links, safe fallbacks for bad values), distinct empty /
  no-results / out-of-range / error states, one labelled primary action per
  row with icon-only secondary actions, and a card list below `md`.
  `lib/listing-lifecycle.ts` and `lib/external-link.ts` are the listing
  authority and link allowlist; `useDraftListings` takes the reviewed
  `retry: false` / `refetchOnMount: "always"`. No lifecycle/AI/readiness
  filter (no API parameter) and no thumbnails (no list-row image field) —
  both recorded as backend dependencies. Vitest deferred; coverage is
  Playwright (`catalogue.spec.ts`, backend-less) plus the adapted
  backend-gated `product-route-isolation.spec.ts`.

- **UX-L2D-03 — Home (on `feature/ux-l2d-dashboard`; merged into `develop`
  2026-09-17 via PR #9).** The
  analytics wall (fourteen metric cards, three charts, revenue hard-coded to
  `USD`) is replaced by an operations page built only from endpoints that
  exist: a needs-attention list (expired or degraded channels, failed imports,
  failed AI optimisation on recent drafts, order-sync failures, unread failure
  notifications), a single rule-based next step, three catalogue tiles from
  `workspace-counts` and `orders/statistics`, the five most recently edited
  drafts (`GET /drafts?sort_by=updated_at`), channel status as word plus
  sentence, and recent notifications. A workspace with nothing connected and
  nothing imported gets a three-step setup instead of zeros. Home shows no
  monetary figure: the analytics revenue sums mixed-currency orders with no
  currency in the payload, so no label is true (that caveat now sits on
  Reports as a known limitation). No "ready to publish" count is shown — a
  per-draft server check with no aggregate remains a backend dependency.
  `services/list-query.ts` translates `sortBy`/`sortDir` to the `sort_by`/
  `sort_dir` names the API reads (drafts, products, imports previously sent
  camelCase and were silently unsorted). `openMockedEditor` answers the
  shell's three requests so the editor suites run without a backend. Charts,
  stat cards and order-sync cards remain on `/analytics` and `/orders`. The
  `/products/[productId]` Critical stays open for UX-L2D-GATE-04.

- **UX-L2D-02 — application shell (on `feature/ux-l2d-dashboard`; merged
  into `develop` 2026-09-17 via PR #9).**
  `PageContainer` applied once in `(protected)/layout.tsx` gives
  every page the same gutter and a centred `2xl` maximum width; Drafts,
  Products, Import history and the editor no longer render flush against the
  sidebar (UX-L2D-01 F-5). Navigation is re-grouped by merchant job — Home ·
  Catalogue · Sales · Channels · Automation · Reports — with Settings pinned
  beneath a scrolling list that fades the edge hiding items, so nothing falls
  below the fold at laptop heights (F-7); planned destinations leave the
  sidebar; the top bar drops its two permanently disabled placeholder controls
  and shows a section › page breadcrumb. Backend-less regression
  `ux-l2d-shell.spec.ts` (gutters at 1440/1024/390, manifest completeness,
  every href resolves, breadcrumb, sidebar at 900 and 640 px, drawer, dark
  mode). No backend, auth, concurrency, integrations or historical UX-L2C code
  changed; the `/products/[productId]` Critical stays open for
  UX-L2D-GATE-04.

- **UX-L2D-01 — Phase 2 baseline and UX audit (on `feature/ux-l2d-dashboard`,
  stacked on the accepted Phase 1 SHA `c0092da6`; merged into `develop`
  2026-09-17 via PR #9).** Documentation
  only: `docs/ux/ux-l2d-01-baseline.md` records the repository baseline, the
  frontend architecture, a screen inventory, 15 findings (one Critical: every
  link on `/products` 404s because the accepted tree has no
  `/products/[productId]` route), the proposed job-based information
  architecture, design-system direction, Home/Drafts/editor proposals and the
  UX-L2D-02…07 backlog. The programme was renamed from UX-L2C to **UX-L2D**
  because `feature/ux-l2c-live-state-clarity` already carries unmerged
  historical UX-L2C work; that branch is subject to **UX-L2D-GATE-04**, a
  mandatory behaviourally verified review before UX-L2D-04, and the Critical
  finding stays open until the gate resolves ownership. No application code
  changed.

### Changed

- **Phase 1 and Phase 2 integrated into `develop` (2026-09-16/17).** Phase 1
  (Git/CI baseline, `fix/ci-baseline-security-pytest`, accepted SHA
  `c0092da6`) was merged through PR #7 on 2026-09-16, merge commit
  `5e21927`; CI on that merge commit: run 35164379004, 10/10 jobs. Phase 2
  (UX-L2D dashboard programme, `feature/ux-l2d-dashboard`) was merged
  through PR #9 on 2026-09-17, merge commit `b52b223`. The original frozen
  review SHA was `3f1ede8` (tree `0aac7f36`); two remediation commits
  (`2351bcc`, `41c152c`) followed and were reviewed as a delta, so the
  final accepted head is `41c152c` (tree `489366a9`, 27 commits on
  `c0092da6`). Evidence, in order: independent frozen-SHA review PASS
  (recorded in the PR #9 body) and remediation delta review PASS (recorded
  in the merge commit message of `b52b223`); no formal GitHub review
  submission exists on either PR; CI on `41c152c` (run 35109481727) and on
  `b52b223` (run 35191388324), both 10/10 — ruff, ruff format, mypy, pytest
  2984 passed; typecheck, lint, build; Playwright 722 passed / 0 failed /
  9 skipped; then the owner's merge. **Merged into `develop` only:** `main`
  and production are unchanged. The Phase 1 and UX-L2D-01…07 entries had
  their status wording amended to match; their bodies are unchanged.
  Still open after the merge: the Celery exclusive-pidbox
  multi-worker/restart drill (safe staging), whether Vitest becomes a CI
  gate, and the backend dependencies recorded in
  `docs/ux/ux-l2d-phase-2-status.md`.

- **Documentation state corrections.** UX-L2B is present in `develop`
  (`a543b029` is the tip of `feature/ux-l2b-publish-integrity`); earlier
  entries below describing it as "not merged" predate that fast-forward.
  Phase 1 (`c0092da6`) was independently accepted by Cursor on 2026-09-15
  (`PASS — PHASE 1 MAY CLOSE`); PR #7 was still a draft at that date
  (merged 2026-09-16 — see the entry above).
  Historical UX-L2C (`feature/ux-l2c-live-state-clarity`, `ece8322`) exists
  remotely, unmerged and not independently accepted.

### Fixed

- **Phase 1 — Git/CI baseline and PR #7 closure (on
  `fix/ci-baseline-security-pytest`; merged into `develop` 2026-09-16 via
  PR #7).** Owner-adopted the bounded
  12-file baseline repair (scanner-required blank template secrets, per-run R7
  Fernet key, `tests/__init__.py`, exact blank-secret assertion, PostgreSQL 17
  client parity, deterministic Compose password probe, Celery
  `control_queue_exclusive`/`event_queue_exclusive`, broker-verification module
  loading, `default,integrations` CI worker queue, exact "Sign in" and
  pathname/query auth assertions, `127.0.0.1` FE/API origins) and closed two
  independent-review findings. F-1: a context-level Playwright fixture
  (`tests/e2e/fixtures/provider-isolation.ts`) now fulfils
  `accounts.google.com/**` locally for `auth-provider-boundary`, `auth`,
  `smoke` and `terms`, with a regression and a same-origin negative control
  (`provider-isolation.spec.ts`); Google-auth (`auth-g1`) and CSP coverage keep
  their own stubs. F-2: CI adds `list` + `json` reporters and publishes the
  Playwright report on success as well as failure, named by SHA/run/attempt,
  so skip identities and reasons are inspectable from a green run. **Runtime
  note:** the two Celery settings are application configuration, not CI-only —
  every worker's pidbox control queue and every event receiver's queue becomes
  exclusive on the next deployment. Single-worker CI verifies declaration,
  `inspect ping` and end-to-end execution of five tasks; multi-worker,
  restart/reconnect and external-monitor behaviour are unverified and gated to
  the safe-staging phase. CI's floating `rabbitmq:4-alpine` resolved to
  RabbitMQ 4.3.5, which denies the deprecated `transient_nonexcl_queues`
  declaration and is what exposed the failure; the Lightsail pin
  `rabbitmq:4.0-alpine` and the local `rabbitmq:4-management-alpine` tag have
  not been exercised against the new settings outside CI. Independently
  accepted by Cursor on 2026-09-15 (`PASS — PHASE 1 MAY CLOSE`) at
  `c0092da6`; PR #7 merged into `develop` 2026-09-16 (`5e21927`); not
  deployed.

- **UX-L2B-R4 — stabilise isolated live-API Chromium + store guidance (on
  `feature/ux-l2b-publish-integrity`, not merged).** Closes the six R3 full-run
  failures with harness/env corrections (not UX-L2B redesign): DB-seeded editor
  fixtures instead of stale `E2E_*` / AliExpress-dependent seed; POST-only
  `/products/import` response matcher; global-rules history waits for PATCH
  200 before reopening Edit; OAuth malformed-callback asserts redirect
  `Location` against this stack’s `SHOPIFY_FRONTEND_RETURN_URL` (R3
  `CONNECTION_REFUSED` was Chromium following a redirect to dead `:3105`);
  Redis logical DBs kept in `0–15` with synthetic rate-limit
  `SECURITY_RATE_LIMIT_REQUESTS=500`. Product: header/checklist store copy
  follows workspace store state (`deriveStoreStatusLabel`) — never claims
  “Connect Shopify” while stores are loading or when a connected store
  exists without a listing. Clean full Chromium (workers=1): **482 passed,
  9 skipped, 0 failed, 0 did not run**, ~20.6m. Backend suite carried forward
  from R2: **2976 passed, 1 skipped**. No migration; Alembic head ``0032``.
  Not independently accepted; not merged or deployed; UX-L2C not started.

### Changed

- **UX-L2B-R3 — locked-state and live-API evidence (on
  `feature/ux-l2b-publish-integrity`, not merged).** Proves that after waiting
  for the product row lock, publication re-runs authoritative readiness
  (including `expectedUpdatedAt`) and re-reads store/listing state before any
  provider call: edit-while-waiting → 409 + zero creates; destination /
  disconnect / soft-delete while waiting → blockers/404 + zero creates.
  Adds barrier-controlled HTTP dual-POST concurrency (create count = 1) and
  Chromium/mobile visual + a11y evidence outside Git. No product-code change;
  no migration; Alembic head remains ``0032``. Backend suite carried forward
  from R2: **2976 passed, 1 skipped**. Not independently accepted; not merged
  or deployed; UX-L2C not started.

### Fixed

- **UX-L2B-R2 — serialise concurrent Shopify publication (on
  `feature/ux-l2b-publish-integrity`, not merged).** Closes the R1-found race
  where two simultaneous publishes could both miss ``StoreListing``, both miss
  the deterministic remote handle, and both create a Shopify product.
  Mechanism: tenant-scoped product ``SELECT ... FOR UPDATE``
  (`ProductRepository.lock_for_update`, ``PUBLISH_LOCK_TIMEOUT_MS=30000``)
  acquired after readiness/version checks and before any provider client call;
  ``StoreListing`` is re-read under the lock; lock releases on commit/rollback.
  Bounded wait surfaces stable ``shopify_publish_busy`` (409). Handle-based
  ``_create_or_adopt`` remains the recovery path after a lost response. No
  Redis; no migration; Alembic head remains ``0032``. Wording: at-most-one
  concurrent provider create for the same publication identity, with
  deterministic adoption on retry — not exactly-once delivery. Full backend
  single clean suite after the fix: **2976 passed, 1 skipped**, ~618s. Not
  independently accepted; not merged or deployed.

### Changed

- **UX-L2B-R1 — mandatory security/data-integrity evidence (on
  `feature/ux-l2b-publish-integrity`, not merged).** Adds HTTP cross-tenant
  404 matrix for publish-readiness and publish (foreign draft/store,
  random-vs-foreign indistinguishability, provider call count zero);
  documents repository-layer isolation as **N/A** (no repository files in
  UX-L2B diff) with compiled-SQL proof that readiness product load still
  carries `tenant_id` + `deleted_at`; Redis 3.0.504 inventory shows **zero**
  Redis commands on the publish/readiness path (handle-based
  `_create_or_adopt` / `droppilot-{product_id}` plus R2 product-row lock is the
  named idempotency/concurrency mechanism — no Redis lock; R1 concurrent
  double-POST gap is closed in R2);
  OpenAPI/schema/camelCase/`requestId` assertions; synthetic isolated env
  proof outside Git. Test harness: synthetic `GOOGLE_OAUTH_CLIENT_ID` + pinned
  `SECURITY_RATE_LIMIT_REQUESTS=100` so isolated worktrees without a root
  `.env` do not false-fail AUTH-G1 / eBay C0. Full backend suite single clean
  run: **2959 passed, 1 skipped**. Not independently accepted; not merged or
  deployed.

### Added

- **UX-L2B — server-authoritative publish integrity (on
  `feature/ux-l2b-publish-integrity`, not merged).** Failed or conflicted
  draft saves can no longer continue into Shopify publish. `handleSave`
  returns an explicit success/failure contract; `handlePublish` stops on
  save failure, 409, auth errors, or newer unsaved edits, and always sends
  `expectedUpdatedAt`. New shared `PublishReadinessService` is the single
  authority for both `POST /integrations/shopify/publish-readiness` and the
  final publish path (provider is not contacted when blockers exist or the
  draft version is stale). Review & publish UI separates blockers from
  advice, with no readiness score and no false “ready” claim. No migration.
  Not independently accepted; not merged or deployed; UX-L2C not started.

### Changed

- **UX-L2A integrated into `develop` at `e1dd0f0`.** UX-L2B builds from that
  baseline. Carried-forward unused presentation `readiness.score` /
  `readiness.level` remain non-authoritative.

- **UX-L2A-R5 — responsive sheet and touch-target polish (on
  `feature/ux-l2a-editor-foundation`, not merged).** Closes two Low findings
  after R4 integration acceptance: the mobile “Things to fix” sheet now closes
  its Radix modal state when the viewport crosses Tailwind `lg` (so a resize
  cannot leave a dark inert overlay), and the trigger meets a 44×44 CSS-pixel
  minimum target. History Back assertions match `router.replace` behaviour.
  Playwright refuses empty/` :8000`/production API defaults unless CI opts in.
  Not independently accepted yet; not merged or deployed; UX-L2B has not
  started. **Carried forward (mandatory UX-L2B / publishing integrity):**
  `handlePublish` awaits `handleSave` when dirty, but `handleSave` swallows
  failures, so a failed save or 409 may still allow the publish POST. Also
  note unused `readiness.score` / `readiness.level` and unused generic tab
  `blocked` capability — do not treat either as authoritative readiness.

- **UX-L2A-R4 — close independent-review findings (on
  `feature/ux-l2a-editor-foundation`, not merged).** Independent review found
  fixes. Presentation readiness advice no longer hard-disables navigation to
  Review & publish; server/channel checks remain authoritative for actual
  publishing. Mobile “Items to review” sheet now uses the shared accessible
  Sheet primitive (Escape, focus trap, focus return). History menu labels are
  distinct (`View recent activity` vs `Open full history`). Dead
  `ProductStatusGroup` / `% Ready` completion claim removed. Celery worker
  detection wait restored to 30s under a 60s suite timeout. Branch remains
  unmerged; production remains undeployed; UX-L2B has not started. Acceptance
  is not claimed before re-review.

- **UX-L2A-R2 — matching isolated-stack verification harness (on
  `feature/ux-l2a-editor-foundation`, not merged).** Proves the original
  `draft-editor-concurrency` suite against a backend built from the same
  feature SHA. Test harness only: rich-text clean-load assertions follow the
  L2A rule that Save is hidden when there is nothing to save; AliExpress
  connect tests skip when platform credentials are absent; Celery-dependent
  global-rules waits can skip without racing the suite timeout. No product
  behaviour change. Independent review and production deploy are not claimed.

- **UX-L2A-R1 — evidence and state corrections (on `feature/ux-l2a-editor-foundation`,
  not merged).** Removes invented Required/Recommended checklist labels (the
  API does not classify findings; checklist items are “Items to review”). Save
  wording includes a real `Saving…` state from the in-flight request, with
  generation guards so a stale response cannot clear newer edits. Before
  screenshots were reconstructed from parent `d1360a1` for comparison — they
  were not captured during the original L2A implementation. Independent review
  and production deploy are not claimed.

- **UX-L2A — product editor foundation (candidate, not accepted).** Frontend-only.
  The draft editor command bar shows the product, draft/live status, save
  status, store, and next action in the first viewport. Sections are grouped
  (Product, Selling, Improve, Publish) without changing URLs. Shipping explains
  a missing supplier price in plain English and never presents it as free. The
  inspector is a “Before you publish” checklist. Production CSP still has no
  `'unsafe-eval'`; login uses `method="post"`. Business rules, APIs, and
  publishing behaviour are unchanged. Baseline screenshots were missed before
  edits and later reconstructed under UX-L2A-R1.

### Added

- **INFRA-L1 — the AWS Lightsail deployment foundation.** The application has
  only ever run on a Windows host, from drive-letter paths, started by Task
  Scheduler, against a loopback PostgreSQL with no TLS. None of that survives a
  move to Linux, and the parts that would have been quietly carried over are the
  dangerous ones.

  **The one code change is transport security.** `DatabaseSettings` had no TLS
  support at all — correct when the database was a loopback process, wrong the
  moment it becomes a managed service on AWS's network. There is now an
  `sslmode`/`sslrootcert` pair read by both drivers: psycopg takes them as libpq
  query parameters, asyncpg takes an `ssl.SSLContext` built from the same
  setting, so the async engine and Alembic's synchronous one cannot end up
  enforcing different things against one database. PROD-H1 gained a rule that
  refuses anything below `verify-full` in a deployed environment, and refuses
  `verify-full` without a CA bundle that exists — `require` encrypts the link
  but never checks who answered, which is a different control entirely.

  Everything else is deployment artefacts: a production Compose file where **no
  service publishes a host port**, two networks so that "can reach the frontend"
  and "can reach the broker" are different permissions, `cap_drop: ALL` and
  `no-new-privileges` everywhere, read-only root filesystems with explicit
  writable scratch, bounded logs and memory, and `${VAR:?}` on every mandatory
  variable so Compose refuses rather than substituting a placeholder. PostgreSQL
  is deliberately not a container: a database inside the Compose project dies
  with the instance it was protecting against losing.

  A fourth image, `droppilot-ops`, carries `pg_dump` and Alembic so the
  internet-facing backend image does not. Its default command does nothing, so
  starting it by accident cannot migrate anything.

  Ingress is a Cloudflare tunnel that dials out — no inbound web rule, no origin
  IP to route around the WAF, no certificate to expire — with a catch-all that
  refuses rather than routing. systemd replaces Task Scheduler, with `--wait` so
  the unit is active only once health checks pass. Six deployment scripts
  refuse a dirty tree, a wrong commit, loose secret permissions, a published
  port, a development server, or an image not pinned by digest.

  **128 tests read the artefacts and assert what they exist to guarantee.** They
  caught two real defects in this milestone's own work: the deployment scripts
  were tracked mode 644 and would have landed on the instance as "Permission
  denied", and the cloudflared template was not valid YAML.

  **Not proven: the stack has not been run.** Docker Desktop's Linux engine
  cannot start on this machine — WSL2 has no distribution installed — so no
  image was built and no container started. The Compose file was validated
  client-side, which is real but partial. Nothing is provisioned on AWS,
  Cloudflare, Google or Resend, and production remains the Windows host,
  untouched.

- **BACKUP-B1-R1 — protected production database names are now policy, not
  spelling.** The accepted BACKUP-B1 review left one informational finding: the
  protected set was matched with exact, case-sensitive equality, so a database
  called `DropPilot` walked past every guard `droppilot` was stopped by. That is
  a protection you can step over by holding shift.

  There is now one canonicalisation authority in
  `app/core/database_identity.py`, and every guard goes through it — the backup
  service, all four scripts, and the erasure tool that already shared the
  module. Comparison is `casefold`-based and trims surrounding whitespace, so
  `DROPPILOT`, `DropPilot` and ` droppilot ` are all protected without any of
  them being listed.

  **Two comparisons, deliberately kept apart.** *Policy* — "is this a protected
  name?" — is case-insensitive. *Identity* — "is the server I am connected to
  the one I was told to expect?" — stays **exact**, because two PostgreSQL
  databases whose names differ only in case are two different databases, and a
  tool that picked one on the operator's behalf would write to a database
  nobody named. Loosening the second while loosening the first was the
  plausible mistake here, and it is asserted against directly.

  **Nothing is rewritten.** The canonical form is a comparison key: never
  returned as a name, never written to a manifest, never handed to libpq. It is
  also deliberately *not* NFKC-normalised — normalising would map a fullwidth
  `ｄｒｏｐｐｉｌｏｔ` onto `droppilot`, and refusing an operation on an innocent
  database while calling it production is the worse failure. Homoglyphs are
  left alone for the same reason; exact identity governs them.

  Operator-supplied names are now gated rather than repaired: surrounding
  whitespace, control and format characters (including bidirectional
  overrides), non-strings and anything past PostgreSQL's 63-byte truncation are
  refused — before configuration is even read, so a typo is reported as a typo.
  Refusals name the flag and never echo the value, and every database name that
  does reach a message goes through a renderer that strips anything
  unprintable.

  **The production-restore confirmation now runs against the observed
  identity.** It used to prompt with the name from the command line, which is
  the string under suspicion; it now prompts with what `current_database()`
  reported, after the identity check has already refused any disagreement. The
  flag alone is insufficient, a confirmation alone is insufficient, a rehearsal
  never prompts, and the flag remains unexercised.

  No operational blocker changes: no production backup, off-site copy, key
  custody, scheduler or alerting exists, and the retention policy remains
  proposed.

- **BACKUP-B1 — encrypted PostgreSQL backups, verification, restore and
  retention.** The production database held 1,268 tenants with no backup of any
  kind: a bad migration or a failed disk lost every customer's workspace with no
  route to recovery. There is now tooling to take, prove and restore an
  encrypted backup — and it has been proven end to end, **on isolated databases
  only**. No production backup has been taken, and publication stays blocked
  until one has.

  Backups are encrypted with an AES-256-GCM envelope: a fresh random data key
  per file, wrapped by a long-lived key from the operator's secret store, with
  the dump split into authenticated 4 MiB chunks whose nonces carry a counter
  and a final-chunk flag. A file that stops early is therefore *refused as
  truncated* rather than accepted as short — the failure you would otherwise
  discover 40 GB into a recovery. There is no unencrypted mode. The backup key
  may not be `SECURITY_SECRET_KEY`, `SECURITY_OTP_HMAC_KEY` or any
  `SECURITY_ENCRYPTION_KEYS` entry, by value or by decoded bytes: a backup
  contains those keys' ciphertext, and an attacker holding both has live
  marketplace access rather than a historical read.

  **Existence is never treated as verification.** `verify` checks the manifest
  against a closed schema, its HMAC, the file's size and SHA-256, then decrypts
  the whole stream and asks `pg_restore` to parse a non-empty table of
  contents. **Configuration is never treated as identity**: every operation
  states the database it expects and confirms it with `current_database()`
  against the server that will actually be read or written. `droppilot` is
  refused in both directions without an explicit flag, and restoring over it
  additionally requires a typed database name at a terminal — a flag that has
  never been exercised.

  The database password never reaches an argument vector or an environment
  variable; it is handed to `pg_dump` through a password file in a directory
  ACL'd to one account, shredded afterwards. Publication is atomic — the
  manifest is renamed last, and a ciphertext that cannot get one is removed,
  because a backup nothing can verify is worse than an absent one.

  Retention is grandfather-father-son and refuses more than it deletes: never
  the newest verified backup, never the only one, never anything unverifiable
  (those are quarantined, not removed), and never outside one absolute,
  non-symlinked, dedicated directory. The proposed 14 daily / 8 weekly / 12
  monthly tiers are **for review, not settled** — twelve monthly copies means an
  erased person can persist for a year, which is a data-protection decision.

  The production-readiness `BACKUPS` rule now reads evidence instead of being a
  constant: an authenticated record of a recent successful backup, a recent
  successful restore drill, a valid key, a directory, and a recorded off-site
  destination. Markers are MAC'd so "backups are healthy" cannot be asserted by
  anyone who can create a file, and a failed run never writes one. It reports
  `BLOCKED`, listing exactly which of the five is missing.

  A Windows Task Scheduler template is committed and **deliberately not
  installed**. Design, threat model and the honest list of what is still
  missing: [docs/operations/BACKUP_RUNBOOK.md](docs/operations/BACKUP_RUNBOOK.md).

  Also declares `cryptography` as a direct dependency. Four application modules
  import it — credential encryption, the eBay signature verifier, and now the
  backup container — but nothing declared it, so it arrived transitively
  through `google-auth`. A transitive dependency is one upstream release away
  from disappearing, and the module that would stop importing is the one that
  encrypts every backup. No version change: it was already installed.

- **PROD-H1 — production-readiness rules, an operator checker and a runbook.**
  Every production guard in this application is conditional on `is_deployed`,
  and the live host runs as `ENVIRONMENT=local` — so none of them is in force.
  The process starts, reports healthy, and each check written to protect it is
  switched off.

  The rules now live once as data in `app/core/production_readiness.py`, with
  two readers: `Settings.model_post_init` walks the startup-enforced entries as
  a backstop after its existing guards, and
  `scripts/verify_production_config.py` walks all of them and reports the whole
  picture without starting anything — no database, no Redis, no HTTP, no write.
  Exit codes separate *ready* from *invalid* from *publication blocked* from
  *dependency missing*, with `1` reserved for the tool's own usage errors so a
  broken invocation is never mistaken for a verdict.

  **No configuration value is ever printed.** Reasons describe a property — a
  length band, whether a value matches a published default, whether a URL is
  loopback — and `Finding` refuses to be constructed with a reason containing
  an equals sign, which is the shape an accidental f-string takes.

  Read-only audit of the real production file, values withheld: the signing key
  is the placeholder **published in this repository**, the OTP key is absent so
  the published default applies, the refresh cookie is not `Secure`,
  `ALLOWED_HOSTS` is a wildcard, and the CORS origin and browser-facing URLs are
  loopback. Nine `FAIL`, two `BLOCKED`, one `MISSING`, two `SKIPPED`, four
  `PASS`. Nothing in production was changed; the runbook in
  [docs/operations/PRODUCTION_CONFIG.md](docs/operations/PRODUCTION_CONFIG.md)
  puts changing `ENVIRONMENT` last, after every guard can pass.

  Also fixed a latent defect the rules found: the deployed encryption guard
  never checked that a key was *usable*, and the fixture two test files called a
  valid production configuration decoded to 34 bytes where Fernet accepts only
  32. A malformed key is now caught at startup rather than the first time a
  merchant connects a supplier.

- **PROD-H1-R1 — the checker reported ambient variables incorrectly.** It took
  the environment snapshot twice: the first call removed the application
  variables, so the second found nothing and the "present in the shell but not
  in the file" category was silently always empty. An operator whose shell held
  a stray `SECURITY_SECRET_KEY` was never told.

  One snapshot now returns both categories, so there is no second call to get
  wrong. Names only, sorted for byte-identical output across runs, values never
  shown; unrelated variables are neither removed nor reported; and the
  environment is restored on every path including errors, so a second run in the
  same process is not auditing the first one's leftovers.

  A UTF-8 byte-order mark at the start of a file is now handled. Windows editors
  add one routinely, and without it the first key parsed as a name beginning
  with U+FEFF — a file whose first line declared the environment read as
  declaring none. Only a leading mark is removed; inside a value it is data.

- **LEGAL-T1 — a draft Terms of Service and a public `/terms` route.** A B2B
  contract for DESIRLY LIMITED trading as DropPilot AI: 34 sections covering
  identity, business-only eligibility, formation, integrations, merchant
  responsibility for marketplace compliance and tax, plans and cancellation,
  acceptable use, liability and governing law. Served like `/privacy` — no
  session, no `AuthProvider`, no API call, and it renders with JavaScript
  disabled.

  **It is a draft and says so.** `TERMS_PUBLISHED` stays `False`, the version is
  `draft-2026-08-31`, the page carries a "pending legal review" banner and is
  `noindex`. No solicitor has seen it. `docs/legal/TERMS_LEGAL_REVIEW.md`
  records the primary sources consulted, the drafting decisions and twelve
  questions a solicitor has to answer.

  **The draft state is now enforced rather than declared.** `TERMS_PUBLISHED`
  previously existed and was read by nothing. A deployed environment now refuses
  registration outright with `422 terms_not_published`, before any other check —
  a stored row saying somebody agreed to unapproved text is worse than no row.
  Local and test environments are exempt so the flow stays buildable, and
  existing users are unaffected and can still sign in.

  Every commercial statement was checked against the code, and the absences are
  stated rather than omitted: there is no billing system, no backups, no data
  export, no self-service cancellation, no SLA, no free trial and no TikTok
  integration, so none of those appears as a promise. The mapping is in
  `docs/legal/TERMS_PRODUCT_AUDIT.md`.

  The statutory company disclosures required by regulation 25 of the Trading
  Disclosures Regulations 2015 and regulation 6 of the E-Commerce Regulations
  2002 now appear on `/terms`, on `/privacy` and in the sign-in footer.

- **AUTH-G1 — Google sign-in and password reset by one-time code.** Sign in with
  Google's own rendered button, and reset a forgotten password with a six-digit
  emailed code. Identities live in a provider-neutral `user_identities` table
  (migration `0031`); no Google ID, access or refresh token is ever stored, and
  the flow uses no client secret because it exchanges no authorization code. A
  Google sign-in whose address already has a local account is refused with
  actionable guidance rather than linked automatically — Google proves who
  controls an address today, not who registered it here.

  Not proven and not claimed: **no live Google sign-in has been performed and no
  real email has been sent.** See
  [docs/auth/AUTH_G1_DEPLOYMENT_HANDOFF.md](docs/auth/AUTH_G1_DEPLOYMENT_HANDOFF.md).

### Changed

- **BREAKING — `POST /auth/register` requires legal acceptance.** The request
  must carry `termsAccepted`, `privacyAccepted`, `termsVersion` and
  `privacyVersion`; a false flag or an unrecognised version is refused with
  `400 legal_acceptance_required`, and the accepted versions are stored on the
  user (migration `0032`). No compatibility window: a signup recorded with no
  evidence of consent is precisely what this prevents. Every caller in this
  repository moved with it.

  There is still **no Terms of Service document**, so the recorded version is
  the sentinel `"unpublished"` and the form says so. A stored row is not
  agreement to terms nobody has written.

- **BREAKING — `POST /auth/google` is replaced by five explicit endpoints**
  (`/google/nonce`, `/google/link/nonce`, `/google/login`, `/google/signup`,
  `/google/link`). One endpoint that inferred sign-in from sign-up could create
  a workspace for somebody who meant to sign in. Nonces are now single-use and
  bound to their intent, so one minted for a login cannot be presented to a
  signup or a link.

### Security

- **AUTH-G1-R3 — a successful step-up no longer erases shared abuse history.**
  Cleanup deleted the per-address counter as well as the per-user one, so any
  account behind an address could wipe every other account's failed attempts
  simply by succeeding on its own: guess twice against a victim, succeed once on
  your own account, repeat. `clear()` is replaced by `clear_user_attempts()`,
  which has no parameter that could reach the shared counter — the split is
  structural rather than a convention a future caller must remember. The address
  counter now drains only through its own bounded TTL.

  The consequence is stated rather than left to be discovered: a successful
  step-up still consumes part of the address budget, so a shared office or CGNAT
  address spends it through ordinary use. `SECURITY_STEP_UP_MAX_ATTEMPTS_PER_IP`
  (default 10) is introduced so the aggregate control can be loosened without
  weakening the per-account ceiling of 3, which is what actually bounds password
  guessing against any one account.

- **AUTH-G1-R2 — `script-src` no longer permits inline script.** The policy
  carried `'unsafe-inline'`, which allows exactly what an XSS payload needs and
  made the directive decorative. It is replaced by a 128-bit nonce minted per
  response in `middleware.ts`; Next.js stamps it onto every script tag it
  renders, and `next-themes` is handed the same value for its own bootstrap
  script. Verified in a browser rather than by reading the header: an injected
  inline script does not run, a guessed nonce does not run, hydration and the
  theme script do, and every script tag in the delivered HTML carries the
  response's nonce.

  **This makes every page render per request.** A build-time prerender has its
  script tags written before the nonce exists. Stated in full in
  [the deployment handoff](docs/auth/AUTH_G1_DEPLOYMENT_HANDOFF.md), including
  the requirement that no shared cache stores these HTML responses.

- **AUTH-G1-R2 — the Google link/unlink password step-up had no rate limit.** A
  stolen session was an unlimited password oracle against the one operation that
  grants a permanent second way into an account. Both operations now share a
  single counter, keyed per user and per address, reserved *before* the hash is
  checked so twenty concurrent guesses cannot all read a counter of zero.
  Alternating between linking and unlinking does not double the allowance, a
  correct password does not lift an active lockout, and Redis being unavailable
  refuses rather than waves the attempt through — the opposite of the login
  throttle, and for a stated reason.

### Fixed

- **AUTH-G1-R3 — the step-up counter could be created without an expiry.**
  `INCR` and `EXPIRE` were separate round trips, so an interruption between them
  left an immortal key: an account permanently unable to link or unlink a
  sign-in method until somebody found and deleted the key by hand. Both
  dimensions are now counted by a single Lua script, so the counter and its
  expiry exist together or not at all, and any key found without an expiry is
  repaired on first use — counters left behind by the previous version heal
  themselves.

  Measured rather than argued: under identical cancellation fault injection
  against real Redis 3.0.504, the old two-round-trip pattern left **199 immortal
  keys in 200 rounds**; the script left **0**.

  The same change fixes a second-order defect. The lockout expiry was re-applied
  on *every* attempt past the ceiling, so an attacker holding a stolen session
  could keep the real account holder locked out indefinitely by continuing to
  guess. It is now assigned once, on the attempt that crosses the ceiling, and
  the window below the ceiling is fixed rather than sliding.

- **AUTH-G1-R2 — the password-reset retry loop was unbounded.** `while True`
  around an optimistic lock terminates only because conflicts happen to be rare.
  Under a burst it was a request that never returned while holding a connection
  and an event-loop slot. It now retries eight times with jittered backoff and
  then raises `503 password_reset_busy`: nothing written, no attempt spent, no
  ticket issued, and the challenge left usable. Deliberately not reported as a
  wrong code, which would burn a guess the person never made.

- **AUTH-G1-R2 — the production-configuration tests depended on the developer's
  shell.** No `DEPLOYED` fixture carried an OTP HMAC key, so on a clean machine
  four of them failed and the rest asserted a guard they never reached — the
  boot stopped on the missing OTP key first. The key is fixed by the harness in
  `tests/environment.py`, every deployed fixture names it, and new tests pin
  each of the four ways the key can be worthless as well as the fact that the
  encryption, cookie and Resend guards are each reachable.

- **AUTH-G1-R1 — the acceptance gate was on the wrong control.** The
  registration form disabled its *Company name* input until the acceptance box
  was ticked, leaving the first field of the form dead on arrival, while the
  submit button was not gated at all. Worse, the client sent `termsAccepted:
  true` as a hard-coded constant regardless of what the person had ticked — so
  an account could be recorded as having accepted documents nobody agreed to,
  which is worse than recording nothing. The gate now sits on the actions that
  record acceptance, and the value sent is the value given.

  Found by running the whole browser suite rather than the milestone's own
  tests: 200 of 351 failed on the disabled field, and every one of those was
  invisible to the 36 tests written for the milestone.

- **AUTH-G1-R1 — the Google button stacked duplicates on re-render.** Its effect
  depended on callbacks passed as inline arrows, so any parent state change
  re-ran it, fetched a second nonce and appended a second Google button to the
  same container. The callbacks and the acceptance flag are now read through
  refs, and the container is cleared before Google draws into it.

- **AUTH-G1-R1 — the CSP blocked Google's own stylesheet.** `style-src` omitted
  `accounts.google.com`, so `gsi/style` was refused and the real button would
  have rendered unstyled in production.

- **AUTH-G1-R1 — the browser suite was contacting `accounts.google.com`.**
  Because `/login` and `/register` mount the Google button, every test that
  signed in through the UI made a live request to Google and got a 403 back
  from the synthetic test client id. Sign-in helpers now abort that request; the
  specs that are *about* the button stub it instead.

- **AUTH-G1-R2 — the Google credential checks were asserted, not exercised.**
  The suite mocked the library call, so the signature, audience and expiry
  checks never ran. Thirteen controls now sign real RS256 tokens with a
  throwaway in-process key against a fake certificate set, and prove refusal of
  `alg=none`, a forged signature, a tampered payload, HS256 algorithm confusion
  using the public certificate as the secret, an unknown `kid`, a wrong
  audience, an expired token, a wrong issuer, an unverified email, and a
  missing, wrong or declined nonce. No request reaches Google.

- **AUTH-G1-R1 — several security controls were advisory rather than enforced.**
  The Google nonce could be omitted to skip replay protection entirely; the OTP
  cooldown and attempt counter were read-then-write and admitted a burst; the
  credential verification transport had no timeout, so a slow Google endpoint
  accumulated one stuck worker thread per sign-in; linking and unlinking Google
  needed no password. Each is now enforced — mandatory nonce, atomic Redis
  operations, a bounded transport with a cached certificate set, and a password
  step-up.

- **EBAY-C0.1 — every legitimate eBay retry was being rejected with 409.** Found
  in production logs, not in review: **7 logical notifications** had been
  redelivered and **every one of the 20 redeliveries was refused**. Not one clean
  idempotent acknowledgement had ever been issued.

  **Root cause (confirmed).** Idempotency compared `sha256(raw_body)`. eBay's
  body carries two fields it documents as belonging to the *delivery attempt*
  rather than the event:

  * `notification.publishDate` — *"A timestamp indicating when the current
    notification was sent."*
  * `notification.publishAttemptCount` — *"An integer indicating how many times
    the notification has been sent to this specific callback URL."*

  Both change on every resend, so a retry's bytes can never match the stored
  digest. The endpoint read that as "this id already exists with different
  content" and refused. eBay *"will resend … until it is acknowledged"*; after 24
  hours of unacknowledged notifications the callback URL is marked down, and the
  developer is marked non-compliant 30 days later.

  **No deletion instruction was lost.** All 7 ledger rows are `completed` with
  `erased_record_count = 0` and `outcome_code = no_matching_data` — the correct
  verified zero-match result. `receipt_count = 1` on all 7 is the proof that no
  retry was ever counted.

  **The fix.** `notificationId` remains the logical idempotency authority, as
  eBay documents it. The stored digest now covers the notification's *identity*:
  topic, schema version, `notificationId` and `eventDate`.

  **It covers no personal data, deliberately.** `username`, `userId` and
  `eiasToken` are excluded. The ledger row is a permanent compliance receipt that
  is never erased, so a digest over the subject would leave a way to confirm,
  forever, that a named person's account was deleted — and an unkeyed hash of a
  low-entropy username is a weak oracle at that. There is no keyed,
  domain-separated hashing authority in this codebase, and an urgent compliance
  hotfix is the wrong place to design one. Every covered field is already a
  plaintext column on the same row, so the digest adds no retention the ledger
  did not already have.

  The cost is stated rather than hidden: a notification id reused for a
  *different person* with a byte-identical `eventDate` is no longer detected.
  `test_a_different_subject_under_one_id_is_not_detected` asserts it so nobody
  finds out by accident.

  **Serialisation is injective.** Fixed field order alone is not enough —
  separator-joined concatenation collides, and any separator can appear inside an
  upstream-controlled value. Fields are presence-tagged and length-prefixed, with
  a domain separator mixed in, and collision control tests cover
  boundary-shifting, separator impersonation, absent-versus-empty and the domain
  marker.

  **True collisions are still refused.** Differing immutable event content under
  one id still returns 409, because neither alternative is safe: acknowledging
  would discard a real deletion instruction eBay will never resend, and
  re-erasing would repeat destructive work under a settled identity.

  **Existing production rows keep working, with no weaker path.** A legacy row's
  raw-body digest cannot match a retry, so the row is validated field by field
  against its own stored `topic`, `schema_version` and `event_date` columns —
  the same fields the digest covers. A row that validates is acknowledged and its
  digest upgraded once under the row lock; a row that does not is refused exactly
  as a comparable row would be.

  **No migration.** The `v2:` tag fits inside the existing `VARCHAR(64)`. Alembic
  head remains `0029`.

  **Unchanged, deliberately:** the signature is still verified over the exact raw
  bytes before anything is parsed, so an unverified delivery cannot reach the
  ledger; erasure still runs at most once per notification; the ledger still
  stores no payload and no eBay identifier; and no log line gained one.

  `EbayComplianceService.process` no longer takes `raw_body` — it was used for
  nothing but the digest, and keeping it would imply the bytes still decide what
  counts as the same notification.

### Changed

- **The redelivery path now takes a row lock.** Two retries arriving together
  would both read `receipt_count = 1` and both write `2` — a lost update that
  made the repeat accounting quietly wrong, which matters because that count is
  the only evidence of how hard eBay had to try. It also serialises the legacy
  digest upgrade, so exactly one deliverer performs it.

- **EBAY-C0 tests that built a collision from a username change were rewritten.**
  They predate the privacy narrowing and would otherwise have asserted that a
  subject-only difference is a collision, which is no longer true by design.
  They now differ in event content, which is what they were always about. One
  ledger test that pinned the digest to `sha256(raw_body)` was rewritten the same
  way, with an added assertion that the digest is *not* the raw-body hash so the
  defect cannot return quietly.

### Known follow-up

- **Why eBay retried these 7 at all is not established.** All 7 first deliveries
  returned **204**. Six were slow (2.2 s–4.9 s) but one answered in **23.75 ms**,
  which contradicts a simple latency explanation, and the request log does not
  carry the notification id so timestamp matching is ambiguous where deliveries
  cluster. Recorded as an open question, not a diagnosis.

- **Endpoint response latency deserves its own investigation** — 5 responses in a
  single log exceeded 3 s. Out of scope for C0.1, and after this fix a slow first
  response is harmless: the retry returns 204 straight from the ledger.

### Added

- **EBAY-C1 — eBay seller OAuth connection, and a functional eBay card.** The
  integrations page previously showed eBay as a greyed-out "Coming soon"
  placeholder. It now carries a working card that connects, reports, reconnects
  and disconnects a real eBay seller account.

  Four endpoints: `GET /ebay/status` (any authenticated role),
  `POST /ebay/connect` and `DELETE /ebay/disconnect` (admin or owner), and a
  public `GET /ebay/callback` that eBay redirects a browser to. Migration `0030`
  adds `ebay_connections`.

  The decisions that shaped it, each recorded with its trade-off in
  [`docs/ebay/EBAY_C1_SELLER_CONNECTION.md`](docs/ebay/EBAY_C1_SELLER_CONNECTION.md):

  * **The consent URL is pinned character for character** against eBay's
    published documentation and OpenAPI specifications, because a wrong host,
    `redirect_uri` or scope fails on eBay's own page where there is no log to
    read. `auth.ebay.com` is not `api.ebay.com`; the Identity API is on
    `apiz.ebay.com`; `redirect_uri` carries the opaque **RuName**, not a URL.
  * **Five scopes, and what is absent matters as much.** No `sell.finances`,
    `sell.payment.dispute`, `sell.marketing` or `sell.advertising`, and only the
    minimal `commerce.identity.readonly` — which returns the account id without
    the person, so no extra personal data has to be declared and erased.
  * **The identity is eBay's immutable `userId`, never the mutable username.**
    A seller who renames is the same seller; keyed on the name, a reconnect
    would orphan the old row and the deletion contract would no longer find what
    it must erase.
  * **One seller per workspace, enforced by a global unique constraint** rather
    than a lookup. A check-then-insert races, and a cross-tenant lookup would be
    an existence oracle — a caller could learn whether a given eBay seller uses
    DropPilot by watching which error came back. The conflict names no workspace.
  * **State is consumed before the authorization code is spent**, is keyed in
    Redis only by its SHA-256, and binds the callback to the workspace that
    started the flow — not to whichever browser arrives.
  * **Refresh is serialised by `SELECT … FOR UPDATE`.** Proved against real
    contention: the first caller is held inside eBay's token call after taking
    the lock, and the test waits for PostgreSQL's own `pg_blocking_pids()` to
    report the second caller blocked. A companion test removes the lock and
    requires the duplicate refresh to reappear, so a green result is evidence
    about the lock rather than about the scheduler.
  * **Revocation is permanent, and is not confused with an outage.** A 4xx
    `invalid_grant` marks the connection `reconnect_required` and drops the dead
    ciphertext; a 5xx does not, because asking every merchant to re-authorise
    over an eBay outage is the worse failure.

  Tokens are encrypted at rest with the platform Fernet keys, and no response
  schema anywhere has a field capable of holding a token, a ciphertext, or the
  immutable `ebayUserId`.

  96 backend tests and 24 Playwright tests (chromium and mobile-chrome) cover
  it, against real PostgreSQL, real Redis, real encryption and the real
  migrations; only eBay's network is faked, and an unexpected outbound URL
  raises rather than escaping.

  **`SECURITY_ENCRYPTION_KEYS` is now a hard requirement for eBay.** `connect`
  refuses to begin a consent flow without it, deliberately — discovering the
  platform cannot store a token after a seller has granted consent wastes their
  time and leaves a live credential with nowhere safe to go.

  Not included, and stated plainly: no listing, inventory, pricing, order or
  fulfilment work; no background token refresh; no provider-side revocation on
  disconnect (eBay documents no endpoint an application can call for this grant,
  so the local credentials are destroyed and the merchant is told to revoke at
  eBay); and **no live eBay round trip** — no seller has granted consent and no
  real eBay token has ever been exchanged.

### Fixed

- **eBay OAuth state could never be consumed on the deployed Redis.** Found by
  running the code against the Redis this platform actually deploys on, not by
  review.

  Consuming single-use CSRF state atomically is what `GETDEL` is for, and it was
  the obvious choice. It was added in **Redis 6.2**; the Windows deployments run
  the 3.0.504 build, where the command does not exist. The resulting `unknown
  command` error surfaced as `EbayOAuthStateError` — *indistinguishable from a
  genuine CSRF rejection*. Every eBay consent would have failed with "that
  authorization request is no longer valid", and the logs would have agreed.

  Replaced with a `MULTI`/`EXEC` transaction containing `GET` then `DEL`, which
  gives exactly the same single-use guarantee (Redis runs a queued block with no
  other client's command interleaved) on every server version from 1.2 onward,
  in one round trip.

  The underlying divergence remains and is recorded in the eBay roadmap: local
  Redis is 3.0.504 while `docker-compose.yml` and CI both run `redis:7-alpine`.
  Until it is closed, no new code should assume a command newer than Redis 3.0.

### Changed

- **Three EBAY-C0 guard tests now assert the EBAY-C1 truth.** They failed when
  `ebay_connections` was added, which is exactly what they were built to do:
  storage with no registered eraser is a release blocker. Rewritten to assert
  the property that survives the milestone — every declaration has an owner,
  every owner has a declaration, and no undeclared model holds an eBay
  identifier — rather than "nothing is stored", which was the right assertion
  while zero was the right number and a useless one afterwards.

  Two migration assertions in the C0 suite were also corrected: one pinned the
  Alembic head to the literal `0029` (the invariant worth protecting is that the
  history never *forks*, not that a particular revision is newest), and the
  downgrade/re-upgrade cycle now restores the database to head so it cannot
  leave later tests in the session running against a schema missing `0030`.

- **`ruff`'s ASYNC110 and a shared lock helper.** `backend/tests/integration/live_locks.py`
  now holds `pg_blocking_pids()`-based contention helpers extracted from the
  GQL-2 harness, which re-exports them so existing imports keep working. Two
  copies of a concurrency primitive is how one of them quietly stops matching
  the other.

- **The Playwright CI job configures synthetic eBay application identifiers**, so
  the eBay card's coverage runs there instead of skipping. Nothing in those tests
  contacts eBay: `connect` only builds a consent URL, and the redirect is aborted
  by the test.

### Security

- **EBAY-C0 proxy-boundary hardening — the application resolver is now the only
  proxy-trust authority.** The EBAY-C0 acceptance fix made
  `app/core/client_ip.py` the single place that decides whether a forwarding
  header may be believed, and its unit tests proved it correct. Running the
  server the way this repository actually runs it showed the guarantee stopped
  one layer too high.

  Uvicorn enables `ProxyHeadersMiddleware` by default with `forwarded_allow_ips`
  defaulting to `127.0.0.1`, so it rewrites `scope["client"]` from
  `X-Forwarded-For` before any application middleware runs — and loopback is
  precisely where cloudflared connects from. The resolver was handed a "socket
  peer" the caller had chosen.

  Measured against the repository's real launch command: **700 requests rotating
  a forged `X-Forwarded-For` produced zero 429 responses and 650 separate
  rate-limit buckets**, where 700 requests from one address produced 53. Not a
  degraded quota — no quota. Every ASGI-level test passed throughout, which is
  the point: the defect was never in the application.

  Every launch surface now passes `--no-proxy-headers` — `run-backend.ps1`,
  `docker/backend.Dockerfile`, `docker-compose.yml`, the CI end-to-end job and
  the documented developer command. `--forwarded-allow-ips` was rejected as a
  substitute: it leaves uvicorn parsing the chain in parallel with the
  application, and two authorities that can disagree is worse than either alone.

  `tests/integration/test_proxy_boundary_real_server.py` launches real uvicorn
  processes and asserts on what a caller can observe — whether the quota can be
  escaped — with blank trusted proxies, with loopback explicitly trusted, and
  from a non-loopback peer. One test deliberately starts an *unhardened* server
  and requires the bypass to reappear, so the flag is provably the cause rather
  than a coincidence. `tests/unit/test_server_launch_surfaces.py` holds the
  launch-surface manifest and fails if any command loses the flag, reintroduces
  uvicorn-side trust, or if a new uvicorn invocation appears anywhere the
  manifest does not cover.

  No application code changed: no migration, no schema change, no API contract
  change, no frontend change. The committed `SECURITY_TRUSTED_PROXIES` default
  stays blank; the Cloudflare Tunnel value is a deployment step, documented in
  `PRODUCTION_SECURITY.md` §7 along with an operator verification loop and
  rollback. **EBAY-C0 must not be activated in production until the deployment
  runs the hardened launch configuration.**

### Fixed

- **EBAY-C0 acceptance fix — two blockers and five medium findings.** An
  independent review returned `EBAY-C0 blocked`. Every finding was real and each
  was reproduced before being fixed.

  **The request body was buffered before it was measured.** `await
  request.body()` reads everything, and the 64 KiB check ran afterwards, so an
  unauthenticated caller decided how much memory the process allocated. Measured
  rather than argued: a test offered 16 MiB in 8 KiB chunks and counted what the
  application asked for — 16,777,216 bytes consumed before rejecting a
  65,536-byte limit. `app/core/request_body.read_bounded_body` now streams and
  stops *asking* at the ceiling. `Content-Length` is a cheap short-circuit for an
  honest oversize and nothing more; a forged small value changes nothing. A
  disconnect is a 400, not a 500. The bytes are returned exactly as received and
  JSON is still parsed only after the signature verifies.

  **A forwarding header was believed from any caller.** Five places did
  `X-Forwarded-For.split(",")[0]`, so a direct attacker could rotate the header
  and be a new client every request — defeating the general quota, the eBay
  compliance budget and the login throttle alike. `app/core/client_ip.py` is now
  the single answer: a forwarding header counts only when the socket peer is
  inside a configured `SECURITY_TRUSTED_PROXIES` CIDR, and the chain is walked
  from the **right** so the caller's own prefix is never believed.
  `CF-Connecting-IP` is honoured only from a trusted peer; `CF-Ray` grants
  nothing. Every resolved value is a parsed IP address, so nothing
  attacker-shaped can reach a Redis key. **Nothing is trusted by default** — a
  deployment that forgets the setting sees clients collapse to the proxy's
  address, which is visible, rather than silently trusting the internet.

  Also: the public-key cache key now carries the eBay environment and a schema
  version, so sandbox and production cannot share a slot; a repeat
  `notificationId` carrying a *different* payload is refused with a typed
  `ebay_notification_conflict` (409) instead of being counted as a duplicate,
  leaving the original digest and outcome authoritative and never re-running
  deletion; the verifier requires eBay's P-256 curve rather than any EC key; and
  the deletion guard is now an explicit `EBAY_STORAGE_DECLARATIONS` registration
  contract, because a regex over column names cannot see an identifier inside a
  JSONB bag or an encrypted column — a limitation now stated rather than implied
  away.

  The signature authority is pinned: `eBay/event-notification-nodejs-sdk`
  commit `feaf3378…` (tag 1.0.3), fixture `test/test.json` blob `092dabc7…`,
  retrieved 23 August 2026. The published signature was reproduced
  independently in Node v24.18.0 and in this repository's Python verifier, both
  over the same 434 bytes with the same SHA-256 — so the byte-level contract is
  confirmed, not assumed. SHA-1 stays because eBay's key metadata returns it;
  that is provider-mandated compatibility and is documented as such.

  No migration was required. Alembic remains a single head at `0029`. No live
  eBay request was made; no frontend code changed, so frontend gates were not
  run and are not claimed.

### Added

- **EBAY-C0 — eBay credentials and Marketplace Account Deletion compliance.**
  eBay requires every Developers Program application to subscribe to Marketplace
  Account Deletion/Closure notifications — or formally opt out — before its
  first production API call, and the keyset stays inactive until the endpoint
  validates. This is that endpoint, and nothing else: no OAuth, no listings, no
  inventory, no orders.

  `GET`/`POST /api/v1/integrations/ebay/marketplace-account-deletion`, public by
  protocol design because eBay cannot present a JWT. What replaces
  authentication is not nothing: the GET proves endpoint ownership through a
  shared secret and returns a digest rather than any stored data, and the POST
  proves origin cryptographically over the exact bytes received before a single
  field is read. Plus a 64 KiB body ceiling, content-type validation, a
  dedicated rate-limit budget and logging that never touches the payload.

  **The signature format was proven, not recalled.** eBay documents no prose
  specification for `X-EBAY-SIGNATURE`; its guide points at the Event
  Notification SDKs. Those were read, the format resolved to ECDSA-P-256 with
  SHA-1 over the raw body with a DER signature and a PEM key, and it was
  verified in Python against eBay's own published test vector *before* the
  verifier was written. That vector is pinned in the test suite, because a
  verifier can pass against fixtures it generated itself and still reject every
  real notification.

  The challenge hashes `challengeCode + verificationToken + endpoint` in exactly
  that order, using the **configured** endpoint string — never a `Host` or
  `X-Forwarded-Host` header, which behind a proxy are attacker-influenced. The
  endpoint is validated but never normalised: adding or removing a trailing
  slash would silently change the hash, which is the exact failure the setting
  exists to prevent.

  Public keys come from eBay's fixed Notification API host with a
  client-credentials token, cached in Redis for the hour eBay recommends. SSRF
  and traversal are prevented by construction rather than by filtering — the
  host is a constant and the only variable in the path is a parsed `uuid.UUID`,
  a type that cannot hold a slash, a scheme or a host.

  Nothing is acknowledged until the signature is verified, the schema validated,
  a durable receipt written and the erasure completed — all in one transaction.
  eBay retries for 24 hours and never resends an acknowledged notification, so a
  retryable 5xx costs a delay while a premature 2xx costs a person's deletion
  request. Invalid signature or rejected schema returns **412**, matching what
  eBay's own SDKs return.

  Migration **0029** adds one table, `ebay_compliance_notifications`, holding
  **no personal data at all** — eBay's payload carries `username`, `userId` and
  `eiasToken`, and none has a column. A SHA-256 payload digest supplies identity
  without content, so the ledger cannot leak what it records and cannot itself
  become something that must be erased next time. `notification_id` is UNIQUE,
  and that constraint *is* the idempotency mechanism: two concurrent deliveries
  race to insert and the database arbitrates, where an application-level
  check-then-insert would let both through.

  A model-layer audit found **no table storing eBay user data**, so the correct
  behaviour today is a *verified zero-match deletion* — authenticated, recorded,
  completed, nothing erased. That is checked by searching the models on every
  test run rather than asserted in prose, and it doubles as the release guard:
  the moment EBAY-C1 adds an eBay-identifying column without registering a
  deletion owner, the test fails. eBay data persistence cannot be called
  production-ready while it is red.

  No Celery task was introduced. The erasure and the ledger row commit together,
  so there is no window in which the ledger claims completion for work that
  rolled back; handing it to a broker would add that window rather than close
  one.

  **No live eBay request was made anywhere.** Every provider call is fixtures
  and `httpx.MockTransport`, labelled as mocked in each test module. The
  production verification token does not exist yet and is generated after
  deployment; a previously exposed token is treated as permanently compromised
  and appears nowhere in this repository.

### Fixed

- **GQL-2 F-01b — a webhook confirmation can no longer go stale.** The previous
  fix made a store that had *never* reconciled visibly degraded. It did not
  cover the opposite case. `webhooks_registered_at` was only ever written, never
  cleared, and `webhook_health()` read any non-null value as healthy — so one
  successful run vouched for every failure after it. A later unhealthy
  reconciliation, a provider outage, or a reconnect that stored **brand-new
  OAuth credentials** all left the previous timestamp standing, and
  `complete_connection` rotated the access token without touching it at all. The
  card showed a healthy state for a store whose subscriptions were missing, and
  because Retry appears only while degraded, the stale timestamp also removed
  the way out.

  A non-null `webhooks_registered_at` now means *the most recent completed
  reconciliation for the current credentials was healthy* — not "some
  reconciliation once succeeded". `register_webhooks` commits the column to NULL
  in its own short transaction **before** it makes any Shopify request, so a
  provider exception, a lock timeout or a worker dying mid-reconciliation leaves
  the store degraded instead of restoring a confirmation the rollback would have
  brought back. `complete_connection` clears it in the same UPDATE that stores
  refreshed credentials, so an old confirmation can never authenticate a token
  it has never seen.

  The invalidation runs on the caller's session rather than an independent one:
  the OAuth path reaches `register_webhooks` with `complete_connection`'s UPDATE
  of that very row uncommitted, and a second connection would block on it until
  the request timed out — the deadlock
  `ProductImportService._persist_failure_durably` already documents. It is a
  deliberate, narrow departure from "handlers never commit", because here a
  trace surviving the failure is the entire point.

  Concurrency is unchanged: the same bounded row lock still serialises
  reconciliation per store, the control test proving an unlocked race duplicates
  still passes, and different stores stay independent. Invalidation takes the
  same bounded wait, so a caller arriving while another reconciliation holds the
  row is refused *before* it can clear a confirmation it is not going to
  replace. Reads never mutate — viewing the status page does not reconcile.

  The UI stops claiming more than it can know. Nothing observes a subscription
  Shopify deletes outside DropPilot, so the badge reads **Webhooks last
  confirmed** with its date rather than *Webhooks active*, and the degraded
  warning says confirmation failed *on the most recent attempt*.

  Red-before: the new suite was written first and run against `8abd9cc` —
  12 failed, 9 passed. Green after: 21 passed. Full backend suite 1741 passed
  (baseline 1720, +21); 82 Playwright tests across chromium and mobile-chrome,
  with the Shopify payloads mocked and labelled as such. No migration; Alembic
  remains a single head at `0028`.

- **GQL-2 acceptance fix — a connected Shopify store can no longer hide broken
  webhooks.** An independent review returned `GQL-2 requires fixes` on one
  blocking finding, and it was a real one: OAuth completion ran webhook
  registration inside a best-effort `try/except` and threw the `ReconcileReport`
  away, so the merchant was redirected with `shopify=connected` whatever had
  happened. `webhooks_registered_at` stayed null, the card rendered *Connected*,
  and the card's only recovery control was gated on the status *not* being
  connected — so the store that needed it was the one that could not see it. The
  amber note said "reconnect if sync stalls" next to a hidden reconnect button.
  The only way out was disconnecting a perfectly valid OAuth connection.

  Keeping the connection was right; presenting it as healthy was not. A store is
  now disconnected, connected-and-webhook-healthy, or connected-but-degraded,
  all **derived from the existing persisted authority** — `webhook_health()`
  reads `status` and `webhooks_registered_at` and there is no new column, because
  a second health field could only ever disagree with the timestamp it duplicates.
  The API returns `webhookHealth` so no client has to invent its own rule for
  what a null timestamp means; the rule the frontend had invented was "assume
  connected".

  New admin-only endpoint
  `POST /api/v1/integrations/shopify/stores/{store_id}/webhooks/reconcile`. Not
  `/shopify/webhooks/reconcile`, which shares a prefix with the unauthenticated
  HMAC webhook receiver and would be matched as a topic named "reconcile" the
  moment declaration order changed. It calls the *same* reconciler OAuth uses —
  no second implementation and no queue — lists every page before creating,
  never replays a mutation whose outcome is unknown, stamps
  `webhooks_registered_at` only on a fully healthy report, and returns 200 with
  an explicit degraded verdict rather than a fake success. A viewer gets 403; a
  foreign store id is indistinguishable from an unknown one.

  The OAuth callback now consumes the report: `shopify=connected` only when it is
  healthy, otherwise `shopify=connected_webhooks_degraded` — a distinct value
  rather than a flag, so an older frontend cannot fall back to rendering full
  success. No secret or raw error text goes in the query string.

  The Shopify card shows the degraded state, explains that product, inventory and
  order updates may be missed, and offers **Retry webhook setup** while the store
  stays connected. The outcome lands in a polite live region that takes focus
  when the retry settles; viewers see the warning and a read-only explanation but
  no control; nothing fires on render and the mutation does not self-retry.

  Lock contention (finding F-02) is now bounded: `lock_for_update` takes a
  `lock_timeout` of 10s, expressed as PostgreSQL's own setting rather than an
  application timer, scoped with `SET LOCAL` and reset immediately after the lock
  statement so it governs acquiring that row and nothing else. Expiry becomes
  `shopify_webhook_reconcile_busy` (409) instead of an unhandled 500. The
  critical section is unchanged, different stores still do not block each other,
  and `shopify_webhook_reconcile_finished` now logs `lock_wait_ms` and
  `duration_ms` on every outcome.

  Documentation corrections: the three mandatory privacy webhooks are assigned to
  a named milestone, **`SHOPIFY-COMPLIANCE-1`**, marked as blocking App Store
  submission; the claim that an automated webhook "recovery path" existed is
  withdrawn — recovery is deterministic but **manual**, and a scheduled sweep is
  recorded as `SHOPIFY-OPS-1`, unowned; and the inventory test count now
  distinguishes the 11 newly added drift tests from the file's 25 total.

  Red-before: the new backend acceptance suite was written first and run against
  the accepted candidate — 17 failed, 3 passed. Green after: 20 passed. Frontend
  coverage is 12 Playwright tests across two projects with the Shopify payloads
  **mocked**, labelled as such in the file; the same flow runs against real
  PostgreSQL in the backend suite.

### Added

- **GQL-2 — shop currency authority and webhook reconciliation on GraphQL.**
  The first three inventory rows migrate onto the GQL-1 foundation: `GQL-000`
  (`shop.currencyCode`), `REST-008` (webhook list) and `REST-009` (webhook
  create). Same topics, same delivery URIs, same shop-scoped model, same
  `Store.currency` semantics — only the transport changed. Ten of twelve
  versioned Admin REST calls remain.

  A new operation layer (`graphql_operations.py`) holds the three static
  documents; `webhook_reconciliation.py` holds the compare-and-create logic.
  Both were written against the official 2026-07 reference, which corrected
  GQL-1's own proposal: `WebhookSubscription.uri` is the current endpoint field,
  and `callbackUrl` and the `endpoint` union are deprecated. A document written
  from the unverified proposal would have compared endpoints by `__typename` and
  never matched anything.

  **Concurrent registration can no longer duplicate.** OAuth completion,
  reconnect and recovery all call `register_webhooks`; two of them overlapping
  would each list an empty shop, each decide a topic was missing, and each
  create it, leaving the merchant receiving every event twice. Reconciliation is
  now serialised per store with `SELECT … FOR UPDATE` on the `ShopifyConnection`
  row — the same coordination mechanism the bulk-pricing work uses, not a second
  locking architecture, and not an in-process lock that would protect neither of
  two workers. Proven by two reconcilers on two real PostgreSQL connections with
  a `pg_blocking_pids()` rendezvous, alongside a control test showing the same
  race without the lock does duplicate.

  The create mutation is **never retried automatically** — the old REST path
  inherited a generic retry, so a lost response could create a second
  subscription. An unknown outcome is reported as `unknown` and resolved by
  re-listing on the next run, and `webhooks_registered_at` is stamped only when
  every desired subscription is confirmed present with no warnings.

  Shop currency still fails closed with no USD fallback, and a failed refresh
  still retains a previously trusted currency rather than clearing it.

  **No migration was required** — no webhook identifier is persisted anywhere,
  so Shopify's list stays the only authority and `alembic heads` is unchanged at
  `0028`.

  Deliberately not done, and recorded rather than assumed safe: `shopify.app.toml`
  subscriptions are **not** introduced, because `webhookSubscriptions` returns
  only shop-scoped subscriptions and running both modes for one topic would
  duplicate every event with no way for this codebase to detect it; that move
  needs `webhookSubscriptionDelete`, which is GQL-6's. Nothing is deleted in this
  phase — duplicates and mismatches are reported as warnings.

  The inventory drift test now checks the document against the **code**, not just
  against its sibling document: a row may only claim `migrated`/`removed` if the
  call site is genuinely gone, and a cited test file must actually exist. That
  guard immediately caught a pre-existing defect — both `OAUTH-*` rows cited a
  test file that has never existed in this repository.

  Known gap, unowned by any GQL phase: the three mandatory privacy webhooks
  (`customers/data_request`, `customers/redact`, `shop/redact`) have no handler
  anywhere in the backend. They are configured outside this API, so they are not
  a REST-migration row, but they are an App Store submission blocker. See
  `docs/shopify-graphql/GQL2_SHOP_WEBHOOKS.md`.

  No live Shopify request was made; live verification is recorded as not run.

- **GQL-1 — Shopify Admin GraphQL client foundation and complete REST
  inventory.** The first Shopify App Store launch-readiness phase. Shopify
  requires new public apps to use GraphQL exclusively, and this platform makes
  twelve versioned Admin REST calls; every one is a submission blocker.

  `ShopifyGraphQLClient` is the single surface future operations go through:
  pinned to `2026-07` via its own setting (separate from the REST version, since
  the two are on different clocks, and validated so `latest` is refused at
  configuration load rather than at the first request); canonical-domain only,
  normalising nothing, rejecting schemes, ports, paths, IPs and suffix
  lookalikes through the *existing* OAuth domain authority rather than a second
  validator; redirects not followed; the token absent from `repr`, logs and
  exceptions, and absent from the shared connection pool, which carries no
  credentials at all.

  Responses are typed and carry Shopify's request id and the full
  `extensions.cost` block, parsed defensively so malformed telemetry can never
  fail a good response. Top-level `errors` **fail closed** even when `data` is
  partially populated — `allow_partial_data` exists to make the alternative
  explicit and is unused. Mutation `userErrors` are a typed failure through a
  contract that takes the mutation field name from the caller, and a document
  that forgot to select them is refused rather than read as success. Queries
  retry only timeouts, 429, 502/503/504 and `THROTTLED`, with `Retry-After`,
  cost-derived and exponential waits all bounded and jittered; `MAX_COST_EXCEEDED`
  is never retried, and **mutations never retry automatically**.

  Also: a `ShopifyGid` value object with no API that accepts an array index (a
  previous generation derived variant identity from position and repriced the
  wrong variant), and bounded cursor-pagination helpers that refuse a repeated
  cursor or `hasNextPage` with no `endCursor`.

  `docs/shopify-graphql/` gains the inventory in both Markdown and JSON — twins
  guarded by a test so they cannot drift — plus the foundation decision record
  and the six-phase roadmap. Ten of fifteen proposed replacements are marked
  `unverified` on purpose: the exact 2026-07 fields have not been read yet, and
  guessing would be worse than the gap.

  Acceptance fixes on the same phase, each with a recorded red-before result:
  parameterized Shopify GIDs (`gid://shopify/InventoryLevel/123?inventory_item_id=456`)
  were rejected outright because the parser terminated the id at `?`; retry
  eligibility was taken from a caller-declared `OperationType`, so a mutation
  declared as a query got three HTTP attempts at a `productCreate`; and the
  foundation document wrongly called `2026-10` the latest stable release when it
  is the release candidate until 1 October 2026. GIDs now support both documented
  shapes with the complete opaque string as the persistence authority, and the
  document is parsed with `graphql-core` (MIT, no runtime dependencies, declared
  explicitly) so the operation's kind is a fact rather than an assertion — a
  mislabelled mutation now makes zero HTTP requests.

  **No REST call was migrated, no behaviour changed, no migration added, and no
  live Shopify request was made** — every test runs through
  `httpx.MockTransport`.


- **M3A-4B — Draft impact, bulk application UI, and final M3A hardening** —
  **Settings → Global Rules → Preview and Impact**: what the active rules would
  do to existing drafts, with search, filters, per-product and per-variant
  figures, and a confirmation stating selected / ready / held / published /
  expected changes before anything is written. Missing figures read
  "Unavailable", never zero. Selection never enumerates the catalogue: "select
  all matching" sends the *filter*, and the server expands it once into a
  durable snapshot capped at 5000. `202` returns a `pending` run; the screen
  polls to a terminal state and stops, and the application id and open section
  live in the URL so a refresh resumes the same run rather than starting
  another. Results are per item, filterable by outcome, and a partial run shows
  its successes. Published products are marked, unselectable, excluded from
  select-all and refused by the service.

  Rules are now scoped **by name**: `GET /global-rules/targets/{kind}` returns
  labels and ids for products, variants, categories and stores, behind a
  keyboard-operable combobox, with raw identifier entry kept as an explicit
  advanced fallback.

  Hardening: a heartbeat plus `pricing.reconcile_applications` recovers runs
  abandoned by a crashed worker without ever stealing a healthy one; concurrent
  rule-version writes return `409` with a merchant-facing message; preview,
  impact and apply carry named per-tenant quotas through the *same* limiter as
  the global middleware; and `scripts/verify_rule_version_integrity.py` reports
  on the deferred typed-reference constraint without touching data. Migration
  `0027`, additive. **M3A is complete.**

- **M3A-4A — Global Rules management UI** — **Settings → Global Rules**
  (`/settings/global-rules`): pricing rules, shipping rules, application
  behaviour, a live calculator and append-only rule history, on one route.
  Strategy fields appear only when the selected strategy uses them; scope
  controls show only the identifier that scope needs; markup versus gross
  margin is explained with a worked example. **Every live figure comes from
  `POST /global-rules/preview`** — no pricing formula is duplicated in
  TypeScript. Updates and activations carry `expectedUpdatedAt`, and a 409
  raises a banner offering *Reload latest version* or *Keep my changes*; there
  is no autosave and no mutation on page load. Owners and admins manage;
  everyone else reads, previews and reads history, with mutation controls
  absent rather than disabled. Two supporting API additions: the preview now
  returns `priceBeforeRounding`, and rule history is paginated in the standard
  `Page` envelope. The draft impact and bulk-application screen is **M3A-4B**
  and is not included. See
  `docs/dsers-parity/M3A_GLOBAL_PRICING_RULES.md`.

- **M3A — Global pricing and shipping rules** (backend only; UI is M3A-4) —
  one calculation shared by import, a read-only impact preview and a confirmed
  bulk application. Scope precedence
  `variant > product > category > store > global`, versioned rules with an
  append-only history, and a fail-closed engine that returns a
  machine-readable reason instead of a price whenever a supplier figure is
  missing. Confirmed applications run on the **existing Celery queue**: the
  API returns `202` with a `pending` run, the worker claims it atomically,
  processes bounded batches that resume correctly after a retry, and records
  one result row per item including the ones nothing happened to. The broker
  payload is a single application id, and the tenant is read from the row
  rather than the message. Migrations `0023`-`0026`, all additive. See
  `docs/dsers-parity/M3A_GLOBAL_PRICING_RULES.md`.

- **M2B — Rich-text product description** — TipTap 3 editor
  (`rich-text-description-editor.tsx`) replaces the raw-HTML `<textarea>` on
  the draft editor's Description tab. **Storage format unchanged** (sanitized
  HTML in `products.description`), so no migration and no change to the
  Shopify publish path. `DESCRIPTION_MAX_LENGTH = 64_000` is now enforced at
  the API boundary and shown as a live counter. No image insertion or upload
  (that is M2D-A); existing supplier images and tables are preserved. See
  `docs/dsers-parity/M2B_RICH_TEXT_DESCRIPTION.md`.

- **M24A — Shopify selling-currency authority + production FX** — GraphQL
  `shop.currencyCode` sync (Admin API **2026-07**); migration `0020`
  `stores.currency_last_synced_at` (nullable, never backfilled); Open Exchange
  Rates provider; freshness vs Redis retention windows; explicit
  `POST /stores/{id}/currency/refresh`. See `docs/FX_RATE_PROVIDER.md`,
  `docs/SHOPIFY_API_MODERNISATION.md`. **Not** production pricing readiness
  (M24B/M24C remain).

### Fixed

- **A cancellation racing a finishing run could rewrite it as cancelled
  (M3A-H1)** — `cancel()` was a read-then-write: a plain `SELECT`, the
  terminal-state guard evaluated against it, then a flush. Nothing held the row
  in between and there is no version column, so the flush emitted
  `UPDATE … WHERE id = ?` and overwrote whatever had landed meanwhile. A
  cancellation arriving while a worker was in `finalize` read `running`, passed
  the guard, waited on the worker's lock and then rewrote a **completed** run as
  cancelled — the one outcome the guard exists to refuse. Product prices,
  result rows and progress were never affected; the record of what happened was.
  Cancellation now takes the same tenant-scoped `SELECT … FOR UPDATE` the worker
  paths use, so inspecting the status and mutating it are one atomic step: a
  finished run is refused with `409`, an already-cancelled run is returned
  without rewriting `finished_at`, the reason, the counters or any audit row,
  and a `pending`/`running` run is cancelled cooperatively with committed
  batches intact. Cross-tenant identifiers still find nothing and return `404`.
  No schema change: the existing status column plus row locking is the whole
  mechanism.

- **The mid-batch concurrency test could not fail (M3A-H2)** — it slept 0.5 s
  and accepted either reclaim outcome. Replaced with deterministic
  two-connection tests that wait on PostgreSQL's own `pg_blocking_pids()`,
  assert the worker is the blocking backend, and prove the reclaim CAS
  re-evaluates the moved heartbeat and takes nothing — plus a negative control
  proving the helper raises when there is no contention.

- **Recovery of a stuck bulk application never actually resumed it (M3A)** —
  the reconciler cleared `claimed_by_task_id` but left the row `running`, so
  the message it published reached a worker that saw a `running` row owned by
  someone else, reported `already_running` and processed nothing. Every
  recovery logged `requeued` and rescued nothing. Recovery now returns the run
  to `pending` and unowned in one conditional UPDATE, commits, and *then*
  publishes; the new delivery takes it through the ordinary `pending → running`
  claim under its own real Celery task id, and resumes from the durable cursor.
  The reclaim is pinned to the exact status, heartbeat, lease and recovery
  count the sweep observed, so a worker that checked in keeps its run and two
  racing reconcilers reclaim exactly once. A failed publish leaves the run
  `pending` with `enqueued_at` NULL, which the pending sweep repairs.

- **A worker whose run had been taken over kept writing to it (M3A)** — the
  worker claimed once and then looped over batches with nothing re-checking
  ownership, so a reclaimed worker carried on writing prices, `PriceChange`
  rows, result rows, progress and heartbeats into a run that belonged to
  another worker, and could mark that healthy run `failed` from its own error
  handler. A durable lease (`lease_token`, migration `0028`) is now minted on
  every grant of ownership and verified under `SELECT … FOR UPDATE` at the
  start of every batch, in the same transaction as the writes. `finalize`,
  `fail` and the heartbeat are owner-conditional through the same lock; a
  replaced worker reports `superseded` and writes nothing at all. The per-item
  unique constraint was never the safety mechanism here — product writes happen
  before it.

- **Runs abandoned before migration `0027` were unrecoverable (M3A)** —
  `heartbeat_at < cutoff` is NULL, never true, for rows that predate the
  column, so the sweep could not see them and they sat `running` forever.
  `0028` backfills them, and the sweep now treats a NULL heartbeat as stale
  once the row itself is older than `STALE_AFTER` — so a legitimately
  just-started run is still never reclaimed early.

- **One member could exhaust an endpoint quota for a whole workspace (M3A)** —
  the named endpoint limiter keyed on `tenant` alone. It is now
  `ratelimit:{name}:tenant:{tenant_id}:user:{user_id}`, taken from verified
  token claims through a principal the dependency requires, so authentication
  resolves first by construction and no caller-supplied header can choose a
  bucket. Two tenants stay isolated, endpoints keep separate names,
  `Retry-After` is unchanged, there is still one `FixedWindowLimiter`, and the
  documented fail-open-on-Redis-outage policy is preserved and now tested.

- **A `202` could be returned for an application that was then rolled back
  (M3A)** — the queue hand-off ran as a FastAPI background task, on the
  assumption that yield-dependency teardown (and therefore the commit) happened
  first. It does not: Starlette awaits background tasks inside the response
  call, still within the session's scope. The worker looked up a row its own
  request had not written, the `NotFoundError` escaped after the response had
  started, and the whole transaction rolled back. The message is now published
  from an `after_commit` hook, and `reconcile_applications` republishes any
  `pending` run the broker never accepted. Found by running a real worker.
- **Every Celery task shared one database engine across `asyncio.run` loops
  (M3A)** — the engine is a module-level singleton whose pooled connections
  belong to the loop that opened them, so the *second* task in a worker failed
  with `'NoneType' object has no attribute 'send'`. Fixed for the pricing tasks
  by disposing the engine before the loop closes; **the same latent bug remains
  in the other task modules** and is recorded in
  `docs/dsers-parity/M3A_GLOBAL_PRICING_RULES.md` §7.
- **Database constraint text was returned to API clients (all repositories)** —
  `TenantScopedRepository._translate_integrity_error` put the raw driver
  message into `details`, which is serialised into the error envelope, so any
  client able to provoke a duplicate received index names, column names and the
  offending values. The text now goes to the log, and the response carries the
  correlation id that ties the two together.

- **Rule history was an unbounded result set (M3A)** —
  `GET /global-rules/{kind}/{id}/history` returned every version of a rule as a
  bare array. History is append-only, so a long-lived rule's trail grows
  without limit; it is now paginated and returns the same `Page` envelope as
  every other list endpoint in the API.

- **Variant-scoped pricing rules never applied anywhere (M3A)** —
  `PricingRuleRepository.find_candidates` had no `variant_id` clause, unlike
  its shipping twin, so a variant-scoped rule was never in the candidate set
  the resolver chose from. The resolver ranked variant highest and simply
  never saw one, which is why unit tests of the precedence logic passed
  throughout. The narrowest scope in the model was silently dead at import, in
  preview and in bulk apply.
- **A misconfigured pricing rule could fail an entire import (M3A)** —
  `compute_sell_price` raises when a strategy is missing the field it needs,
  and `_apply_global_rules` did not catch it, so the exception rolled back the
  transaction that had just written the supplier snapshot and the merchant got
  no draft at all. Pricing failures now flag the draft
  (`pricing_calculation_failed`) and let the import complete.
- **A rule denominated in another currency priced silently (M3A)** — the M3A
  path did no currency check, so a GBP rule's `min_price`, `max_price`,
  `markup_fixed`, `min_profit` and `fees_fixed` were applied to a USD cost as
  though the numbers were comparable. It now fails closed with
  `fx_rate_unavailable`.

- **Draft description saves no longer write on every autosave (M2B)** —
  `ProductService.update_product` sanitized the description *after* the
  no-op comparison, so a rich-text client's re-serialised markup (`<br />`
  vs `<br>`, a stripped `class`, a rewritten `rel`) read as a change and
  wrote an identical value, moving `updated_at` and spuriously invalidating
  other editors' version tokens every 1.8s. Sanitization now runs first.
- **Opening a draft no longer rewrites it (M2B)** — the rich-text editor
  treated ProseMirror's own normalisation transactions as merchant edits,
  marking the form dirty on load and autosaving a draft nobody had touched.
  Edits are now recognised from actual input (DOM events and toolbar
  commands), not from any document change.
- **Supplier images and tables are no longer destroyed by the description
  editor (M2B)** — the editor's initial schema had no image or table node,
  so ProseMirror discarded them on parse and the autosave above persisted
  the loss. The schema now covers everything the sanitizer allows; note
  that "can exist in the document" and "can be created from the toolbar"
  are deliberately different sets.

- **Pricing currency integrity** — Prohibit 1:1 cross-currency conversion;
  block calculated profit/proposed prices when FX is required and unavailable;
  Shopify selling currency requires a verified sync timestamp — no tenant /
  supplier / USD fallback for unsynced stores. See
  `docs/PRICING_CURRENCY_DEFECT_AUDIT.md`.
- **Pricing currency integrity, closing the gap the above left open** —
  `ProductVariant.sell_price` had no record of which currency it was
  actually computed in (`variant.currency` is the *supplier's* currency, a
  different field); a price set for a GBP store would have silently reached
  Shopify as an unlabelled number after a switch to a USD store. Added
  `sell_price_currency` (migration `0021`), stamped whenever pricing writes
  a price; a mismatch — or a price predating this column — surfaces as
  `needsRecalculation` rather than being trusted; Shopify publish now blocks
  outright when an enabled variant's price currency doesn't match the
  store's verified currency, never sending a mislabelled amount. See M23/M26
  in `docs/TECHNICAL_DEBT.md`.
- **M24B — AliExpress `target_currency` derived from destination, not
  hardcoded** — every refresh/sync path (`POST /products/{id}/sync`,
  `POST /drafts/{id}/refresh`, the scheduled resync task) previously omitted
  `currency` and silently defaulted to `"USD"` regardless of the product's
  real destination — a GB-destined refresh asked AliExpress for USD pricing.
  `ImportDestinationService.resolve_currency()` now derives it (explicit
  request → verified store currency → destination's mapped market currency,
  GB→GBP/US→USD → tenant default), mirroring the existing ship-to-country
  resolver. Also fixed a live-reproduced mislabeling: `Product.currency` was
  set from AliExpress's unlocalized native currency
  (`ae_item_base_info_dto.currency_code`, always the seller's own currency
  regardless of what was requested) while `cost_price_min`/`cost_price_max`
  are computed from the correctly-localized SKU prices — now derived from
  the SKUs themselves; the native currency is preserved separately as
  `supplier_native_currency` (migration `0022`, alongside `import_currency`
  recording what was actually requested). Live-traced against a real
  product; shipping/tax/landed-cost remain out of scope — see
  `docs/ALIEXPRESS_LOCALIZED_PRICING.md` and M27 in `docs/TECHNICAL_DEBT.md`.

### Changed

- **Money / FX domain** — `Money` value object, `FxService` as sole FX
  entrypoint (`unavailable` / `stub` / `openexchangerates`), Decimal-safe JSON
  rate parsing. See `docs/MONEY_AND_CURRENCY_ARCHITECTURE.md`.

### Changed

- **Premium draft editor header** — Three-layer sticky chrome with thumbnail,
  breadcrumb, supplier-sync/readiness/SEO badges, Publish-primary action
  hierarchy, More menu for tertiary tools, Draft Preview sheet (not a tab
  switch), tablet wrap + mobile bottom action bar. Unsupported Duplicate /
  Archive / Delete actions are hidden until APIs exist. See
  `docs/PREMIUM_PRODUCT_EDITOR_UI.md`.

### Added

- **AliExpress import destinations** — Import as Draft records `ship_to_country`
  on each import job and `import_ship_to_country` / `import_ship_to_checked_at`
  on the product (migration `0019`). Destination resolution prefers the dialog
  selection, then store `settings.countryCode`, then optional
  `DEFAULT_SHIP_TO_COUNTRY`, then the last successful import — never a silent
  US default. Publish warns/blocks when the store market differs from the
  import destination.
- **Premium Product Editor** — Sticky header/inspector, autosave, SEO workspace
  + advisory score (no meta-keywords export), editable shipping/customs,
  target-margin pricing, post-publish View in Store / Manage in Shopify when
  URLs are verified. Migration `0018` persists listing handle/admin/storefront
  fields. See `docs/PREMIUM_PRODUCT_EDITOR_UI.md`.
- **Draft Product Editor Stage 5** — Pricing workspace (`GET/POST …/pricing`)
  with Decimal profit/margin/break-even; inventory freshness + supplier stock
  labels; shipping package/logistics columns (migration `0017`). Missing
  freight is never coerced to zero.
- **Draft Product Editor Stage 4** — Media reorder/featured/alt text/add-by-URL/
  remove; variant merchant SKU, sell/compare-at prices, enable/disable.
  Migration `0016`. Supplier sync preserves merchant image order/alt and
  merchant variant pricing fields.
- **Draft Product Editor (Stages 1–3)** — Clickable Drafts rows open
  `/drafts/{id}` with Overview/Description/SEO editing, Save Draft
  (`PATCH /api/v1/drafts/{id}`), Refresh Supplier Data, readiness sidebar, and
  Publish to Store panel. Reuses Product Editor write/sanitize APIs. See
  [DRAFT_PRODUCT_EDITOR_PLAN.md](docs/DRAFT_PRODUCT_EDITOR_PLAN.md).
  Merged to `develop` (`da83d52`).
- **Product Workspace V2 Stage 0** — Drafts vs Products are publication
  projections over the same `Product` aggregate. `GET /api/v1/drafts` lists
  products with no synced `StoreListing`; `GET /api/v1/products` lists only
  successfully published products; `GET /api/v1/products/workspace-counts`
  feeds sidebar badges. Frontend routes `/drafts`, `/products`, and
  `/imports/history`; supplier ingestion is labelled **Import as Draft**.
  Migration `0015` indexes `(tenant_id, product_id, status)` on
  `store_listings`. See [PRODUCT_WORKSPACE_V2_PLAN.md](docs/PRODUCT_WORKSPACE_V2_PLAN.md).

### Fixed

- **AliExpress ship-to errors** — `rsp_code 482 SHIP_TO_COUNTRY_PROHIBITED` maps
  to `aliexpress_ship_to_prohibited` (422) with an actionable destination
  message; `605` maps to `aliexpress_product_unavailable` (404). Empty product
  envelopes are no longer labelled “Product not found.” Integration tests use
  isolated `droppilot_test` so Alembic head mismatches on the shared developer
  DB do not block the suite. Premium-editor migration `0018` is preserved on
  develop for stamp compatibility.
- **A-06** — CI `frontend-e2e` job runs Playwright chromium against a live API
  with `SECURITY_RATE_LIMIT_REQUESTS=1000`; `.env.example` documents the e2e
  ceiling; Playwright serves the standalone build via `npm run start:e2e`
  (also closes A-13 `next start` mismatch).
- **A-16 / A-09** — Finished the uncommitted Shopify webhook-tunnel workaround
  (was broken — a referenced function was never defined), added the two
  missing webhook topics (`products/create`, `app/uninstalled`), handled
  `app/uninstalled` by marking the connection `ERROR` immediately, and made
  replay-dedup fail closed (503) on a Redis outage for mutating topics
  (`orders/create`, `orders/updated`, `app/uninstalled`) while non-mutating
  topics still acknowledge.
- **A-15 (Shopify)** — Disconnecting a store now catches failures and shows a
  per-store error message instead of failing silently; the connected stores
  card also now shows when each store was originally connected, not just its
  last sync time.
- **A-02** — Logout (and mid-session token clear) call `queryClient.clear()` so
  a shared browser cannot show the previous tenant's React Query cache;
  `router.refresh()` alone was insufficient.
- **A-05** — Compose frontend defaults to `NEXT_PUBLIC_API_URL=http://localhost`
  so the SPA uses nginx same-origin `/api`; CORS includes the nginx origin; CI
  smoke asserts the bundle does not embed `:8000`.
- **A-04** — Shopify product publish is Celery-safe: create uses deterministic
  handle `droppilot-{product_id}` and adopts an existing Shopify product on
  redelivery instead of posting a duplicate.
- **A-03** — Platform-global AI prompt create / version / activate are refused
  unless `AI_ALLOW_PROMPT_MUTATION=true` (default off). Tenant admins can still
  list, history, and test-render. Tests opt in via conftest.
- **A-01** — Shopify `shop_domain` is globally unique (migration `0012`);
  webhooks resolve the owning tenant by indexed domain lookup instead of a
  capped table scan; connect rejects a shop already bound to another workspace.
- AliExpress connect is platform-credential only: merchants no longer enter App
  Key / App Secret. OAuth uses `ALIEXPRESS_APP_*` from the environment; tenant
  rows store encrypted seller tokens only (migration `0009`).
- Shopify connect rejects custom storefront domains; OAuth callback failures are
  classified (`hmac` / `state` / `exchange`) without logging secrets. See
  [SHOPIFY_CONNECTION_DEBUG_REPORT.md](docs/SHOPIFY_CONNECTION_DEBUG_REPORT.md).

### Added

- **Phase 8.1 Cloudflare wildcard verify** — public `*` route makes
  `/install`, `/webhook`, and `/health/live` reach FastAPI; path-specific
  tunnel rules 1–4 redundant; canonical webhook base set to
  `…/shopify/webhook`; live merchant consent still pending (M17). See
  [PHASE_8_1_LIVE_DEPLOY.md](docs/PHASE_8_1_LIVE_DEPLOY.md).
- **Phase 8.1 public routing re-verify** — Cloudflare Tunnel still path-scoped;
  public `/install` and `/webhook` remain 404; canonical webhook documented as
  `…/shopify/webhook`; Product Editor stashes left untouched; M17 still open.
  See [PHASE_8_1_LIVE_DEPLOY.md](docs/PHASE_8_1_LIVE_DEPLOY.md).
- **Phase 8.1 live deploy notes** — local Phase 8.1 routes confirmed after
  uvicorn restart; public `/install` still 404 due to path-scoped Cloudflare
  Tunnel; webhook base aligned to public POST callback; Partner/Cloudflare
  checklist. See [PHASE_8_1_LIVE_DEPLOY.md](docs/PHASE_8_1_LIVE_DEPLOY.md).
  M17 remains open pending merchant consent + tunnel path updates.
- **Phase 8.1 — Shopify OAuth production quality** — App URL install
  (`GET …/shopify/install` with HMAC), claim-install for anonymous App Store
  entry, Connect dialog (domain only), webhook base validation + singular
  `/webhook` receiver, idempotent webhook registration, disconnect revoke,
  uninstall releases shop claim, reconnect store reuse. Plan/completion:
  [PHASE_8_1_PLAN.md](docs/PHASE_8_1_PLAN.md),
  [PHASE_8_1_COMPLETION.md](docs/PHASE_8_1_COMPLETION.md). Live Partner OAuth
  consent remains open (M17).
- **Phase 8.1 verification follow-up** — documents that `phase-8-1-complete`
  was tagged before build/Playwright/live OAuth were fully verified; records
  subsequent green `next build`, Playwright Shopify (26) + integrations/shell
  (62), production `/install` 404, and remaining M17. Tag **not** moved. See
  [PHASE_8_1_VERIFICATION.md](docs/PHASE_8_1_VERIFICATION.md).
- **Shopify production install plan** — Partner checklist, AutoDS migration
  steps, webhook base mismatch findings. See
  [SHOPIFY_PRODUCTION_INSTALL_PLAN.md](docs/SHOPIFY_PRODUCTION_INSTALL_PLAN.md).
- **Shopify install-flow audit** — why shop domain is required for
  authorization-code grant and why AutoDS-like UX needs App URL architecture.
  See [SHOPIFY_INSTALL_FLOW_AUDIT.md](docs/SHOPIFY_INSTALL_FLOW_AUDIT.md).
- **Phase 9 Stage 3** — product optimisation data architecture: migration
  `0011` (SEO/marketplace/AI columns + `product_versions`),
  `ProductOptimizationService` (StubProvider via Stage 2 prompts), versions /
  optimize / activate APIs, products-table AI status + Optimize + History UI.
  See [PHASE_9_STAGE_3_COMPLETION.md](docs/PHASE_9_STAGE_3_COMPLETION.md).

### Changed

- **Phase 8.1** — `app/uninstalled` now deletes the Shopify connection (releases
  the global shop claim) instead of leaving an ERROR row; mutating webhook
  processing failures no longer ACK silently.

---

## [phase-8] — 2026-08-01

Shopify as the first sales channel. See
[PHASE_8_COMPLETION.md](docs/PHASE_8_COMPLETION.md) and
[SHOPIFY_INTEGRATION.md](docs/SHOPIFY_INTEGRATION.md).

### Added

- Migration `0008`: `shopify_connections`, `store_listings`, `orders.store_id`
- OAuth connect/callback/status/disconnect; Fernet-encrypted access tokens
- Product publish, inventory/price push, order import (poll + webhooks)
- Celery `shopify.*` tasks and beat entry
- Integrations UI Shopify card

### Verified

- Backend: ruff, mypy strict, **592** pytest
- Frontend: lint, typecheck, build
- Playwright integrations (chromium): **11** passed
- Live Shopify Admin/OAuth: **not run** (no Partner credentials)

---

## [phase-7] — 2026-07-31

Production hardening and operational readiness. See
[PHASE_7_COMPLETION.md](docs/PHASE_7_COMPLETION.md).

### Added

- Compose **beat** service and worker healthcheck
- CI on `develop`: image builds, `docker compose config`, Celery broker job,
  best-effort compose smoke
- `workers.health` task + `scripts/verify_celery_broker.py`
- Webhook HMAC (opt-in) and shed-without-429 limiter
- Email verification foundation: migration `0007`, logging mailer,
  `/auth/verify-email/*`, `RequireVerified` (flag-gated)
- `docs/STORE_CHANNEL_DECISION.md`, `docs/PHASE_7_PLAN.md`

### Changed

- Access tokens carry `email_verified`
- Frontend: removed unused notification Zustand store; removed stale Import nav

### Verified (local)

- Backend: ruff, mypy strict, pytest (583+)
- Frontend: lint, typecheck, build
- Live AliExpress and local Docker/Celery: **not** available on this machine —
  CI path documented

---

## [phase-6] — 2026-07-31

Inventory synchronisation, dynamic pricing, multi-store management, automation,
notifications, real analytics, and shipment tracking extensions.

Closed with migration `0006`, integration tests against real PostgreSQL, live
`AliExpressClient.call()` for the inventory dependency, and frontend pages for
every new module. See [PHASE_6_COMPLETION.md](docs/PHASE_6_COMPLETION.md).

### Added

**Domain** (`migration 0006`)
- `stores`, inventory sync runs/changes, pricing rules/changes, automation
  rules/runs, notifications, analytics daily rollups
- Product `sell_price` and optional `store_id`

**Services & APIs**
- Inventory sync (idempotent, reuses product import)
- Pricing engine (percentage / fixed / tiered; min profit / max price guards;
  preview before apply; audit trail)
- Store management with health and statistics
- Automation dispatcher (background-oriented)
- Notification centre
- Analytics dashboard aggregates

**Celery**
- `inventory.sync`, `pricing.recalculate`, `automation.run`,
  `shipment.refresh`, `analytics.aggregate`, `cleanup.old_notifications`

**Frontend**
- `/inventory`, `/pricing`, `/stores`, `/automation`, `/notifications`,
  `/analytics`, `/shipments`
- Dashboard and analytics consume live `/analytics/dashboard` — `MOCK_*` removed

### Verified

- **566 backend tests** — ruff, mypy strict, pytest all green
- **Live `AliExpressClient.call()`** — `product.get` success for inventory path;
  category / order error path re-checked
- Frontend lint, typecheck, build
- Tenant isolation tests for new repositories
- Playwright: Phase 6 ops smoke + updated shell dashboard tests (chromium)

### Not verified

- Celery under a live RabbitMQ broker (M15)
- Webhook signature verification (M11)
- Docker deployment (C1)
- Marketplace OAuth for sales channels (manual stores only)

---

## [phase-5] — 2026-07-31

Order management, fulfilment, and synchronisation from AliExpress.

Closed with live `AliExpressClient.call()` verification, migration `0005`,
integration tests against real PostgreSQL, and an Orders UI backed by the real
API. See [PHASE_5_COMPLETION.md](docs/PHASE_5_COMPLETION.md).

### Added

**Contract layer** (`app/integrations/aliexpress/orders.py`)
- Wire models and parsers for order detail, commission list, and logistics
- Captured live fixtures for error envelopes and list responses
- Client fix: unwrap `error_response` envelopes before error mapping

**Domain model** (`app/models/order.py`, migration `0005`)
- `orders`, `order_items`, `shipments`, `tracking_events`, `order_events`,
  `order_sync_runs`
- Validated fulfilment transition map

**Sync service** (`app/services/order_sync.py`)
- Idempotent incremental import; timeline merge; statistics

**Order API** (`app/api/v1/orders/router.py`)
- List (filters), statistics, sync, detail, timeline

**Background sync** (`app/tasks/orders.py`)
- `orders.sync_all`, `orders.sync_one_store`, `orders.refresh_status`,
  `orders.cleanup` with Celery beat entries

**Webhook processing**
- Redis replay protection, classification, delivery counters; unsigned payloads
  never mutate order state directly

**Frontend orders module**
- `/orders` list with filters, search, pagination, sync dialog, live statistics
- `/orders/[orderId]` detail with items, shipments, tracking, timeline
- Dashboard live order-synchronisation row

### Verified

- **523 backend tests** — ruff, mypy strict, pytest all green
- **Live `AliExpressClient.call()`** — category success; order get error path;
  commission order list capture
- **Tenant isolation** — SQL compile tests on order repositories; integration
  cross-tenant 404
- **Frontend lint, typecheck, build** — all pass
- **Playwright orders suite** — 16 passed against real backend

### Not verified

- Populated order-detail success body from live API (M16)
- Celery order tasks under a live broker/worker (M15)
- Webhook signature verification (M11 — unsigned)
- Docker deployment (C1 — unchanged)

---

## [phase-4] — 2026-07-31

Product import and catalogue synchronisation from AliExpress.

Closed with contract discovery from live payloads, integration tests against
real PostgreSQL, and a products UI backed by the real API. See
[PHASE_4_COMPLETION.md](docs/PHASE_4_COMPLETION.md).

### Added

**Contract layer** (`app/integrations/aliexpress/catalog.py`)
- Pydantic models parsing real `aliexpress.ds.product.get` and feed payloads
- Captured fixtures committed under `tests/fixtures/aliexpress/`

**Domain model** (`app/models/product.py`, migration `0004`)
- `products`, `product_variants`, `product_images`, `product_imports`
- Unique constraint on `(tenant_id, source, external_id)` for idempotent import

**Import service** (`app/services/product_import.py`)
- Import by supplier product id; feed browse without importing
- Idempotent upsert preserving tenant-set status on refresh

**Product API** (`app/api/v1/products/router.py`)
- List, detail, import, sync, import history, feed browse

**Background sync foundation** (`app/tasks/products.py`)
- `products.sync_one` — refresh one product from its supplier
- `products.sweep_stale` — fan out refresh for stale catalogue rows

**Frontend products module**
- `/products` page with table, empty state, and import dialog
- Real API fetchers in `services/products.ts`

### Verified

- **419 backend tests** — ruff, mypy strict, pytest all green
- **Tenant isolation** — SQL compile tests on four repositories; integration
  tests confirm cross-tenant access returns 404 not 403
- **Real payload parsing** — integration tests use committed capture, not
  invented JSON
- **Frontend lint, typecheck, build** — all pass
- **Playwright** — UI paths pass; import-flow tests skip when live OAuth
  callback cannot complete (documented)

### Not verified

- Celery product sync tasks under a live broker/worker
- Celery beat scheduling for stale-product sweeps
- Live import through production `AliExpressClient.call` (fixture transport in tests)
- Docker deployment (C1 — unchanged)

---

## [phase-3] — 2026-07-31

AliExpress integration foundation. Connection, credentials, client and inbound
webhook only — no product, price, inventory, or order functionality.

Closed with live verification against the real AliExpress gateway (sub-phases
3.5–3.7). See [PHASE_3_COMPLETION.md](docs/PHASE_3_COMPLETION.md).

### Verified live

- **OAuth round trip** completed end to end against `api-sg.aliexpress.com`,
  storing encrypted access and refresh tokens
- **Replay protection** — an OAuth state is deleted on first use and a second
  presentation is refused
- **API permissions** — product, category, search, order and freight endpoints
  reachable; affiliate correctly denied, verified against a known-good denial
  control rather than by absence of evidence
- **Webhook** reachable through `https://api.whiteto.com` and answering 200 for
  every malformed input tried

### Added

**Inbound webhook** (`app/integrations/aliexpress/webhook.py`)
- `POST /api/v1/integrations/aliexpress/webhook`, separate from the OAuth
  callback — different method, caller, contract and response
- Always answers 200, including on an unreadable body, because a delivery agent
  reads the status as a retry instruction
- Logs field names and counts, never payload values: an order notification
  carries buyer names and addresses
- Signature verification is **not** implemented; recorded as M11

### Fixed (during live verification)

- `sign_method` was excluded from the signature base string, which made every
  token exchange fail with `IncompleteSignature` (`fa02dd2`)
- Only the root `Settings` read `.env`, so every nested settings group silently
  ignored the file and ran on defaults (`ba752c9`)
- The OAuth callback required a Bearer token that a browser redirect from
  AliExpress can never carry (`74c6653`)
- Redis connections failed on every request because redis-py negotiates RESP3
  with `HELLO`, which the local server rejects (`74c6653`)

### Added

**Credential encryption** (`app/core/encryption.py`)
- Fernet (AES-128-CBC + HMAC-SHA256, random IV) for third-party credentials
- Key rotation via `MultiFernet`: keys newest-first, decryption tries each, so
  rotation needs no downtime
- Fails closed when no key is configured — refusing beats storing a customer's
  supplier secret in plaintext

**Database** — migration `0003`
- `aliexpress_connections` with encrypted secret and token columns, status,
  expiry, and last-sync tracking. Unique per tenant

**Integration package** (`app/integrations/aliexpress/`)
- Signed HTTP client with timeouts, jittered exponential backoff, and typed
  error mapping. Retries only failures that could resolve themselves
- Inspects the response body regardless of status, because AliExpress reports
  failure inside HTTP 200 as often as through a status code
- OAuth signing and a single-use, server-side, random `state` token

**Outbound rate limiting** (`app/integrations/rate_limiter.py`)
- Per tenant and provider. **Fails closed**, the opposite of the inbound
  limiter: exceeding a provider's quota can suspend the application key for
  every tenant

**Endpoints**
- `POST /api/v1/integrations/aliexpress/connect` (admin or owner)
- `GET /api/v1/integrations/aliexpress/callback`
- `GET /api/v1/integrations/aliexpress/status`
- `DELETE /api/v1/integrations/aliexpress/disconnect` (admin or owner)

**Background tasks** — `health_check` and `sweep_health_checks`, with tenant
context bound per connection; integration work routed to its own queue

**Frontend** — `/settings/integrations` with real server-driven connection
state, and a settings index that is now a genuine hub rather than a placeholder

**Documentation** — `docs/ALIEXPRESS_INTEGRATION.md`

### Changed

- Disconnect **hard-deletes** the connection, unlike everything else in the
  platform. A customer who disconnects has asked us to forget their credentials
- Celery `task_routes` sends `integrations.*` to a dedicated queue

### Fixed

- **Docker images installed a stub `app` package into site-packages** (debt item
  M1). The real code only won by `sys.path` ordering, so any command run from a
  different directory resolved to the empty stub. The builder now installs
  dependencies only, extracted from `pyproject.toml`

---

## [phase-2-complete] — 2026-07-31

Application shell, navigation, and SaaS UI foundation. Frontend only; no
business functionality.

### Added

**Application shell**
- `AppShell` — sidebar, top bar, and a main region that owns scrolling so the
  chrome stays put without `position: fixed`
- Collapsible desktop sidebar with six navigation sections, tooltips when
  collapsed, and a persisted collapse preference
- Responsive drawer below `md`, sharing the same navigation component as the
  desktop rail so the two cannot diverge; closes on navigation and when the
  viewport grows past the breakpoint
- Top bar with notification centre, theme toggle, and a user menu showing name,
  email, tenant, and role

**Navigation**
- `lib/navigation.ts` — one manifest feeding sidebar, drawer, and top bar. Each
  entry declares whether its destination exists; unbuilt ones render as
  non-interactive items, so primary navigation can never reach a 404

**Dashboard**
- Six stat cards with trend indicators that decouple direction from sentiment,
  so a metric where down is good is not painted red
- Three reusable charts — sales area, stacked orders, horizontal product
  performance — on a shared chart theme that reads design tokens at runtime, so
  a theme switch recolours them with no JavaScript
- `ChartContainer` owning all four chart states: loading, error, empty, populated

**Design system** — nine primitives: `avatar`, `tooltip`, `sheet`, `separator`,
`empty-state`, `error-state`, `page-header`, `coming-soon`, plus `stat-card` and
`chart-container` under `components/dashboard/`

**Routes** — `/products`, `/stores`, `/orders`, `/analytics`, `/settings`,
`/unauthorized`, plus loading and error boundaries scoped to the protected group

**State and services** — `notification-store`; `services/dashboard.ts`,
`products.ts`, `stores.ts` as query keys and types with no fetchers, because
those endpoints do not exist

**Testing** — 47 Playwright tests covering sidebar, mobile navigation, theme
switching, protected routes, dashboard, and user menu at 320px, 768px, and
1440px. They register real accounts through the API rather than stubbing it, so
they exercise the actual token and cookie handling

### Changed

- `layouts/sidebar.tsx` and `layouts/top-nav.tsx` moved into
  `components/navigation/` and split into focused components
- `middleware.ts` treats `/unauthorized` as public — it reports a permission
  failure, not an authentication one

### Fixed

- **`CORS_ORIGINS` could not be set in the documented format.**
  pydantic-settings runs `json.loads` on list-typed fields before validators
  execute, so the comma-separated form in `.env.example` raised
  `JSONDecodeError` during boot. The application could not start with its own
  example configuration. Fixed with `NoDecode` plus a validator accepting both
  forms, and covered by regression tests
- The user menu showed the email address twice for accounts with no name set

---

## [phase-1.1] — 2026-07-31

Pre-Phase-2 housekeeping.

### Added

- `CLAUDE.md` — the engineering constitution for this repository
- `docs/TECHNICAL_DEBT.md` — ranked debt register, each item with a trigger
- **Login throttle test coverage** (16 tests). The throttle previously had none,
  while a fixture docstring claimed otherwise. Runs against `fakeredis` so it
  executes everywhere rather than skipping without a Redis server
- **Authorization integration tests** (9 tests) covering each role, a token with
  no roles, a token with only unrecognised roles, and that authorization is
  decided before resource lookup so a 403 does not leak existence

### Changed

- Both `/api/v1/users` endpoints now require a recognised role via
  `RequireViewer`. `require_minimum_role` was previously unit-tested but wired
  to no endpoint
- Sidebar entries for unbuilt destinations render as disabled "Soon" items
  instead of linking to routes that returned 404
- Development branch renamed from `phase-0-foundation` to `develop`

### Fixed

- `frontend/tsconfig.tsbuildinfo` is no longer tracked in git
- Corrected a docstring in `tests/integration/conftest.py` that claimed test
  coverage which did not exist

---

## [phase-1-complete] — 2026-07-31

Authentication and multi-tenant identity. Commit `a21f7b8`.

### Added

**Authentication**
- Argon2id password hashing, with NFKC normalisation so the same password typed
  on different platforms verifies, and transparent rehashing when parameters are
  raised
- Password strength validation: minimum 12 characters, common-password denylist,
  and rejection of passwords containing the user's own email local part
- JWT access tokens (15 minutes) and refresh tokens (30 days), with the `typ`
  claim verified on every decode so a refresh token cannot be used as a bearer
  credential
- Refresh token rotation with reuse detection — replaying a consumed token
  terminates every session for that user
- `AuthenticatedUser` principal bound to context variables, feeding tenant
  filtering in every repository
- Role-based authorization: `require_roles` for exact membership and
  `require_minimum_role` for hierarchical checks
- Login throttling counted against email **and** client IP independently,
  checked before password verification
- Minimum length enforced on the JWT signing key (32 characters, RFC 7518 §3.2)
  in every environment

**API endpoints**
- `POST /api/v1/auth/register` — creates tenant, first user, and owner role
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/refresh`
- `POST /api/v1/auth/logout` — requires no access token
- `POST /api/v1/auth/logout-all`
- `GET /api/v1/auth/me` — roles read from the database, not the token

**Database** — migration `0002`
- `roles` table, seeded with four roles using deterministic UUIDv5 identifiers
  so a role has the same id in every environment
- `user_roles` association table with a composite primary key
- `refresh_tokens` table storing only a SHA-256 hash of each token

**Frontend**
- Sign-in, registration, and password-reset request pages
- `AuthProvider` session management; access token held in memory only, refresh
  token in an httpOnly path-scoped cookie
- Single-flight token refresh in the API client — without it, concurrent 401s
  each rotate the token, the second presents a consumed one, and reuse detection
  signs the user out
- `AuthGuard` route protection distinguishing *loading* from *unauthenticated*,
  so a page refresh does not eject an authenticated user
- Form primitives wiring react-hook-form and Zod to the design system with
  correct `aria-describedby` / `aria-invalid` handling

**Testing**
- 25 integration tests running against real PostgreSQL, building the schema by
  applying the Alembic migrations rather than `create_all`
- 53 new unit tests covering token verification, password handling, and
  authorization

**Documentation**
- `docs/Authentication.md`, `docs/Database.md`

### Changed

- **Tenant identity now comes from a verified JWT claim** instead of the
  `X-Tenant-ID` header. Confined to `app/api/deps.py`; no endpoint, service, or
  repository signature moved
- `users` gains `first_name`, `last_name`, `is_verified`
- `Base.__mapper_args__` sets `eager_defaults=True`, required in an async
  codebase — see Fixed below
- Enum columns pass `values_callable` so member values are persisted, not names
- `docs/DevelopmentSetup.md` rewritten; its `X-Tenant-ID` instructions had
  become actively wrong

### Removed

Each required by the Phase 1 specification. Safe because migration `0001` had
never been applied to any live database.

- `users.full_name` — replaced by `first_name` and `last_name`, with a read-only
  property preserving the display form
- `users.email_verified_at` — replaced by `is_verified`
- `users.role` enum column and the `user_role` type — replaced by `user_roles`.
  Keeping both would give a user's role two sources of truth, and reading the
  stale one is a privilege-escalation bug
- The Phase 0 `UserRole` **enum** was renamed to `RoleName` so the `UserRole`
  **table** could take the name

### Fixed

Three bugs found by running against real PostgreSQL. None was reachable without
a live database, and all three would have reached production.

- **Enum persistence.** SQLAlchemy sent member *names* (`"TRIAL"`) where the
  database expected values (`"trial"`), failing every tenant insert
- **`MissingGreenlet` on login.** Server-side `onupdate` columns are expired
  after a flush; serialising a just-updated user triggered lazy IO, which raises
  in async SQLAlchemy. This broke *every* login
- **No minimum JWT signing key length.** A short key signs and verifies
  normally and is only weaker, so the weakness was entirely silent

---

## [phase-0-complete] — 2026-07-30

Foundation. Commit `b513e4b`.

### Added

- Clean architecture with a strictly one-way dependency direction:
  `api → services → repositories → models`, with `core` depending on nothing
- Multi-tenancy enforced in a single base repository, reading the tenant from
  context so it cannot be forgotten at a call site
- UUID primary keys, UTC timestamps, soft deletes, deterministic constraint
  names, connection pooling
- Global exception handling producing one error envelope for every failure, with
  a request id on every response
- Structured logging with correlation across API and workers
- Redis caching with tenant-namespaced keys; distributed rate limiting with a
  circuit breaker
- Celery and RabbitMQ configured with context propagation and no jobs
- Next.js 15 / React 19 frontend, design system on CSS-variable tokens, light
  and dark themes
- Multi-stage non-root Dockerfiles, Compose stack, Nginx reverse proxy, GitHub
  Actions
- 54 tests, 11 of them targeting tenant isolation directly
- `README`, `Architecture`, `FolderStructure`, `CodingStandards`,
  `DevelopmentSetup`, `Contributing`

### Changed

- `app.workers.tasks` moved to `app.tasks` during finalisation. Tasks are entry
  points, architecturally symmetric with `api/`; `workers/` retains the
  infrastructure that runs them

### Fixed

- Pydantic `ClassVar` error that made the application unimportable
- Missing `email-validator` dependency
- Rate limiter paying a full Redis connect timeout on every request during an
  outage, adding ~4 seconds of latency per request

---

[Unreleased]: https://github.com/Adnan-Zulfiqar/ds-platform/compare/phase-2-complete...HEAD
[phase-2-complete]: https://github.com/Adnan-Zulfiqar/ds-platform/compare/phase-1-complete...phase-2-complete
[phase-1-complete]: https://github.com/Adnan-Zulfiqar/ds-platform/compare/phase-0-complete...phase-1-complete
[phase-0-complete]: https://github.com/Adnan-Zulfiqar/ds-platform/releases/tag/phase-0-complete
