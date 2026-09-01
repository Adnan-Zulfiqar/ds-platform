# Backups

## The position

**No backup of the production database has ever been taken.** As of BACKUP-B1
the tooling to take, verify, restore and retain one exists and has been proven
end to end — on isolated databases. That proves the tooling. It does not
protect a single row of production data, and nothing in this repository should
be read as claiming otherwise.

Verified rather than assumed, as of this milestone:

* **Tooling exists**: `backend/scripts/create_database_backup.py`,
  `verify_database_backup.py`, `restore_database_backup.py` and
  `prune_database_backups.py`, over `app/services/database_backup.py`,
  `app/services/backup_retention.py` and `app/core/backup_crypto.py`. Design and
  threat model: [docs/operations/BACKUP_RUNBOOK.md](../operations/BACKUP_RUNBOOK.md).
* **A restore drill has been performed**, against a database built by running
  the migrations to head, seeded with multi-tenant relational data and
  encrypted credential columns, restored into a second database with matching
  digests — `backend/tests/integration/test_backup_restore_drill.py`. **On
  isolated databases only.**
* **No production backup exists.** No `pg_dump` has been run against
  `droppilot`. The tooling refuses that name without an explicit flag, and the
  flag has never been used.
* **No scheduled task exists.** A Task Scheduler template is committed at
  `backend/scripts/scheduler/droppilot-backup.xml.example` and has deliberately
  **not** been imported.
* **No off-site copy exists, and no destination has been chosen.** Every
  hypothetical copy would be on the same host as the database.
* **No backup encryption key has been generated for production.** There is no
  secret store to put one in.
* No object storage in use: `StorageSettings` exists with an `S3_` prefix and
  `S3_REGION` defaulting to `eu-west-1`, but no application code reads it. The
  backup tooling does not use it either — it writes to a local directory only.

## What that means

This is a **service-continuity failure before it is a privacy one**. A disk
failure, a bad migration or an accidental `DROP` on the production host loses
every customer's workspace with no route to recovery. The production database
holds 1,268 tenants.

It also means one thing the privacy notice must **not** say. A notice claiming
data is "permanently deleted, including from backups" would be unverifiable
today and false the moment backups are introduced without erasure reaching them.
The notice therefore states the position honestly: deletion removes data from
the live system, and there are currently no backup copies for it to persist in.

## Required before commercial launch

Listed as [blocker 2](README.md#blockers-before-public-launch). The **Mechanism**
and **Encryption** rows below are now implemented; the rest are operational
decisions and actions that code cannot make on anybody's behalf:

| Requirement | Target | Status |
|---|---|---|
| Mechanism | Scheduled `pg_dump` of the production database, or a managed equivalent | **tool built, never scheduled** |
| Frequency | Daily at minimum; the acceptable data loss window is a business decision | scheduler template written, **not installed** |
| Encryption | **At rest and in transit, mandatory.** A backup is a complete copy of every customer's personal data in one portable file | **implemented** — AES-256-GCM envelope, no unencrypted mode exists |
| Access control | A dedicated credential with no interactive login; not the application's database user | documented in the runbook; **no such account created** |
| Location | United Kingdom, matching the production server, or a documented transfer basis if not | **no destination chosen**, so no transfer to assess |
| Retention | A fixed period, chosen and written down — an indefinite backup archive is indefinite retention of everything ever deleted | tooling enforces a policy; the tiers are **proposed, not agreed** |
| Restore testing | A restore actually performed and timed. An untested backup is a belief, not a backup | **done on isolated databases**; never from a production backup |
| **Erased data** | The hard question below | **unresolved** — the replay list does not exist |

## Erasure versus immutable backups

When backups exist, a person erased today still exists in yesterday's backup.
This is a known tension in UK GDPR practice, and the usual resolution is:

* Backups are **put beyond use** rather than edited — you do not surgically
  remove one person from a compressed dump.
* The erasure is **re-applied on restore**: a list of completed erasure requests
  is kept, and any restore replays them before the system is returned to
  service.
* The backup retention period bounds how long the residue can exist, which is
  why choosing that period is not optional.

None of this is implemented. It still has nothing to be implemented against —
no production backup exists — but the tension is now *imminent* rather than
hypothetical, and the proposed twelve monthly copies would mean **a person
erased in month one persists in a backup for up to a year**. That number is the
main reason the retention tiers are a data-protection decision rather than a
default.

Before the first production backup is taken, the runbook in
[RETENTION_AND_ERASURE.md](RETENTION_AND_ERASURE.md) must gain a step recording
each completed erasure for replay, and the privacy notice must be updated in
the same change.

## What the notice says today

> "We do not currently keep backup copies of the production database. Deleting
> data therefore removes it from the only copy we hold."

**Still true.** BACKUP-B1 built the tooling and proved it on isolated
databases; it took no production backup, so no backup copy of production data
exists for erased data to persist in. The sentence must be changed in the same
change that takes the first one — not when the tooling was written, which is
the mistake this paragraph exists to prevent.
