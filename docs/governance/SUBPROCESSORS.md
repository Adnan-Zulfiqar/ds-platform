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
| **Google** (Identity Services) | Sign-in. The browser obtains a signed ID token; we verify it | The address, name and Google's immutable subject identifier. **No Google token is stored** | Google infrastructure, international | Google Terms of Service; the OAuth client is registered to `https://app.whiteto.com` | Google is an independent controller for its own account data. **No DPA or transfer assessment completed by DESIRLY LIMITED** | **Active once enabled** — Google app is still in Testing mode |
| **Resend** | Transactional email: password-reset codes only | Recipient address, subject, message body | **Ireland (`eu-west-1`)** — sending domain `auth.whiteto.com` verified there | Resend's published terms | Sending region is in the EEA. **No DPA signed or transfer assessment completed** | **Configured, not enabled** — no API key exists yet |
| **AliExpress / Alibaba** | Supplier catalogue and ordering | App credentials, product data, order placement details | Alibaba infrastructure, **including outside the UK/EEA** | AliExpress Open Platform terms | **No transfer mechanism established by DESIRLY LIMITED.** Requires review before order placement handles buyer addresses | Active when a merchant connects |

## Configured but disabled

| Provider | Status | Notes |
|---|---|---|
| OpenAI / Anthropic | **Disabled** — `AI_PROVIDER` defaults to `stub`, which generates locally and sends nothing | If enabled, listing text is sent to the provider. Add to the active table and re-check transfers before enabling |
| Open Exchange Rates | **Disabled** — FX defaults to `unavailable` | If enabled, **currency codes only**; no personal data |
| AWS S3 | **Not used** — `StorageSettings` exists (`S3_` prefix, `S3_REGION` default `eu-west-1`) but no application code reads it | Not a subprocessor today |

## Not present

* **No marketing email.** Resend carries transactional messages only — one
  message type, the password-reset code. There is no mailing list, no campaign
  tooling and no marketing scope. Email verification tokens are still generated
  and stored but nothing sends them.
* **No Firebase.** Google sign-in uses Identity Services directly. Firebase
  Authentication was not adopted: it would put a second identity store and a
  second session authority beside the one this platform already has.
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
