# EBAY-C0 — eBay credentials and Marketplace Account Deletion compliance

eBay requires every Developers Program application to subscribe to Marketplace
Account Deletion/Closure notifications, or to formally opt out, **before its
first production API call**. Until that is done the keyset stays inactive. This
phase builds that endpoint and the configuration it needs, and nothing else:
there is no eBay OAuth, no listing, no inventory, no orders.

---

## Repository audit

Done before any code was written, because the answers decide the design.

| Question | Finding |
|---|---|
| Existing eBay settings, models, routes, OAuth or dependencies? | **None.** `StorePlatform.EBAY` exists as an enum member and the frontend offers "eBay" in a store-platform dropdown, but nothing persists an eBay user, token, listing or order. |
| Public unauthenticated endpoint convention? | `POST /api/v1/integrations/shopify/webhooks/{topic}` and the AliExpress webhook. Both public, both verified by signature rather than session. |
| Raw-body signature verification? | `app/integrations/shopify/webhook.py` — reads `await request.body()` before parsing and verifies HMAC over those bytes. Reused as the pattern here. |
| Redis caching? | `app/core/redis.py`, `RedisPurpose.{CACHE,SESSION,RATE_LIMIT}`. Failures **degrade** rather than fail. Reused. |
| HTTP client / retry conventions? | `ShopifyGraphQLClient` — bounded retry on transient statuses, **never** on a non-idempotent operation. Reused verbatim as a rule. |
| Celery / outbox / idempotency? | Celery exists (`app/tasks/`), and M3A established durable idempotency via a DB row plus a conditional update. **No second queue was introduced** — see "Why no Celery task" below. |
| PII deletion / anonymisation service? | None existed. `app/integrations/ebay/deletion.py` is the first. |
| Unscoped repository exceptions? | `TenantRepository`, `AuthenticationUserRepository`, plus documented maintenance repositories. A third documented case is added here, with reasons. |
| Error envelope / rate limits? | `AppError` subclasses with stable `code` and `status_code`, translated once in `app/api/error_handlers.py`. Fixed-window limiter in `app/core/rate_limit.py`. Both reused. |

No second HTTP client, queue, cache abstraction or error style was created.

---

## Official contracts verified

Read on **23 August 2026**, not recalled.

### Challenge — [Marketplace User Account Deletion guide](https://developer.ebay.com/develop/guides-v2/marketplace-user-account-deletion)

> "eBay will send a challenge code to that URL in the form of a GET call. This
> GET call will use this format: `GET https://<callback_URL>?challenge_code=123`."

> "The three parameters must be hashed in the following order or the
> verification will fail: **challengeCode + verificationToken + endpoint**."

> "reply back to eBay with a 200 OK and the hashed value through a
> `challengeResponse` field in JSON format… The content-type header for this
> response must be set to 'application/json'."

> "We strongly advise that implementations use a JSON library to create their
> response body… a byte order mark (BOM) is often prepended… Since a BOM is
> considered invalid JSON, the receiving service issues a parse exception."

> "The verification token has to be between 32 and 80 characters, and allowed
> characters include alphanumeric characters, underscore (_), and hyphen (-)."

> "the provided endpoint URL should use the 'https' protocol, and it should not
> contain an internal IP address or 'localhost' in its path."

### Notification — same guide

Payload shape confirmed against the guide's table **and** its AsyncAPI contract:
`metadata.{topic, schemaVersion, deprecated}` and
`notification.{notificationId, eventDate, publishDate, publishAttemptCount,
data.{username, userId, eiasToken}}`. The contract describes `X-EBAY-SIGNATURE`
as an *"ECC message signature"*.

> "200 OK, 201 Created, 202 Accepted, and 204 No Content are all acceptable."

> "If signature verification fails, a HTTP status 412 - Precondition Failed is
> returned."

> "After a 24-hour period of multiple, unacknowledged notifications from a
> callback URL, the callback URL is marked down."

> "Deletion should be done in a manner such that even the highest system
> privilege cannot reverse the deletion."

### Public key — [getPublicKey](https://developer.ebay.com/api-docs/commerce/notification/resources/public_key/methods/getPublicKey)

