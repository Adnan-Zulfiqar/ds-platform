# Encrypted database backups — threat model, tooling and runbook

**Status: the tooling exists and has been proven on isolated databases. No
production backup has ever been taken.** Nothing in this document should be
read as a claim that production data is protected today. It is not.
[docs/governance/BACKUPS.md](../governance/BACKUPS.md) states the live position.

---

## 1. What this exists to survive

A backup is the least interesting file in the system until the day it is the
only file in the system. The design below is driven by the failures that
actually destroy companies, not by the ones that are easy to code for.

| Threat | What happens without a control | Control |
|---|---|---|
| **Database loss** — a bad migration, an accidental `DROP`, corruption | 1,268 tenants' workspaces gone with no route back | Scheduled `pg_dump` in PostgreSQL's custom format, restorable by `pg_restore` |
| **Disk or host failure** | The backup dies with the database it was protecting | `BACKUP_DIRECTORY` must be a volume other than the database's, and an off-site copy is required before launch. **Neither is configured today** |
| **Accidental deletion of a backup** | The archive silently thins until an incident finds it empty | Retention refuses to delete the newest verified backup, refuses to delete the only one, and rehearses by default |
| **Ransomware, or an attacker deleting backups** | Encrypted-in-place backups are worthless; deleted ones more so | Off-site copy with **write-once or separately-credentialed** access. **Not configured.** Local ACLs restrict the directory to one account, which is a speed bump, not a control |
| **Corrupt or truncated backup** | Discovered during the restore, when it is too late to take another | Chunked AEAD with a final-chunk flag: a stream that ends early is *refused as truncated*, not accepted as short. Both plaintext and ciphertext digests recorded and checked |
| **Wrong-database backup or restore** | Staging data poured over production, or a "backup" of an empty database | `SELECT current_database()` confirmed against a mandatory `--expect-database`; the manifest records the source, and restore checks it against `--expect-source` |
| **Secret leakage** | The dump password in a scheduler log, a DSN in a manifest, a key in a crash dump | Password passed by `PGPASSFILE`, never `argv` and never the environment; manifest built from a closed field set and scanned before publication; tool output filtered |
| **Unauthorised restore** | An attacker with the backup file reads every customer's data | AES-256-GCM envelope encryption; the file is useless without the key, which never touches this repository |
| **Retention failure** | Indefinite retention of everything ever deleted — a UK GDPR problem, not a disk problem | Grandfather-father-son policy, enforced and reported by name |
| **Silent scheduled-job failure** | Nine months of "backups" that never ran | The last-success marker is authenticated and written **only** after a full success; the readiness check fails on its age |
| **Restoring into production by accident** | The recovery destroys what it was recovering | `droppilot` refused outright; `--production-restore` plus a typed database name; refuses entirely without a terminal |
| **The backup itself is a data-protection liability** | One portable file with every customer's personal data and every encrypted marketplace credential | Encrypted at rest by default with no unencrypted mode; UK residency required; retention bounds how long erased data survives in it |

### The one that is easy to get wrong

