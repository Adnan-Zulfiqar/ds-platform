# Backups

## The position

**There are none.** Not "untested", not "informal" — none.

Verified rather than assumed:

* No backup code in the application. The single match for "backup" across
  `backend/app` is an incidental comment in `app/integrations/ebay/oauth.py:111`
  about the blast radius of a leaked backup.
* No `pg_dump` anywhere in the repository.
* No scheduled task, service or cron entry performing one on the production
  host.
* No object storage in use: `StorageSettings` exists with an `S3_` prefix and
  `S3_REGION` defaulting to `eu-west-1`, but no application code reads it.

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

Listed as [blocker 2](README.md#blockers-before-public-launch). Whoever
implements it needs to settle all of these, not just the first:

| Requirement | Target |
|---|---|
| Mechanism | Scheduled `pg_dump` of the production database, or a managed equivalent |
| Frequency | Daily at minimum; the acceptable data loss window is a business decision |
| Encryption | **At rest and in transit, mandatory.** A backup is a complete copy of every customer's personal data in one portable file |
| Access control | A dedicated credential with no interactive login; not the application's database user |
| Location | United Kingdom, matching the production server, or a documented transfer basis if not |
| Retention | A fixed period, chosen and written down — an indefinite backup archive is indefinite retention of everything ever deleted |
| Restore testing | A restore actually performed and timed. An untested backup is a belief, not a backup |
| **Erased data** | The hard question below |

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

None of this is implemented, because there is nothing to implement it against
yet. When backups are introduced, the runbook in
[RETENTION_AND_ERASURE.md](RETENTION_AND_ERASURE.md) must gain a step recording
each completed erasure for replay, and the privacy notice must be updated to
describe the backup position accurately.

## What the notice says today

> "We do not currently keep backup copies of the production database. Deleting
> data therefore removes it from the only copy we hold."

True as of this milestone, and it must be changed in the same commit that
introduces backups.
