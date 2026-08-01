# Store channel architecture decision

| | |
|---|---|
| Date | 2026-07-31 |
| Phase | 7 |
| Status | **Implemented in Phase 8 (Shopify / Option A)** |
| Implements in Phase 7? | No |
| Implements in Phase 8? | Yes — Shopify |

Phase 6 shipped multi-store as a **manual-first** model: tenants create and
manage `Store` rows in DropPilot without connecting a sales-channel OAuth app.
This document records the options for the next channel-integration step and the
decision for what to build next.

---

## Options

### Option A — Shopify OAuth integration

Connect Shopify shops via OAuth, pull products/orders, push inventory and
fulfilment updates.

| | |
|---|---|
| Pros | Largest dropshipping channel for many merchants; mature Admin API; clear OAuth docs |
| Cons | App review, webhook verification, scopes, billing app fees; large surface area |
| Effort | High (OAuth + webhooks + product/order mapping + multi-store edge cases) |

### Option B — WooCommerce integration

Connect WooCommerce via REST API keys (or application passwords) rather than a
full OAuth marketplace app.

| | |
|---|---|
| Pros | Common with AliExpress sellers; simpler auth than Shopify apps for self-hosted shops |
| Cons | Heterogeneous hosting; plugin version drift; weaker “app store” distribution |
| Effort | Medium–high |

### Option C — Manual store management only (status quo)

Keep stores as first-class tenant entities without live channel sync. Merchants
use DropPilot for supplier sync, pricing, inventory, and order ops; channel
publish remains out of band or future work.

| | |
|---|---|
| Pros | Already shipped; no OAuth secrets or channel webhook attack surface; matches Phase 6 scope |
| Cons | Not a full AutoDS competitor on storefront sync until A or B lands |
| Effort | None for Phase 7 |

---

## Decision

**Stay on Option C for the immediate next product slice.** Prefer **Option A
(Shopify)** as the first *implemented* channel when the roadmap opens a store-
channel phase, unless customer demand clearly skews WooCommerce-first.

### Why not implement A/B in Phase 7

Phase 7 is production hardening (deployment, Celery, webhooks, auth foundation).
Adding a sales-channel OAuth integration would:

1. Expand secrets, redirect URIs, and webhook surfaces before C1/M15 are closed.
2. Force schema and sync design under time pressure without merchant pilots.
3. Violate the phase rule: implement only the current phase.

Manual stores remain the supported path. Channel OAuth is a dedicated later
phase with its own migration, decision record for scopes/webhooks, and security
review.

### Prerequisites before implementing Option A

- C1 closed with a proven Compose/production deploy path
- Webhook HMAC mode proven against a real AliExpress (or Shopify) delivery
- Clear tenant isolation tests for any new channel connection table
- Documented secret storage (already have Fernet pattern from AliExpress)

---

## Non-goals (this decision)

- No Shopify/WooCommerce client code in Phase 7
- No fake “connected” UI that pretends OAuth succeeded
- No change to the existing `StorePlatform` enum semantics beyond documentation
