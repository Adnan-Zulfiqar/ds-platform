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

Everything happens in **one transaction**. Any exception rolls the whole thing
back, so a failure part way through leaves nothing behind — proven by
`test_a_failure_part_way_through_leaves_nothing_behind`, which injects a failure
after the fourth statement and checks every earlier mutation is restored.

### What `platform-user` does

| Deleted | Cleared (row kept, person unlinked) |
|---|---|
| refresh tokens | ai_prompts, prompt_executions |
| email verification tokens | inventory_sync_runs, order_sync_runs |
| role grants (physically — a soft-deleted grant still grants) | price_changes, global_rule_versions |
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

The tool prints what applies.

| Key | Action |
|---|---|
| `login:email:<sha256(address)[:32]>` | **Exactly addressable** — delete it. The throttle hashes the address, so no scan or pattern is needed |
| `ratelimit:ip:<address>` | **Cannot be attributed** to a subject; it is keyed by client IP. Expires within 60 seconds, so it self-resolves |
| `t:<tenant>:*` | Workspace cache namespace, cleared by the existing tenant invalidation on closure |

Never use `FLUSHDB`, and never a pattern that is not already tenant-scoped. The
deployed Redis is 3.0.504, so anything newer than that vintage is unavailable
anyway.

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
