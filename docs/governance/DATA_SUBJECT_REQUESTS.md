# Data subject requests — runbook

Owner: **DESIRLY LIMITED privacy operations**, `privacy@whiteto.com`.
Operational contact: Adnan Zulfiqar.

**Statutory deadline: one month from receipt.** Acknowledge promptly, record the
received date immediately, and treat the month as the deadline rather than the
target.

This document was referenced by the C1.2 commit and did not exist. That was a
review finding, and this is it.

---

## First: which subject is this?

The single most important step, and the one the first implementation got wrong
by collapsing all three into one command.

| Who is asking | Scope | What to do |
|---|---|---|
| Someone with a DropPilot AI login | **platform-user** | Run the tool, scope `platform-user` |
| A merchant closing their account entirely | **workspace** | Run the tool, scope `workspace` |
| **A shopper who bought from one of our merchants** | **none** | **Refer them to the merchant.** See below |

### Why a shopper's request is referred, not executed

Where a merchant syncs their sales channel, DESIRLY LIMITED holds that buyer's
details **on the merchant's instructions**. The merchant is the controller and
the buyer's relationship is with them.

There is also a hard technical constraint. `orders` carries `buyer_name`,
`recipient_name`, `recipient_phone` and an address, and **no buyer identifier at
all** — no email, no external buyer id, no customer id. A buyer could only be
matched on a name, and erasing every order matching "J. Smith" would destroy a
different customer's records.

**This is a launch blocker, recorded in [README.md](README.md).** Exact,
merchant-scoped buyer erasure needs a stable buyer identifier on `orders` first.
Until then:

* Reply to the shopper explaining that the merchant is the controller, and give
  them the merchant's identity if the merchant permits it.
* If the **merchant** instructs us to erase a specific order's buyer details, do
  it manually against that order id, record the instruction, and note it here.
* Never approximate. `test_neither_scope_touches_buyer_fields` exists to keep
  that true.

### The Shopify exception: redaction by exact order id

Shopify's mandatory privacy webhooks are different in kind from a request
that arrives by email, and are handled automatically
(`app/integrations/shopify/compliance.py`, since 2026-10-05):

* **`customers/redact`** names the Shopify order ids to redact. DropPilot
  stores that id as `orders.external_id`, so the match is exact and limited
  to the one workspace and store the shop belongs to. The buyer's name,
  phone and address lines are blanked on those orders; the country code is
  kept. With no order ids listed, nothing is touched.
* **`shop/redact`** (48 hours after uninstall) blanks the buyer details on
  every order of that store. The connection and token were already deleted
  on uninstall.
* **`customers/data_request`** is **referred**: the workspace receives an
  in-app notification with the customer and order ids, and the merchant,
  as controller, answers the customer through Shopify. The notification
  never carries the customer's email or phone.

Every delivery is HMAC-verified and replay-protected like any other
Shopify webhook, and each outcome is logged with ids and counts. There is
no separate ledger table: the notification row (for a data request) and the
log line are the record. If a durable receipt per redaction is required for
an audit, that is a schema change to propose, not something to improvise.

`tests/integration/test_shopify_gdpr_webhooks.py` proves the exact-id
scope, including that another workspace's order with the same Shopify id
is untouched.

---

## 1. Verify who is asking

Do not act on an unverified request. An erasure carried out for an impersonator
is a data breach with extra steps, and one done for a disgruntled ex-colleague
is worse.

* Confirm the request comes from the address on the account, or verify through a
  route already associated with it.
* For a **workspace** closure, confirm the requester can actually authorise it —
  closing a workspace erases every member, not just them.
* Record how verification was done. Do not keep more identity evidence than the
  verification needed.

## 2. Establish the tenant

Every scope requires a tenant id. This is deliberate: the same address can exist
in two workspaces — a consultant with accounts at two clients is ordinary — and
the tool refuses to guess.

Find the tenant from the account, the support thread, or by asking the
requester which workspace they mean. **Never** resolve by address alone.

## 3. Rehearse

Dry run is the default. It reads and counts, and writes nothing.

```bash
cd backend
python scripts/erase_data_subject.py platform-user \
    --tenant-id <uuid> --expect-database <database>
```

The tool prompts for the address without echoing it. Pass `--user-id` instead
when the request already identifies the account, and skip the prompt.

`--expect-database` is mandatory. The tool asks the server `SELECT
current_database()` and refuses if the answer differs. **Configuration is not
identity** — the connection URL is the same string that was already wrong if
someone is in the wrong directory.

## 4. Review the plan

The output names the scope, the confirmed database, the target, and a count per
category. Check the shape is one person's account. If a `platform-user` run
reports thousands of rows, stop and investigate before continuing.

## 5. Execute

```bash
python scripts/erase_data_subject.py platform-user \
    --tenant-id <uuid> --expect-database <database> --apply
```

You will be asked to type `<scope> <tenant-id> <database>` exactly. A production
database name additionally requires `--i-understand-production`; without it the
tool refuses regardless of `--apply`.