> `GET https://api.ebay.com/commerce/notification/v1/public_key/{public_key_id}`
> … "replace the api.ebay.com root URI with api.sandbox.ebay.com"

> "This request requires an access token created with the client credentials
> grant flow" with scope `https://api.ebay.com/oauth/api_scope`.

> "The retrieved public key value should be cached for a temporary — but
> reasonable — amount of time (e.g., one-hour is recommended.)"

### Signature format — the part that had to be proven, not assumed

eBay documents *no* prose specification for the `X-EBAY-SIGNATURE` format; the
guide points at its Event Notification SDKs instead. So the SDK is the
specification, and it was read rather than remembered —
`eBay/event-notification-nodejs-sdk`, `lib/validator.js` and `lib/constants.js`:

```js
const xeBaySignature = getXeBaySignatureHeader(signatureHeader); // base64 → JSON
const publicKey = await client.getPublicKey(xeBaySignature.kid, config);
const verifier = crypto.createVerify(constants.ALGORITHM);       // 'ssl3-sha1'
verifier.verify(formatKey(publicKey.key), xeBaySignature.signature, 'base64');
```

Resolved and then **verified in Python against eBay's own published vector**
(`test/test.json` in the same repository) *before* the verifier was written:

| Element | Value |
|---|---|
| header | base64 of JSON `{alg, kid, signature, digest}` |
| `kid` | UUID, the `getPublicKey` path parameter |
| `signature` | base64 of a **DER**-encoded ECDSA signature |
| key | PEM `SubjectPublicKeyInfo`, **P-256**, delivered without newlines around the armour |
| digest | **SHA-1** — `ssl3-sha1` on an EC key means ECDSA-with-SHA-1 |
| signed bytes | the notification body exactly as received |

SHA-1 is eBay's choice, not one this codebase would make. It is safe in this
position — an attacker would need a second preimage of a body eBay signed, not
a colliding pair of their own — and it is called out in
`app/integrations/ebay/signature.py` so nobody "modernises" it to SHA-256 and
silently rejects every real notification.

**No live eBay request was made at any point.** Everything is fixtures and
`httpx.MockTransport`, and every test module says so.

---

## The endpoint

```
GET  /api/v1/integrations/ebay/marketplace-account-deletion
POST /api/v1/integrations/ebay/marketplace-account-deletion
```

Deployed as `https://api.whiteto.com/api/v1/integrations/ebay/marketplace-account-deletion`
— **no trailing slash**, matching the portal registration byte for byte.

Both are **public by protocol design**: eBay is not an authenticated user of
this API and cannot present a JWT. What replaces authentication is not nothing:

* GET proves endpoint ownership through a secret only eBay and this server
  share, and returns a digest rather than any stored data;
* POST proves origin cryptographically, against eBay's published key, over the
  exact bytes received, before a single field is read.

Plus body-size limits, content-type validation, a dedicated rate-limit budget,
and logging that never touches the payload.

### Trailing slashes

The registered URL is served directly with 200 on the first hop. Starlette still
offers its usual inbound tolerance for the slash spelling — `…-deletion/`
307s **to** `…-deletion` — which this phase neither adds nor removes. It points
at the registered URL, never away from it, and eBay never sends it. Asserted in
`test_the_registered_url_is_served_directly_with_no_redirect`.

---

## Configuration

`EbaySettings`, prefix `EBAY_`, composed into `Settings` as `settings.ebay`.

| Variable | Notes |
|---|---|
| `EBAY_ENVIRONMENT` | `sandbox`\|`production`. Chooses the Notification API host and nothing else. |
| `EBAY_CLIENT_ID` | Used for the client-credentials token. |
| `EBAY_CLIENT_SECRET` | `SecretStr`. |
| `EBAY_DEV_ID` | Declared now, unused until EBAY-C1. |
| `EBAY_REDIRECT_URI_NAME` | Same. |
| `EBAY_MARKETPLACE_DELETION_ENDPOINT` | Validated for shape; **never normalised**. |
| `EBAY_MARKETPLACE_DELETION_VERIFICATION_TOKEN` | `SecretStr`, 32-80 chars, `[A-Za-z0-9_-]`. |

