# Privacy policy — unresolved operator decisions

> **Superseded in part by EBAY-C1.2.** This document is kept as the C1.1 audit
> record. The current position lives in [`docs/governance/`](../governance/),
> which supersedes every row below.
>
> **Correction.** Row 4 originally named `AWS_REGION`. The setting uses the
> `S3_` prefix, so the variable is `S3_REGION`. The substance — a region default
> of `eu-west-1` on a storage integration that no code uses — was correct;
> the variable name was not. Found by independent review, corrected here.
>
> Resolved in C1.2: rows 1, 2, 4 (controller identity, address, hosting
> country), and the log-retention and backup positions. Rows 3, 5, 6, 8, 9, 10
> and 11 remain open and are tracked in
> [`docs/governance/README.md`](../governance/README.md#blockers-before-public-launch).

The published policy at `/privacy` was written from the implementation. Every
factual claim in it is traceable to code. This document lists what the code
**cannot** answer, so nobody mistakes an accurate technical description for a
complete legal one.

**No legal review has taken place.** Nothing here has been seen by a solicitor
or a data protection adviser. This is an engineer's audit of what the software
does and what the policy therefore cannot claim.

Nothing in this table may be filled in by guessing. Each row needs a decision
from whoever controls the deployment.

---

## Decision table

| # | Required fact | Status in code | What the policy currently says | Decision needed |
|---|---|---|---|---|
| 1 | **Legal / controller identity** | Not encoded anywhere. `PROJECT_NAME=DropPilot AI` is a display string | Identifies only the trading name "DropPilot AI" | Registered legal entity, company number, and whether it is controller or processor for each data category |
| 2 | **Business / service address** | Absent | No postal address at all | A real registered address. UK GDPR Art. 13 requires controller identity and contact details |
| 3 | **ICO registration** | Absent | Not mentioned | Registration number, or a documented reason none is required |
| 4 | **Hosting country / region** | Not fixed by the software. `StorageSettings` uses the `S3_` prefix, so the region variable is **`S3_REGION`** (default `eu-west-1`) — and **no code uses S3** | "a deployment decision and is not fixed by the software" | **RESOLVED in C1.2: United Kingdom.** Now stated in the notice |
| 5 | **International-transfer safeguards** | None implemented — no transfer mechanism exists in code | Describes marketplaces operating internationally under those providers' own terms | Whether an SCC, IDTA or adequacy route is relied on, and for which recipient |
| 6 | **Lawful basis per purpose** | Not determinable from software | Lists four *candidate* bases, then defers the mapping | A definitive purpose-to-basis mapping (table below) |
| 7 | **Retention period per category** | Only two enforceable periods exist: `access_token_ttl_minutes=15`, `refresh_token_ttl_days=30` | "We have not yet fixed a published retention period for every category of data" | A period or an enforceable criterion per category (table below) |
| 8 | **Subprocessors / recipients** | Categories are provable; named vendors are not | Lists five recipient *categories* | A published subprocessor list with names, roles and locations |
| 9 | **Backup retention** | **No backup subsystem exists in the application.** The only match for "backup" is an incidental comment in `ebay/oauth.py:111` | **Silent — the policy does not mention backups at all** | Whether backups exist at the infrastructure level, their retention, and whether erasure reaches them |
| 10 | **Log / audit retention** | **No retention or rotation policy is implemented.** `logging.py:43` mentions a retention policy only as a hypothetical | States what logs contain; says nothing about how long they are kept | A log retention period, and where logs are aggregated |
| 11 | **Account-deletion fulfilment** | `soft_delete` and `hard_delete` exist on the base repository. **No self-service deletion endpoint exists** | "deletion is currently handled on request rather than through a self-service control" | Who fulfils it, within what SLA, whether it is hard or soft deletion, and whether it reaches backups (row 9) |

---

## Purpose to lawful basis (row 6)

The policy deliberately does not assert this mapping. It must be decided.

| Processing purpose | Evidence it happens | Proposed basis — **needs approval** |
|---|---|---|
| Creating and running a workspace account | `app/models/user.py`, `tenant.py` | Contract |
| Authentication and session management | `refresh_token.py`, `app/core/password.py` | Contract |
| Marketplace connection (eBay/Shopify/AliExpress) | `app/models/ebay.py`, `shopify.py`, `integration.py` | Contract, or Consent for the authorisation step |
| Storing buyer/recipient order data | `app/models/order.py` — `buyer_name`, `recipient_phone`, address fields | Contract — **but see the controller/processor question below** |
| Security, abuse prevention, fault diagnosis | `app/middleware/request_context.py` | Legitimate interests — needs a documented balancing test |
| Responding to eBay account-deletion notifications | `app/integrations/ebay/deletion.py` | Legal obligation |
| Optional AI listing generation | `app/ai/` — defaults to `STUB`, sends nothing | Contract, only when an operator configures a real provider |

**Unsettled and material:** where order sync runs, the operator is processing
*their customers'* personal data through this service. That makes the operator a
controller and DropPilot AI a processor, which requires a written processing
agreement — not a privacy policy clause. This is not resolvable in the policy.

---

## Retention per category (row 7)

| Category | Enforceable period in code? | Policy wording today |
|---|---|---|
| Access tokens | **Yes** — 15 minutes | "held in memory only" |
| Refresh tokens | **Yes** — 30 days, hashed, revoked on sign-out | "expire automatically; signing out revokes them" |
| eBay OAuth state | **Yes** — `EBAY_OAUTH_STATE_TTL_SECONDS`, 600s default | not mentioned |
| Account and workspace data | **No** — soft delete only, no purge job | "retained while your account is open" |
| Marketplace connections | Event-driven, not time-based | "retained until you disconnect them" |
| eBay compliance ledger | **No period.** Retained indefinitely by design | "retained as compliance evidence" — **no period stated** |
| Order and buyer data | **No** | not addressed separately |
| Operational logs | **No** | contents described; duration not |
| Backups | **Nothing exists in code** | not mentioned |

---

## Exact sentences that remain unsafe, incomplete or unsupported

Quoted verbatim from the live page. These are accurate as engineering
statements; they are incomplete as legal ones.

1. > "Which basis applies to a specific processing activity depends on
   > circumstances we cannot determine from the software alone."

   Honest, and **not an adequate Art. 13 disclosure**. A controller must state
   the basis, not defer it. Blocking for publication.

2. > "We have not yet fixed a published retention period for every category of
   > data."

   Truthful disclosure of a gap, not a compliant retention statement. Blocking.

3. > "The hosting location of a particular DropPilot AI deployment is a
   > deployment decision and is not fixed by the software."

   Offering disclosure on request is weaker than stating the location. The
   sentence that follows it also makes a **promise a human must keep** —
   someone must actually answer those emails.

4. > "deletion is currently handled on request rather than through a
   > self-service control in the application"

   Accurate, but names no fulfilment owner and no time limit. UK GDPR expects
   one month. Nobody is currently assigned to this.

5. > "Infrastructure — the hosting, database and cache services the deployment
   > runs on."

   A recipient *category* standing in for named subprocessors.

6. > "The marketplaces and any optional providers described in section 7 operate
   > internationally, so connecting an account may involve your data being
   > processed outside the United Kingdom or the European Economic Area"

   Describes *their* safeguards and asserts none of our own. If DropPilot AI is
   a controller for that transfer, this is insufficient.

7. > "At the time of writing, the service implements no analytics, advertising,
   > tracking or third-party error-reporting integrations."

   **Currently true and verified** by repository search. Flagged only because it
   is the sentence most likely to become false without anyone noticing — adding
   an error reporter silently invalidates it. `app/error.tsx` already carries a
   comment marking where one would go.

8. **Omission, not a quote — backups.** The policy is silent on backups. If
   infrastructure-level backups exist, then "permanently deletes" (section 4,
   eBay disconnect) is true of the live database but possibly not of every copy.
   This is the most likely place for the policy to be *wrong* rather than merely
   incomplete.

9. **Omission — log retention.** Section 9 says what logs contain but never how
   long they are kept. Those logs include IP addresses, which are personal data.

---

## What was deliberately not changed

Wording was improved only where the underlying fact was already established in
code. No lawful basis, retention period, address, entity name, hosting region,
safeguard or subprocessor was invented to fill a gap, and no claim of legal
review was added.
