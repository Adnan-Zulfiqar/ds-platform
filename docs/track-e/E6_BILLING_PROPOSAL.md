# Track E6 — subscription billing: proposal (needs owner decisions)

Status: **proposal only. Nothing built.** Blocker **B-014**.

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