The database work happens in **one transaction**. Any exception rolls the whole
thing back, so a failure part way through leaves nothing behind — proven by
`test_a_failure_part_way_through_leaves_nothing_behind`, which injects a failure
after the fourth statement and checks every earlier mutation is restored.

Exit codes: **0** done, **1** subject not resolved, **2** refused or rolled
back, **3** database erasure complete but cache cleanup pending (see section 6).

### What `platform-user` does

| Deleted | Cleared (row kept, person unlinked) |
|---|---|
| refresh tokens | ai_prompts, prompt_executions |
| email verification tokens | inventory_sync_runs, order_sync_runs |
| role grants (physically — a soft-deleted grant still grants) | price_changes, global_rule_versions |
| **Google/provider identities** | — |
| notifications | product_imports, product_versions, rule_applications |
| | stores, shopify/aliexpress/**ebay** connections |

Then the user row is anonymised: address replaced with
`erased-<id>@erased.invalid`, names and password hash cleared, account
deactivated. The row survives because orders, products and audit rows reference
it; a cascade would destroy a business's records to satisfy one person's request.

**Colleagues, orders and marketplace credentials are untouched.** Clearing
`ebay_connections.user_id` unlinks the person while the workspace keeps its
connection.

### What `workspace` does

Everything above for **every member**, and then deletes the workspace's Shopify,
AliExpress and **eBay** connections outright, encrypted credentials included.
This is the only scope permitted to destroy credentials.

## 6. Redis

The tool performs the cleanup itself, **after** the database transaction has
committed, and prints what it did. Nothing here needs to be run by hand.

### The real key inventory

| Key | Database | TTL | Erasure |
|---|---|---|---|
| `login:email:{sha256(normalised_address)[:32]}` | RATE\_LIMIT | 300 s, extended to 900 s once the attempt limit is reached | **Deleted** on `platform-user`, when the subject was resolved by address |
| `login:ip:{sha256(client_ip)[:32]}` | RATE\_LIMIT | 300 s / 900 s, as above | **Not deleted** — see below |
| `ratelimit:ip:{client_ip}` | RATE\_LIMIT | 60 s window | **Not deleted** — same reason |
| `t:{tenant}:*` | CACHE | 300 s default per entry | **Cleared** on `workspace`, via `CacheClient.invalidate_tenant` |
| `*:oauth:state:*` | CACHE | 600 s | Not deleted; expires, and holds no personal data |

**Why the IP keys are left alone.** An address is not a person. It is shared by
everyone behind a NAT, reassigned by ISPs, and one subject may have signed in
from many. Deleting "their" IP counter would mean guessing which addresses were
theirs and lifting throttling for whoever else is behind them. They also need no
erasure step, because they expire within 900 seconds at the outside.

> **Correction.** An earlier version of this runbook described the IP counter as
> `ratelimit:ip:<address>` and did not mention `login:ip:{hash}` at all. Both
> exist, both are hashed, and both are in the table above. The address itself
> never appears in a key.

### Ordering, and what happens when Redis is down

PostgreSQL and Redis cannot commit together, so the tool sequences them and says
so rather than pretending otherwise:

1. The erasure runs and **the database transaction commits**.
2. Only then is Redis touched. If the database failed, Redis is never called —
   there is nothing to invalidate.
3. If Redis then fails, **the erasure still happened and is still correct.** The
   tool prints `database erasure complete; cache cleanup pending` and exits
   **3**, so nobody records the request as finished.

**Retrying is the fix, and it is safe.** Re-run the identical command: the
database work is idempotent and will report zero counts, and the cache step runs
again regardless of those counts. That last part is deliberate — skipping the
cache when nothing was deleted would leave a stale namespace forever after an
outage.

Nothing uses `FLUSHDB`, which would destroy every tenant's cache to clean up
one. `invalidate_tenant` walks the namespace with `SCAN`, not `KEYS`, which
blocks Redis for a full keyspace walk. Both are available on the deployed Redis
3.0.504, and the tests run against that exact version.

## 7. If it fails

The transaction rolls back automatically and the tool says so. Nothing is
partially erased. Investigate, fix, and re-run — the operation is **idempotent**,
so a second run reports zeroes rather than failing.

There is no undo for a *successful* run, and **no backups exist** to restore
from ([BACKUPS.md](BACKUPS.md)). That is why dry run is the default.

## 8. Record and respond

Record: scope, tenant id, user id, date received, date completed, how identity
was verified, and the counts the tool printed.

**Do not record the address itself.** The tool never writes it to the
application log, and a request register that reintroduces it defeats the
erasure. The user id is sufficient to identify the record.

Tell the person what was removed and what was kept, and why. The tables above
are the honest answer.

---

## Access, rectification, portability

The tool covers erasure. The other rights are manual today:

* **Access** — assemble from the workspace UI and, where necessary, direct
  queries scoped to the tenant. Do not include other members' data.
* **Rectification** — the user can correct their own profile; anything else is a
  scoped update.
* **Portability** — no export feature exists. Assemble manually, in a structured
  format, limited to data the person provided.

Building these into the product is not yet scheduled.
