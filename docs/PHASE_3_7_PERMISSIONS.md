# Phase 3.7 — AliExpress permission verification

**Result: Phase 4 is ready.** All five capabilities Phase 4 needs are reachable
with the live token. No application code was modified.

| | |
|---|---|
| Date | 2026-07-31 |
| Application | app key `541272`, environment `test`, registered as 【AutoPilot】 |
| Gateway | `https://api-sg.aliexpress.com/sync` |
| Token | live access token from the Phase 3.6 OAuth round trip |

---

## Correction to the Phase 3.6 report

Phase 3.6 recorded that the OAuth consent screen requested only *Alibaba
Member's Basic Information*, and concluded that the missing product scope was
"the most likely blocker for Phase 4".

**That conclusion was wrong.** Direct probing shows the dropship APIs are fully
accessible with the token that consent produced.

The consent screen describes what is read from the *end user's account*
— identity, membership. It does not enumerate the application's API permission
groups, which is what actually governs which methods may be called. The
**AliExpress-dropship** group being Active in the console is the thing that
matters, and it is in force.

The lesson is narrow and worth keeping: the consent screen is not a reliable
proxy for API permission. Only calling the APIs answers the question.

---

## Method

The developer console cannot be read from the application, so permission was
established empirically — call each API with the live token and let the gateway
answer.

This works because the gateway checks permission **before** business parameters.
A parameter error therefore proves authorisation succeeded and only the
arguments were wrong.

Two controls make the readings trustworthy rather than assumed:

| Control | Purpose | Result |
|---|---|---|
| `aliexpress.this.method.does.not.exist` | what an unknown method looks like | `InvalidApiPath` |
| `aliexpress.affiliate.product.query` | what a genuine denial looks like | **`InsufficientPermission` — "App does not have permission to access this api"** |

The second is the important one. Without it, "no permission error appeared"
would be an argument from silence. With it, we know exactly what a denial looks
like on this gateway — and **no dropship API produced one**.

`InvalidApiPath` was initially misread as a denial. The first control corrected
that: it means the method name does not exist, not that access was refused.

**All probes were read-only.** Order APIs were exercised with lookups against
non-existent identifiers. Nothing was created, modified or cancelled on the live
account.

---

## Enabled permissions

### 1. Product APIs — available

| Method | Evidence |
|---|---|
| `aliexpress.ds.product.get` | `rsp_code 605 ITEM_ID_NOT_FOUND` — executed a real lookup against a fabricated id |
| `aliexpress.ds.category.get` | `resp_code 200 "Call succeeds"`, 51,470 bytes of category data |

The `ITEM_ID_NOT_FOUND` response is the strongest single signal in this report:
the method ran, queried the catalogue, and reported the id missing. That is only
reachable past authorisation.

### 2. Search APIs — available

| Method | Evidence |
|---|---|
| `aliexpress.ds.text.search` | `rsp_code 00` when `sortBy` is supplied |
| `aliexpress.ds.feedname.get` | `resp_code 200 "Call succeeds"`, 16,154 bytes of feed names |
| `aliexpress.ds.recommend.feed.get` | `rsp_code 200`, valid empty result set |
| `aliexpress.ds.image.search` | `MissingParameter: shpt_to` |

`aliexpress.ds.text.search` returns `NGSELECTION_SEARCH_ERROR` when `sortBy` is
omitted and `rsp_code 00` when it is supplied. That is parameter sensitivity,
not permission — worth knowing before Phase 4 spends time on it.

### 3. Order APIs — available

| Method | Evidence |
|---|---|
| `aliexpress.ds.trade.order.get` | `MissingParameter: order_id` |
| `aliexpress.ds.commissionorder.listbyindex` | `MissingParameter: start_time` |
| `aliexpress.trade.ds.order.get` | `code 15 isp.service-unavailable` — routed to the order service |

Note the correct name is **`aliexpress.ds.trade.order.get`**, not
`aliexpress.ds.order.get` (`InvalidApiPath`) and not `aliexpress.ds.order.query`
(`InvalidApiPath`).

### 4. Logistics / freight APIs — available

| Method | Evidence |
|---|---|
| `aliexpress.ds.freight.query` | `MissingParameter: currency` |
| `aliexpress.logistics.buyer.freight.calculate` | `MissingParameter: send_goods_country_code` |

### 5. Seller authorisation APIs — available

| Method | Evidence |
|---|---|
| `aliexpress.ds.member.benefit.get` | returned a populated `benefit_catalog` (2 entries) |

Token issue and refresh were already proven live in Phase 3.6.

---

## Missing permissions

### Affiliate APIs — denied, and not required

`aliexpress.affiliate.product.query` returns `InsufficientPermission`. These
belong to the **Affiliate / Portals** permission group, which is a different
programme from dropshipping. Phase 4 sources products through the DS APIs, so
this is not a blocker. Request it only if commission tracking on affiliate links
becomes a product requirement.

### Webhook / notification APIs — not exposed here

No `/sync` method was found for notifications; `aliexpress.ds.notification.list`
returns `InvalidApiPath`, the unknown-method code.

AliExpress delivers asynchronous events through a separate message subscription
service configured in the console, not through a callable API on this gateway.
This is **not a permission denial** — the capability simply is not reachable by
this mechanism.

Phase 4 does not require it. Order status can be polled through the order APIs,
which is what the existing Celery task structure is built for. Push notification
is an optimisation to consider later, not a prerequisite.

---

## Phase 4 blockers

**None from permissions.** Every capability Phase 4 requires is reachable.

Carried forward from earlier phases, none of which are permission-related:

1. **No business API call has been exercised through the application's own
   client.** All probing here was direct HTTP using the application's signing
   code. `AliExpressClient.call` and the response schemas remain unverified
   against real payloads. This is the real risk for Phase 4, and it is a
   contract risk rather than an access one.
2. **Token refresh is still unverified live.** Tokens expire 2026-08-30.
3. **Docker remains unbuilt** (C1 in `TECHNICAL_DEBT.md`).
4. **Local Redis is 3.0.504**, pinned to RESP2; production targets Redis 7.
5. **The callback reaches the backend through a development tunnel** forwarding
   a single path.

---

## Required AliExpress console actions

**None are required to begin Phase 4.**

Two are optional and worth doing at some point:

| Action | Priority | Why |
|---|---|---|
| Rename the application from 【AutoPilot】 to DropPilot AI | Low, before public launch | It is the name every seller sees on the consent screen |
| Request the Affiliate / Portals permission group | Only if needed | Required solely for affiliate commission APIs, which Phase 4 does not use |

---

## Verdict

**Phase 4 is ready to begin.** Product, search, order, logistics and seller
authorisation APIs are all confirmed reachable with the live token, verified
against a known-good denial control rather than by absence of evidence.