A backup contains **customer personal data** (merchant accounts, their
customers' order records) **and the ciphertext of marketplace credentials**
(Shopify, AliExpress and eBay tokens, encrypted with `SECURITY_ENCRYPTION_KEYS`).
Those two facts have opposite consequences:

* the personal data means the backup is a UK GDPR asset in its own right — it
  needs a retention period, a residency answer and a deletion story;
* the encrypted credentials mean **the backup key and the application's
  encryption keys must never be the same key, and should not be in the same
  custody**. An attacker with both has live marketplace access, not merely a
  historical read. `app/core/backup_crypto.py` refuses a backup key that
  matches `SECURITY_SECRET_KEY`, `SECURITY_OTP_HMAC_KEY` or any entry of
  `SECURITY_ENCRYPTION_KEYS`, by value and by decoded bytes.

---

## 2. Ownership and policy

| Question | Answer | Settled? |
|---|---|---|
| **Owner** | The operator running the production host — currently the sole director. There is no second person, and that is itself a risk worth writing down: a single point of failure for the key, the schedule and the drill | Stated, not delegated |
| **Approved destination** | A dedicated directory on a volume **other than the database's**, plus an off-site encrypted copy | Local: designed. Off-site: **not configured, not chosen, not purchased** |
| **UK residency** | Backups must stay in the United Kingdom, matching the production server, or the transfer needs a documented basis and an update to the privacy notice | Requirement recorded. No provider selected, so no transfer exists to assess |
| **Encryption** | AES-256-GCM, envelope: a fresh random data key per backup, wrapped by a long-lived key from configuration. Section 3 | Implemented |
| **Key custody** | `BACKUP_ENCRYPTION_KEY` lives in the operator's secret store and nowhere else. **It must not be stored beside the backups** — an attacker who takes the archive must not take the key with it. A sealed offline copy is required, because a lost key makes every backup permanently unreadable | Requirement recorded. **No secret store is in place** |
| **Key rotation** | Annually, or immediately on suspicion. Rotation re-wraps: old backups keep their old key, so **retiring a key means retiring the backups it opens**. Both keys must be kept until the last backup under the old key ages out | Designed. Never performed |
| **Retention** | Proposed: 14 daily, 8 weekly, 12 monthly. Section 5 | **Proposed for review. Not legally settled** |
| **Restore-test frequency** | Quarterly at minimum, and after any schema change that alters restore behaviour. `BACKUP_MAX_DRILL_AGE_DAYS` defaults to 90 and the readiness check fails past it | Designed. One drill performed, on isolated databases only |
| **Monitoring** | The scheduled task's exit code, plus the last-success marker's age. Section 6 | Designed. **No alerting is wired up** |
| **Escalation** | A failed or missing backup is a same-day operator action, not a ticket. Two consecutive failures should stop the deployment of anything else until resolved | Stated |
| **Deletion after restoration** | A person erased today still exists in yesterday's backup. Backups are **put beyond use** rather than edited, and completed erasures are **replayed after any restore** before the system returns to service | Requirement recorded; the replay list does not exist yet. See [RETENTION_AND_ERASURE.md](../governance/RETENTION_AND_ERASURE.md) |

---

## 3. Encryption and key design

### The container

```
DPBKUP\x00\x01            8-byte magic — a wrong file is rejected immediately
uint32                    header length
header                    canonical JSON: version, key id, chunk size,
                          wrapped data key, wrap nonce, stream prefix, backup id
repeated:
  uint32                  chunk ciphertext length
  chunk                   AES-256-GCM over one plaintext chunk
```

**Envelope, not direct encryption.** Each backup gets a fresh random 256-bit
data key; only that key is wrapped by `BACKUP_ENCRYPTION_KEY`. Rotating the
long-lived key therefore re-wraps a few hundred bytes per file instead of
re-encrypting the archive — which is the difference between rotation being
policy and rotation being fiction. It also means no single key ever encrypts
more than one file, so GCM's nonce budget is never a consideration.

**STREAM framing, not one-shot AEAD.** A one-shot `encrypt()` needs the entire
dump in memory twice, and gives no way to notice that a file stops early. So
the plaintext is split into 4 MiB chunks, each authenticated, with the nonce
built as `7-byte random prefix ‖ 4-byte counter ‖ final flag`. Chunks cannot be
reordered, duplicated, dropped, or moved between backups, and a stream that
ends without its final-flagged chunk is refused as truncated. Bytes appended
after the final chunk are refused too.

**The header is the associated data** for every chunk, so the wrapped key, the
key identifier and the chunk size cannot be edited or swapped in from another
file.

**The manifest is authenticated separately**, with HMAC-SHA256 under a subkey
derived from the backup key by HKDF. That lets a verifier reject a tampered
manifest before decrypting anything, and means the manifest and the ciphertext
cannot be mixed and matched.

### The key

* base64-encoded, exactly 32 bytes decoded;
* refused if absent, malformed, a repeating pattern, or **published in this
  repository**;
* refused if it equals `SECURITY_SECRET_KEY`, `SECURITY_OTP_HMAC_KEY` or any
  `SECURITY_ENCRYPTION_KEYS` entry — by string and by decoded bytes, so a
  differently-encoded copy of the same key is caught;
* identified in manifests and reports by a **key id**: 64 bits of HKDF output
  over the key. It names which key a file needs and narrows nothing.

Generate one — on the operator's machine, never in this repository:

```bash
python -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

---

## 4. The tools

All four live in `backend/scripts/` and share their judgement with
`app/services/database_backup.py`, so they cannot disagree about what a valid
backup is.

| Exit | Meaning |
|---|---|
| 0 | Succeeded |
| 1 | Usage error — a bad flag. **Never a verdict about a backup** |
| 2 | Configuration or identity refused. Nothing was read or written |
| 3 | `pg_dump` failed |
| 4 | Encryption failed |
| 5 | Verification failed — the backup must be treated as absent |
| 6 | The filesystem refused, or a protection could not be applied |
| 7 | The restore, or a check after it, failed |

### `create_database_backup.py`

```bash
python scripts/create_database_backup.py --expect-database droppilot_staging
```

**The connection comes from `POSTGRES_*` configuration; `--expect-database` is
the claim it is checked against.** The two are compared, and neither overrides
the other. A tool that connected to the name the operator expected would get
agreement by construction and detect nothing — and the failure being guarded is
a configuration file pointing somewhere unintended.

Confirms the database by asking the server, dumps in custom format, streams it
through authenticated encryption into a `.part` file, and publishes atomically:
the ciphertext is renamed into place only after `pg_dump` exits zero and the
final chunk is sealed, and **the manifest is renamed last** because every other
tool reads the manifest's presence as "complete". If the manifest cannot be
published, the ciphertext already renamed is removed — a backup nothing can
verify is worse than an absent one, because it gets counted.

Any failure, including Ctrl-C, leaves the directory as it was found **and
leaves the last-success marker at its previous value**. A run that failed can
never make the readiness check say backups are current.

`droppilot` requires `--allow-production`, and is refused **before any
connection is opened** — the stated name is enough to refuse on, and opening a
session against production is itself something an unauthorised run must not do.
The server's own answer remains the backstop for the case where the two differ,
which is the case that matters: a staging `.env` left pointing at production.

`pg_dump` is found on `PATH`, or failing that in a standard PostgreSQL
installation directory, highest major version first. `BACKUP_PG_BIN_DIR`
overrides both. The fallback exists because the account a scheduled task runs as
routinely has a `PATH` the interactive operator's shell does not, which turns
"it works when I run it" into a nightly failure.

### `verify_database_backup.py`

```bash
python scripts/verify_database_backup.py --all
```

Manifest schema → manifest MAC → file size → file SHA-256 → full decryption
with every tag checked → plaintext length and digest → `pg_restore --list` with
a non-empty table of contents. The plaintext exists only inside a temporary
directory restricted to this account and is overwritten before removal.

**Existence is never treated as success.** A directory of files that fail this
reports failure, not "no backups found, nothing to do".

### `restore_database_backup.py`

```bash
# Rehearsal — the default
python scripts/restore_database_backup.py --file <name> \
    --expect-source droppilot_staging --target droppilot_restore_check

# Perform it
python scripts/restore_database_backup.py --file <name> \
    --expect-source droppilot_staging --target droppilot_restore_check --apply
```

Verifies first, then checks three identities: the backup's recorded source
against `--expect-source`, the connected database against `--target`, and that
the target is empty unless `--allow-non-empty`. Runs inside a single
transaction with `--exit-on-error`, so a failure leaves the target unchanged
rather than half-populated. Afterwards it reports the Alembic revision, the
table count and the configurable integrity checks — **all aggregates and
digests, never a row**.

As with a backup, the connection comes from configuration and `--target` is the
expectation it is checked against.

`droppilot` as a target is refused outright unless `--production-restore` is
given *and* the operator types the database name at a prompt. Without a
terminal it refuses rather than reading from an unattended stream — and if the
terminal check is wrong and the read hits end-of-file, that is a refusal with
exit code 2, never a traceback. A traceback would exit 1, which this tooling
reserves for a mistyped flag, and a monitor would file an unattended
production-restore attempt as the operator's typo.
**That flag has never been exercised.**

A restore reads the backup through **once**: identity is judged from the
manifest, which is authenticated on its own, and the single full verification
supplies the plaintext. A rehearsal verifies in full, because answering "would
this work" is its only purpose.

### `prune_database_backups.py`

Section 5.

---

## 5. Retention

**Proposed for review — not legally settled.** How long a copy of 1,268
tenants' personal data may be kept is a data-protection decision that needs a
lawful-basis argument behind it. This section makes whatever is decided
enforceable and auditable; it does not make the decision.

| Tier | Proposal | Setting |
|---|---|---|
| Daily | 14 days | `BACKUP_RETENTION_DAILY_DAYS` |
| Weekly | 8 weeks | `BACKUP_RETENTION_WEEKLY_WEEKS` |
| Monthly | 12 months | `BACKUP_RETENTION_MONTHLY_MONTHS` |

A backup survives if **any** window wants it — recent enough, newest in its
week, or newest in its month. Intersection would be the obvious-looking
mistake: it deletes a file that two of the three policies were trying to keep.

Twelve monthly copies means **a person erased in month one can persist in a
backup for up to a year**. That is the number the privacy notice has to be
honest about, and it is the main reason the tier is a decision rather than a
default.

### Refusals no flag relaxes

* the newest backup passing its integrity check is never deleted;
* nothing is deleted while only one backup passes;
* an unverifiable file is **quarantined, never removed** — it is evidence about
  a failure, and deleting evidence is how the failure stays invisible;
* the directory must be absolute, not a symlink or junction, at least two
  levels below its root, outside any home directory, system directory or source
  checkout, and every candidate's resolved parent must be that directory;
* `.part` files and orphaned manifests are quarantined, not deleted;
* the `.state` directory holding the markers is never enumerated or touched.

The default check is cryptographic — manifest authenticity, recorded sizes,
SHA-256 — which proves a file is byte-identical to what was published. **It does
not prove restorability.** `--deep` decrypts and parses every file; the report
always says which check ran. Running the deep check daily would read the entire
archive every night, which is why it is a flag and why the distinction is
stated rather than blurred.

**No cloud deletion happens, at all.** No provider is configured, and remote
deletion must not be added until a destination is chosen and reviewed
separately — a bug in retention against a local disk loses old backups; the
same bug against a bucket with delete permission loses all of them.

---

## 6. Scheduling — designed, deliberately not installed

A template lives at
[`backend/scripts/scheduler/droppilot-backup.xml.example`](../../backend/scripts/scheduler/droppilot-backup.xml.example).
**It has not been imported, and importing it on the production host is not part
of this milestone.** Installing it would begin taking production backups, which
needs the off-site destination, the key custody arrangement and the retention
decision to exist first.

### Requirements the template encodes

| Requirement | How |
|---|---|
| **Non-interactive** | Calls `.venv\Scripts\python.exe` with a fixed argument list. No prompt is reachable; the restore tool refuses without a terminal by design |
| **Dedicated restricted account** | Run as a service account that is a member of no administrative group, has "Log on as a batch job", is denied interactive and network logon, and has write access to the backup directory and read access to the checkout — **not the interactive operator account, and not the application's database user**. The database role it uses needs only `CONNECT` and `SELECT` |
| **One instance** | `<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>` plus `<ExecutionTimeLimit>`. A dump that overruns must not have a second one start on top of it |
| **Explicit working directory** | `<WorkingDirectory>` is set to the backend directory. This is not tidiness: the tools read the `.env` beside the checkout, so a wrong working directory audits — or backs up — the wrong configuration |
| **Restricted logs** | stdout and stderr are redirected into the backup directory, which is ACL'd to the service account. Logs record names, sizes and digests only; the tools filter tool output for anything credential-shaped before printing |
| **Failure propagation** | The task's last result is the process exit code, and the codes above are distinct. A monitor can distinguish "dump failed" from "disk full" without parsing text |
| **Last-success age** | The authenticated marker in `.state/last-success.json`, checked by `scripts/verify_production_config.py` against `BACKUP_MAX_BACKUP_AGE_HOURS` (default 30 — a daily schedule plus one missed run plus margin) |
| **Alerting contract** | Two signals, both required. **(a)** Non-zero exit from the task. **(b)** The readiness check reporting `BACKUPS` as anything but `PASS`. Either is an operator page the same day. A backup system with no alerting is a backup system that has already failed silently — and **no alerting is wired up today**, so this is a contract to implement, not one in force |
| **No plaintext secret** | The XML carries no password and no key. Credentials come from the `.env` the working directory selects, which is ACL'd separately. The task's own account password is held by Windows, not by the XML |

### Boot and logon limitations, stated honestly

* A task with a daily trigger **does not run while the machine is off**.
  `<StartWhenAvailable>true</StartWhenAvailable>` runs it late once the machine
  returns, which is why a missed window shows up as marker *age* rather than as
  a failure — the age check is the real detector, not the trigger.
* A batch-logon task **does not run if the service account's password expires
  or the account is locked**, and Task Scheduler reports that as a task-level
  error rather than a backup failure. It must be monitored as one.
* The task does not run if the checkout, the venv or the `.env` moves. Nothing
  detects that except the failure itself.
* Windows Task Scheduler has no built-in alerting. The contract above needs
  something outside it — the readiness check on a schedule, or a monitor
  reading the task's last result. **Neither exists yet.**

---

## 7. The drill

A restore drill is the only thing that converts a backup from a belief into a
fact. The procedure, as performed in BACKUP-B1 against **isolated databases
only**:

1. Create a database and migrate it to the current head.
2. Seed representative multi-tenant data, including relational rows.
3. Record row counts and stable digests — `sha256` over an ordered aggregate of
   each table, so the comparison is exact and the output is a hash, not data.
4. Take a real encrypted backup.
5. Verify it, including `pg_restore --list`.
6. Restore into a **different, newly created** database.
7. Compare: Alembic revision, schema objects, `NOT NULL` on every `tenant_id`,
   row counts, foreign-key integrity, that encrypted credential columns are
   still ciphertext, and that the digests match exactly.
8. Prove the negatives: a corrupted copy is refused, a wrong key is refused, a
   wrong source or target identity is refused.
9. Drop the isolated databases and shred every temporary plaintext file.

Step 8 matters as much as step 7. A verifier that accepts everything passes
step 7 too.

Record each drill by writing the authenticated marker in `.state/`. The
readiness check reads it; nothing else asserts that a drill happened.

---

## 8. What is still blocked

Publication remains blocked until **all** of these have actually happened —
not been implemented, happened:

1. an authorised production backup has been taken;
2. an off-site encrypted copy exists, in the UK or with a documented transfer
   basis, with credentials separate from the production host so that
   compromising the host does not delete the archive;
3. `BACKUP_ENCRYPTION_KEY` is generated and held in a secret store, with a
   sealed offline copy, and is not stored beside the backups;
4. a controlled restoration drill has been performed **from a production
   backup**, timed, with the result recorded;
5. the retention tiers are agreed as a data-protection decision, and the
   erasure-replay list exists;
6. alerting is wired to the two signals in section 6;
7. [docs/governance/BACKUPS.md](../governance/BACKUPS.md) and the privacy
   notice are updated in the same change that takes the first production
   backup — the notice currently says no backup copies exist, which is true
   today and becomes false that day.

Until then `scripts/verify_production_config.py` reports `BACKUPS` as
`BLOCKED`, and it reports *which* of these is missing rather than a bare
refusal.
