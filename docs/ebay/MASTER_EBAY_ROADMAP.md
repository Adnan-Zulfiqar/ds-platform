# eBay integration — master roadmap

eBay is the second sales channel, after Shopify. It arrives compliance-first,
for a reason that is not optional: eBay requires every Developers Program
application to subscribe to Marketplace Account Deletion/Closure notifications
(or formally opt out) **before its first production API call**, and the keyset
stays inactive until that endpoint validates. Building listings before the
deletion endpoint would mean building on a keyset that cannot be used.

## Phases

| Phase | Scope | Status |
|---|---|---|
| **EBAY-C0** | Compliance challenge, signed deletion notifications, safe configuration | **complete** |
| **EBAY-C0.1** | Retry idempotency hotfix — identity digest replaces the raw-body digest | **complete, deployed** |
| **EBAY-C1** | OAuth connect / reconnect / disconnect, encrypted per-tenant tokens, functional integration card | **complete on branch, not deployed** |
| **EBAY-C2** | Seller policies, marketplaces, inventory locations, listing defaults | **complete on `develop` (mocked eBay transport only)** — [`EBAY_C2_LISTING_SETUP.md`](EBAY_C2_LISTING_SETUP.md) |
| **EBAY-C3** | Draft-to-eBay listing publication | **complete on `develop` (mocked eBay transport only)** — [`EBAY_C3_PUBLISH.md`](EBAY_C3_PUBLISH.md); proposal approved 2026-10-03 |
| **EBAY-C4** | Inventory and pricing synchronisation | **complete on `develop` (mocked eBay transport only)** — [`EBAY_C4_PRICE_QUANTITY.md`](EBAY_C4_PRICE_QUANTITY.md) |
| EBAY-C5 | Orders, fulfilment, tracking, cancellation | not started |
| EBAY-C6 | Production growth-check and operational hardening | not started |

**EBAY-C0 and EBAY-C0.1 are complete and running in production.** C0.1 was
verified against genuine eBay retry traffic: a real redelivery returned 204,
incremented the receipt count, upgraded one legacy digest, did not repeat the
deletion, and produced no new 409.

**EBAY-C1 is complete on its branch and is *not* deployed.** No seller has
completed a live eBay consent flow and no real eBay token has been exchanged;
everything is verified against eBay's published specifications and a mocked
transport. Production also has no frontend build yet, which is a prerequisite for
any C1 rollout — see the deployment section of
[`EBAY_C1_SELLER_CONNECTION.md`](EBAY_C1_SELLER_CONNECTION.md).

See [`EBAY_C0_COMPLIANCE.md`](EBAY_C0_COMPLIANCE.md) for the compliance
contracts and what ``payload_digest`` covers after C0.1, and
[`EBAY_C1_SELLER_CONNECTION.md`](EBAY_C1_SELLER_CONNECTION.md) for the OAuth
design, decisions and limitations.

---

## The release guard — satisfied by EBAY-C1, and still live

**No eBay data storage may ship without deletion coverage.** This is not a
convention — it is a test that fails.

EBAY-C0 established that *no table in this application stored eBay user data*,
which is what made its correct behaviour a **verified zero-match deletion**.
EBAY-C1 changed that: `ebay_connections` holds a seller's immutable eBay
`userId`, their display username, and their encrypted access and refresh tokens.

The guard did its job. Adding that table failed three tests in the C0 suite until
the storage was **declared** in `EBAY_STORAGE_DECLARATIONS` and an eraser was
**registered** in `_OWNERS` (`app/integrations/ebay/deletion.py`), in the same
change. What the guard now checks, on every run:

* every declaration has an owner (`unowned_declarations()` — a release blocker);
* every owner has a declaration (`undeclared_owner_names()` — a dead eraser that
  looks like coverage);
* no model *outside* `ebay.py` grows an eBay identifier without being declared.

Every owner must:

* erase irreversibly — physical delete or irreversible anonymisation. A
  soft-delete does not satisfy eBay's requirement that *"even the highest
  system privilege cannot reverse the deletion"*;
* cover **every** tenant holding that person's data, not one workspace;
* invalidate any cache keyed on the erased data;
* be idempotent — eBay redelivers, and a second run must not fail;
* be safe when nothing matches.

The same obligation applies unchanged to C2 onwards. Any table that stores an
eBay listing id, order, buyer detail or policy tied to a seller account is
declared and erased in the change that introduces it, or it does not ship.

---

## Carried into later phases

0. **eBay's per-attempt fields are not part of a notification's identity.**
   ``publishDate`` and ``publishAttemptCount`` change on every resend, by
   documented design. Anything that decides "have I seen this before" must be
   built from ``notificationId`` plus immutable event content — never from the
   raw body, and never from a field whose description mentions the *attempt*.
   EBAY-C0.1 exists because that distinction was missed once; a future topic
   with its own retry semantics must not repeat it.

1. **Signature format is SHA-1 ECDSA over the raw body.** eBay's choice,
   verified against their published vector, documented in
   `app/integrations/ebay/signature.py`. Do not "modernise" the digest — real
   notifications would stop verifying.
2. **The compliance endpoint string is hashed byte for byte.** Never normalise
   it, never derive it from a request header, never add or remove a trailing
   slash.
3. **Never acknowledge what has not been verified and processed.** eBay retries
   for 24 hours; it never resends an acknowledged notification. A retryable
   failure costs a delay, a premature 2xx costs a deletion request.
4. **The public-key path is SSRF-proof by construction**, not by filtering: a
   fixed host plus a `uuid.UUID` path segment. Keep it that way — a caller must
   never be able to supply a URL.
5. **Platform credentials are not merchant settings.** `EBAY_CLIENT_*`,
   `EBAY_DEV_ID` and the verification token belong to DropPilot's application
   and must never gain a frontend input field. Per-seller OAuth tokens are the
   opposite: encrypted, per-tenant, and never returned by an API — the same
   shape as `shopify_connections`. EBAY-C1 kept both halves of that.
6. **`redirect_uri_name` is the RuName, not a URL.** EBAY-C1 consumes it. eBay
   resolves it to the accept and decline URLs registered in the portal; sending
   an actual URL fails on eBay's own page with nothing diagnosable. `dev_id`
   remains declared and unused.
7. **`access_token_for` is the only way to obtain a usable token.** Every eBay
   call from C2 onwards goes through it, so "is it still valid, and who
   refreshes it" is answered once rather than at each call site. Do not decrypt
   `encrypted_access_token` anywhere else.
8. **The scope set was audited once and requested in full.** eBay's consent is
   per-authorization, so adding a scope in a later milestone sends every
   connected merchant back through consent. C2-C5 must work within
   `EBAY_OAUTH_SCOPES` or accept that cost deliberately.
9. **Assume nothing newer than Redis 3.0.** The Windows deployments run the
   3.0.504 build while compose and CI run Redis 7. EBAY-C1 shipped `GETDEL` and
   had to replace it with `MULTI`/`EXEC`, because the resulting error was
   indistinguishable from a genuine CSRF rejection. Until that divergence is
   closed, check the command's version before using it.

---

## Not started, and deliberately so

EBAY-C1 ends at "this workspace has a usable eBay access token, and the platform
can prove whose it is". It implements no listing, no inventory, no pricing, no
orders and no fulfilment, and no background token refresh.

C2 begins at the first call that *uses* that token: the seller's business
policies, marketplaces and inventory locations. Nothing was built ahead for it.

Neither milestone implements an opt-out flow. This application now persists eBay
data, so subscribing to deletion notifications is the correct choice and the
portal's exemption route is not modelled.
