# Track E6 — subscription billing (Stripe)

Status: **decided 2026-10-04 (D-016).** Stripe Billing with three monthly
plans and a 30-day free trial, granted once. Built in stages:

| Stage | Scope | State |
|---|---|---|
| E6a | Stripe client, subscription state, checkout, plan change, portal, sync, signed webhook | Done (PR #73) |
| E6b | Enforcement: listing limit at publish, AI add-on gate, read-only after trial, trial once per store | Done (this PR) |
| E6c | Settings → Billing page | Planned |

## Plans

| Plan | Monthly | Listings | AI add-on (unlimited AI) |
|---|---|---|---|
| Starter | $12 | 200 | $8 |
| Growth | $30 | 450 | $12 |
| Pro | $70 | 1000 | $17 |

**What counts as a listing.** Each published product counts once per
enabled variant (at least one), on each store it is published to. A product
with 10 variations counts as 10. Drafts and removed listings do not count.

**Trial.** The trial runs for 30 days from workspace creation, with Growth's
450 listings and no AI. Subscribing during the trial keeps the remaining
free days, because Stripe is told to start charging when the trial ends.

## Stripe set-up (sandbox, done 2026-10-04)

Everything below is in **Desirly Limited sandbox** (`acct_1UHKYv5Hwtvz7QHG`).
It was created through the Stripe connector:

- **Six recurring USD prices**, found by lookup key:
  - plans: `droppilot_{starter,growth,pro}_monthly`;
  - AI add-ons: `droppilot_ai_addon_{starter,growth,pro}_monthly`.
- **Webhook endpoint:** `https://api.whiteto.com/api/v1/billing/webhooks/stripe`.
  - Events: checkout completed, `customer.subscription.*`, `invoice.paid`
    and `invoice.payment_failed`.
  - It is reachable once the tunnel points at this PC.
- **Customer portal configuration** (the default):
  - customers can update their card, address and tax ID;
  - customers can see their invoices;
  - customers can cancel, effective at period end.

  Plan and add-on changes happen in DropPilot (`/billing/change`), because
  the portal cannot change a subscription that has two items.

**Root `.env` (owner):** `STRIPE_SECRET_KEY` (`sk_test_…`, verified against
the sandbox account) and `STRIPE_WEBHOOK_SECRET` (`whsec_…`).

**Going live later.** The live account needs the same six prices with the
same lookup keys, a live webhook endpoint, a portal configuration and live
keys. No code changes.

## E6a as built

| Endpoint | Who | What |
|---|---|---|
| `GET /api/v1/billing` | any member | plan, status, trial end, usage, limits, catalogue |
| `POST /api/v1/billing/checkout {plan, aiAddon}` | owner | returns the Stripe Checkout URL |
| `POST /api/v1/billing/change {plan, aiAddon}` | owner | swaps subscription items, prorated |
| `POST /api/v1/billing/portal` | owner | returns the customer portal URL |
| `POST /api/v1/billing/sync` | any member | re-reads the subscription from Stripe; called on return from Checkout |
| `POST /api/v1/billing/webhooks/stripe` | Stripe | checks the signature, then re-reads the subscription |

- **Stripe client.** A small `httpx` client (`app/integrations/stripe/client.py`)
  instead of the `stripe` package, so there is no new locked dependency.
  It verifies webhook signatures with Stripe's v1 scheme and a 5-minute
  replay window.
- **Webhooks are doorbells.** The subscription is fetched again with the
  secret key, and the workspace comes from **Stripe's** copy of its
  metadata. The subscription's customer must be the one this workspace
  created, otherwise the event is ignored. A forged or misrouted
  subscription cannot change another workspace's plan.
- **State.** `tenant_subscriptions` (migration `0047`) mirrors Stripe: plan,
  add-on, status, period end, cancel-at-period-end, and the trial end.
  `past_due` keeps access while Stripe retries the card.

### Verified (E6a)

- `tests/unit/test_stripe_client.py`:
  - a correct signature is accepted, including when several `v1` values
    are sent;
  - a changed body, the wrong secret or a malformed header is refused;
  - an old timestamp is refused as a replay;
  - nested form encoding is correct.
- `tests/integration/test_billing.py` runs against a fake Stripe and
  asserts the exact form bodies sent:
  - a new workspace is on the trial;
  - checkout sends the plan, the add-on, the tenant metadata and the
    remaining trial;
  - a sync applies a paid plan;
  - a plan change swaps the items and drops the add-on;
  - a signed webhook uses Stripe's copy over the event body;
  - a bad signature gets 400;
  - a subscription naming another workspace is ignored;
  - only owners can manage billing, and an unconfigured server answers 503.

### Not verified

- A real sandbox checkout. It needs the backend recreated with this code and
  the test card `4242 4242 4242 4242` entered on Stripe's page, which is an
  owner step.

## E6b as built

Nothing is enforced while `STRIPE_SECRET_KEY` is blank, so self-hosted and
test deployments behave as before.

| Where | Check | Error (HTTP 402) |
|---|---|---|
| eBay, Shopify, WooCommerce publish | room for the product's listings, before the lock | `listing_limit_reached` (details: used, limit, needed) |
| `POST /products/import`, automation import step | trial running or plan paid | `billing_inactive` |
| AI prompt execution, image analysis | AI add-on on a paid plan | `ai_addon_required` (or `billing_inactive`) |

- **Counting.** A listing is a product published to one store, counted as
  its enabled variants (at least 1). Republishing an already-listed product
  is free. Removed listings do not count.
- **Trial.** It gets the Growth limit (450, `TRIAL_LISTING_LIMIT`) and no
  AI. This was my default, not the owner's; it is one constant to change.
- **After the trial without a plan.** The workspace is read-only for new
  work, while refresh, sync and orders keep flowing. Blocking an existing
  customer's order flow over billing would hurt their buyers, not them.
- **One trial per store.** When a store connects (Shopify OAuth, eBay OAuth,
  WooCommerce keys), a Celery task `billing.claim_trial` records a SHA-256 of
  the store's identity. If another workspace had already recorded it, the
  new workspace's trial ends at once. A paying workspace is not touched. A
  closed first workspace still counts. The task runs after commit and is
  idempotent.
- **`TrialFingerprintRegistry`** is unscoped by necessity and was added to
  the CLAUDE.md §4 closed list: a hash in, a boolean out, Celery only.
- **Migration.** `0048` adds `trial_fingerprints`.

### Verified (E6b)

`tests/integration/test_billing_limits.py`, 7 tests on real Postgres. They
cover:
- the per-variant count across two stores;
- the exact limit and a free republish;
- AI refused on trial and on Pro without the add-on;
- `/products/import` returning 402 after the trial;
- nothing enforced without Stripe;
- the trial claim: first use, the same account twice, a reused store, a
  paying workspace and a deleted holder.

The test found one bug, which is fixed: the import check ran before the
tenant was bound.

### Not verified (E6b)

- The publish and AI wiring is exercised through `BillingGate`, not through
  each marketplace's publish endpoint.
- The connection hooks have not been run against live Shopify, eBay or
  WooCommerce.

---

*Original proposal (kept for the record):*

## Why not just build it

Billing code that guesses the plans, prices, trial length and payment
provider would be built around inventions. Each of these is a business
decision with legal and tax consequences for DESIRLY LIMITED, a UK company,
selling to businesses worldwide. Choosing them is not engineering, and
building around a guess means rebuilding when the real answer arrives.
Everything below is a recommendation for the owner to accept or change.

## Decisions needed from the owner

| # | Decision | Recommendation | Why |
|---|---|---|---|
| 1 | Payment provider | **Paddle** (merchant of record) | Paddle is the legal seller. It collects and remits VAT/GST/sales tax in every jurisdiction, which matters for a UK company selling to sellers worldwide. Stripe Billing is cheaper per transaction, but the tax registrations and filings become ours (Stripe Tax calculates, it does not remit) |
| 2 | Plans and prices | Owner to supply, e.g. Starter / Growth / Pro, monthly and annual | Prices are a commercial decision |
| 3 | What each plan limits | Suggest: connected stores, products under management, AI generations per month, team seats | Each limit needs an enforcement point. Seats would gate Track E4 invitations |
| 4 | Trial | Suggest 14 days, no card, then read-only until subscribed | `tenants.status` already has `trial`. Read-only is safer than deleting data |
| 5 | Over-limit behaviour | Suggest: block new additions, never stop syncing existing listings | Breaking a live seller's stock sync over billing would oversell, and real buyers would lose out |
| 6 | Sandbox account | Owner creates the Paddle (or Stripe) **sandbox** account and puts its keys in `.env` | Keys are secrets. The agent never creates accounts or handles credentials |

## Proposed design (once decided)

- **The provider is the source of truth.** Webhooks (signature-verified,
  idempotent by event id) update a tenant-scoped `subscriptions` row: plan,
  status and current period end. The app never computes billing state itself.
- **Checkout and the customer portal are the provider's hosted pages.** No
  card data ever touches DropPilot (PCI scope stays at SAQ-A).
- **Plan limits live in one module** (`app/services/entitlements.py`) and are
  read through one dependency. Each existing creation point calls it:
  - store connect;
  - import;
  - AI generate;
  - invitation.
- **Tenant suspension for non-payment** reuses `tenants.is_active`, which
  login already enforces uniformly.
- **Tests:**
  - Webhook signature rejection.
  - Replay of the same event leaves the state unchanged.
  - Each limit is enforced at its creation point.
  - Over-limit never stops a running sync.

## Owner checklist (to unblock)

1. Choose the provider. The recommendation is Paddle.
2. Write down the plans, prices (monthly and annual, currency) and the limit
   values for each plan.
3. Confirm the trial and over-limit behaviour, or change them.
4. Create the provider's **sandbox** account. Put its API key and webhook
   secret in the root `.env` under names the agent will document. Do not paste
   them in chat.
5. Tell the agent "E6 decisions done", with the answers to 1–3.

E6 stays open until then. The agent moves on to the next Track E item.
