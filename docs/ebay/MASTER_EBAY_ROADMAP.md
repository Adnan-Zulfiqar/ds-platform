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
| **EBAY-C0.1** | Retry idempotency hotfix — identity digest replaces the raw-body digest | **complete** |
| EBAY-C1 | OAuth connect / reconnect / revoke, encrypted per-tenant tokens | not started |
| EBAY-C2 | Seller policies, marketplaces, inventory locations | not started |
| EBAY-C3 | Draft-to-eBay listing publication | not started |
| EBAY-C4 | Inventory and pricing synchronisation | not started |
| EBAY-C5 | Orders, fulfilment, tracking, cancellation | not started |
| EBAY-C6 | Production growth-check and operational hardening | not started |

EBAY-C0 is complete, with the EBAY-C0.1 retry-idempotency hotfix applied on
top. See [`EBAY_C0_COMPLIANCE.md`](EBAY_C0_COMPLIANCE.md) for the verified
contracts, the design decisions, the known limitations, and what
``payload_digest`` covers after C0.1.

---

## The release guard EBAY-C1 must satisfy

**No eBay data storage may ship without deletion coverage.** This is not a
convention — it is a test that fails.

EBAY-C0 established that *no table in this application stores eBay user data*,
and re-establishes it on every test run by searching the model layer
(`test_no_model_stores_an_ebay_user_identifier`). That is what makes today's
correct behaviour a **verified zero-match deletion**: a real notification is
authenticated, recorded and completed, and nothing is erased because there is
nothing to erase.

The moment EBAY-C1 adds a column that stores an eBay user identifier, that test
fails. Clearing it requires registering a data owner in `_OWNERS`
(`app/integrations/ebay/deletion.py`) **in the same change** that introduces the
storage. Every owner must:

* erase irreversibly — physical delete or irreversible anonymisation. A
  soft-delete does not satisfy eBay's requirement that *"even the highest
  system privilege cannot reverse the deletion"*;
* cover **every** tenant holding that person's data, not one workspace;
* invalidate any cache keyed on the erased data;
* be idempotent — eBay redelivers, and a second run must not fail;
* be safe when nothing matches.

Until an owner exists for it, eBay data persistence **is not production-ready**,
whatever else works.

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
   and must never gain a frontend input field. Per-seller OAuth tokens in
   EBAY-C1 are the opposite: encrypted, per-tenant, and never returned by an
   API — the same shape as `shopify_connections`.
6. **`dev_id` and `redirect_uri_name` are already declared** and unused. EBAY-C1
   consumes them rather than adding new settings.

---

## Not started, and deliberately so

EBAY-C0 implements no OAuth, no listing, no inventory, no orders and no
fulfilment. It also implements no opt-out flow: this application will persist
eBay data from EBAY-C1 onwards, so subscribing is the correct choice and the
portal's exemption route is not modelled.
