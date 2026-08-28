# Subprocessors and international transfers

Only what is **actually running or actually configured**. A register listing
providers a product might one day use is worse than none — it implies diligence
that has not happened.

No SCC, IDTA, adequacy finding or signed DPA is claimed anywhere below, because
none has been evidenced. Where a provider publishes standard terms, that is
noted as *published terms, not accepted* until someone confirms acceptance.

## Active

| Provider | Purpose | Data | Location | Contract | Transfer position | Status |
|---|---|---|---|---|---|---|
| **DESIRLY LIMITED production server** | Runs the application, PostgreSQL and Redis | Everything in the register | **United Kingdom** | Self-hosted — no third party | No transfer | Active |
| **Cloudflare** | Tunnel and TLS termination via `cloudflared` (PID confirmed running) | Request metadata and **client IP addresses** at the edge; traffic content in transit | Global anycast network | Cloudflare's published terms and DPA — **acceptance not yet confirmed** | Cloudflare publishes SCC/UK-addendum based terms. **Not verified for this account.** | **Active** |
| **eBay** | Seller OAuth, listing and order APIs; marketplace account deletion notifications | Seller account identifier, display name, scopes, tokens; whatever the seller authorises | eBay's own infrastructure, international | eBay Developers Program agreement — accepted to obtain credentials | eBay is an independent controller for its own platform data. Transfers occur under eBay's terms | **Active** (connection only; no listing or order import yet) |
| **Shopify** | Merchant store connection, product and order APIs | Shop domain and id, access token, catalogue and order data | Shopify infrastructure, international | Shopify Partner Program terms | Shopify's own DPA and transfer terms apply to data it processes | Active when a merchant connects |
| **AliExpress / Alibaba** | Supplier catalogue and ordering | App credentials, product data, order placement details | Alibaba infrastructure, **including outside the UK/EEA** | AliExpress Open Platform terms | **No transfer mechanism established by DESIRLY LIMITED.** Requires review before order placement handles buyer addresses | Active when a merchant connects |

## Configured but disabled

| Provider | Status | Notes |
|---|---|---|
| OpenAI / Anthropic | **Disabled** — `AI_PROVIDER` defaults to `stub`, which generates locally and sends nothing | If enabled, listing text is sent to the provider. Add to the active table and re-check transfers before enabling |
| Open Exchange Rates | **Disabled** — FX defaults to `unavailable` | If enabled, **currency codes only**; no personal data |
| AWS S3 | **Not used** — `StorageSettings` exists (`S3_` prefix, `S3_REGION` default `eu-west-1`) but no application code reads it | Not a subprocessor today |

## Not present

* **No email provider.** Outbound email is not implemented — no SMTP, no
  transactional email service. Verification tokens are generated and stored but
  nothing sends them. This also means **no provider receives customer email
  addresses**.
* **No analytics, advertising or tracking provider.** Verified by repository
  search: nothing in `package.json`, and the only "Sentry" reference is a
  comment in `frontend/app/error.tsx` marking where a reporter would go.
* **No payment processor**, because billing is not implemented.

## Operator action required

1. **Cloudflare** — confirm which account and plan is in use, and whether
   Cloudflare's DPA has been accepted. This is the one active third party
   receiving IP addresses on every request, so it matters most.
2. **AliExpress** — establish the transfer position before order sync sends
   buyer names, phone numbers and addresses to a supplier outside the UK/EEA.
   This is the largest unresolved transfer risk in the product.
3. **Shopify and eBay** — record which agreement version was accepted and when.
4. Publish a subprocessor list to merchants, and decide how they will be
   notified of changes — usually a contractual commitment.
5. Re-check this register whenever a provider is enabled. The disabled rows are
   one environment variable away from being active.