Enforced at configuration load, so a mistake is a boot failure with a clear
message rather than a silent portal rejection days later:

* token length and character set (eBay's rule, verbatim);
* endpoint must be `http(s)`, name a host, and carry no userinfo, query,
  fragment or surrounding whitespace;
* **no normalisation** — adding or removing a trailing slash would silently
  change the challenge hash, which is the exact failure this setting exists to
  prevent;
* in a deployed environment: HTTPS required, and localhost / private / loopback
  / link-local / reserved IP literals refused.

Incomplete configuration **fails closed**: the challenge raises rather than
hashing an empty token, which would produce a well-formed digest that eBay
rejects while telling the operator nothing.

No frontend field was added. These are platform credentials, not merchant
settings, and no response schema in this codebase can hold one.

---

## GET — challenge

`SHA256(UTF8(challengeCode) + UTF8(verificationToken) + UTF8(exactConfiguredEndpoint))`,
lowercase hex, returned as `{"challengeResponse": "…"}` with 200 and
`application/json`, serialised by FastAPI's real JSON encoder (no BOM).

The endpoint in the hash is the **configured string**. `challenge_response()`
takes only the code — there is no parameter through which a request could supply
a host, which is the structural reason a spoofed `Host` or `X-Forwarded-Host`
cannot influence the answer. Tested with both headers spoofed.

Missing, blank and oversized codes are refused. The token never appears in a
response, an error or a log line.

---

## POST — notification

Order is the security property: **verify → parse → claim → erase → acknowledge**.

1. Read raw bytes; refuse anything over **64 KiB** (eBay's sample is ~500 bytes).
2. Require `application/json`.
3. Require and decode `X-EBAY-SIGNATURE`; extract only `kid`, validated as a
   `uuid.UUID`.
4. Mint a client-credentials token, fetch the key from eBay's **fixed** host,
   cache it in Redis for one hour.
5. Verify ECDSA over the **exact raw body**, using the algorithm and digest from
   `getPublicKey` — not from the attacker-supplied header. A header that
   disagrees with the key metadata is a rejection, so a forged header cannot
   downgrade the digest.
6. Parse and validate the schema; unknown topic or schema version fails closed.
7. Insert the ledger row; the UNIQUE constraint arbitrates duplicates.
8. Run the deletion processor.
9. **204 No Content.**

| Outcome | Status |
|---|---|
| Verified, processed | `204` |
| Signature invalid / schema rejected | `412` (eBay's documented rejection code) |
| Body too large | `413` |
| Key service, OAuth or database unavailable | `5xx` — eBay resends |

An unverifiable notification is **never** acknowledged. eBay retries for 24
hours, so a retryable failure costs a delay; acknowledging early costs a
person's deletion request permanently, because eBay never resends an
acknowledged notification.

### SSRF and traversal

Prevented by construction rather than by a filter. The host is one of two
constants chosen by environment; the only variable in the path is a parsed
`uuid.UUID`, a type that cannot represent a slash, a scheme or a host. Tested
with `../../etc/passwd`, `https://attacker.test/key` and similar.

### Retries

Both eBay calls are reads, so a bounded retry (3 attempts, 429/5xx and transport
errors only) is safe. 401 and 404 are **not** retried — repeating them only
burns the call quota eBay warns about. There is no non-idempotent operation in
this module, and the rule is written down so a later one cannot inherit the
policy by accident.

---

## Ledger and migration

Migration **0029**, additive, single head. `alembic heads` → `0029`.

One table, `ebay_compliance_notifications`, holding **no personal data at all**.
eBay's payload carries `username`, `userId` and `eiasToken`; none has a column.
A table that cannot hold personal data cannot leak it, and cannot itself become
something that must be erased on the next request. `payload_digest` (SHA-256 of
the received bytes) supplies identity without content.

`notification_id` is **UNIQUE**, and that constraint *is* the idempotency
mechanism. An application-level check-then-insert leaves a window where two
concurrent deliveries both see nothing; the database does not. Proven by
`test_concurrent_duplicate_deliveries_process_once`, which runs two full
deliveries on separate connections.

Deliberately **not** tenant-scoped: an eBay deletion is an instruction about a
person, who may have data under several workspaces or none.
`EbayComplianceLedgerRepository` is therefore the third documented unscoped
repository — reachable only from this public, signature-verified receiver, with
four methods all keyed by eBay's own notification id.

Downgrade drops the table then the enum types, in that order (an enum still
referenced by a column cannot be dropped, and orphan types make a re-upgrade
fail on `CREATE TYPE`). The full down/up cycle is exercised, not asserted.

---

## Deletion processor and the eBay-data audit

**No table in this application stores eBay user data.** Verified by searching
the model layer, and re-verified on every test run by
`test_no_model_stores_an_ebay_user_identifier` — not asserted in prose.

So the correct EBAY-C0 behaviour is a **verified zero-match deletion**: the
notification is authenticated, recorded, and completed with
`erased_record_count = 0` and `outcome_code = "no_matching_data"`. That is not a
stub. `test_the_processor_runs_every_registered_owner` registers a temporary
owner and proves the count it reports reaches the ledger — without it, "erased
0" would be indistinguishable from a processor that never calls anything.

### The release guard for EBAY-C1

`_OWNERS` in `app/integrations/ebay/deletion.py` is the single registration
point. It is empty, and it must gain an entry **in the same change** that
introduces eBay data storage. This is enforced, not requested: the moment a
model gains an eBay-identifying column with no owner covering it,
`test_no_model_stores_an_ebay_user_identifier` fails. A future author cannot
ship eBay OAuth or eBay data persistence and describe it as production-ready
while that test is red.

### Erasure, not soft deletion

Every owner must physically delete or irreversibly anonymise. `deleted_at` is
not erasure — a row that can be undeleted has not been erased, and eBay's
requirement is explicit that even the highest system privilege must not be able
to reverse it. No legal-retention exception is claimed, because none applies to
data this application does not hold; inventing one would be worse than useless.

---

## Why no Celery task

The brief permits a queue and forbids a second one. Neither was needed. The
erasure and the ledger row commit in a **single transaction**, so there is no
window in which the ledger claims completion for work that rolled back — which
would be permanent, since eBay never resends an acknowledged notification.
Handing the work to a broker would *add* that window and require proving
broker-failure safety to close it again. If a future owner makes erasure slow
enough to need a worker, the durable receipt this phase adds is exactly the
handoff record that would make it recoverable.

---

## Observability

Structured events, all free of payload content:
`ebay_challenge_answered`, `ebay_challenge_rejected` (reason category),
`ebay_notification_signature_verified` / `_rejected` (reason category),
`ebay_notification_duplicate`, `ebay_notification_processed`,
`ebay_notification_rejected`, `ebay_public_key_cache_hit` / `_miss`,
`ebay_key_service_unavailable`, `ebay_deletion_processed`.

Never logged: the verification token, client secret, access token, raw
signature, raw body, `username`, `userId`, `eiasToken`. Asserted by
`test_no_secret_or_identifier_reaches_the_logs`, which captures at DEBUG and
searches for each value.

---

## Known limitations

1. **No live eBay verification.** No developer credentials, no registered
   endpoint, and none is permitted in this pass. Everything is proven against
   eBay's own published vector and mocked transports. The endpoint has never
   answered a real eBay challenge.
2. **The production verification token does not exist yet.** It is generated and
   installed after this work is deployed. A previously exposed token is treated
   as permanently compromised and appears nowhere in this repository.
3. **Zero-match deletion is correct only while nothing stores eBay data.** True
   today and checked on every run; EBAY-C1 changes it.
4. **SHA-1 is eBay's digest choice.** Documented rather than silently
   "improved".
5. **`dev_id` and `redirect_uri_name` are declared but unused** until EBAY-C1.
6. **Rate-limit budget is a judgement, not a measurement.** 600/minute for this
   path, chosen because eBay's real volume is unknown and the general 100/minute
   quota would certainly be too low. Revisit with real traffic.
7. **No opt-out path is implemented.** This application will persist eBay data
   from EBAY-C1, so subscribing is the correct choice; the opt-out route in
   eBay's portal is not modelled here.
