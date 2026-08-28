# Log retention

## The position today

**The application persists no logs, and therefore retains none.**

`app/core/logging.py` configures exactly one handler,
`logging.StreamHandler(sys.stdout)`. There is no `FileHandler` anywhere. The
production start script (`run-backend.ps1`) runs uvicorn in the foreground and
redirects nothing, and no nginx or Docker log driver is in use — neither is
running on the production host. Request logs, which contain client IP addresses,
therefore exist only in the console of the running process and disappear with it.

That is a defensible privacy position and a poor operations position: there is
nothing to delete, and equally nothing to investigate a security incident with.
Whichever way that trade-off is resolved, the privacy notice has to match the
answer, so it is [blocker 4](README.md#blockers-before-public-launch).

**Cloudflare is separate.** `cloudflared` runs on the production host, so
Cloudflare terminates TLS and keeps its own edge logs — including IP addresses —
under Cloudflare's retention, not ours. That belongs in
[SUBPROCESSORS.md](SUBPROCESSORS.md), and no tool of ours can delete it.

## The pruning authority

`app/core/log_retention.py` and `scripts/prune_logs.py` exist so that the moment
a deployment *does* persist logs, a retention period is enforced rather than
promised.

```bash
cd backend
python scripts/prune_logs.py                      # dry run — the default
python scripts/prune_logs.py --apply              # actually delete
python scripts/prune_logs.py --retention-days 7 --apply
```

Configuration:

| Variable | Default | Meaning |
|---|---|---|
| `LOG_DIRECTORY` | *unset* | The one directory to prune. Unset means refuse. |
| `LOG_RETENTION_DAYS` | `30` | Maximum age of a rotated file. |

With `LOG_DIRECTORY` unset — which is the case in production — the script prints
a refusal and exits 2. **That refusal is the correct behaviour**, not a
misconfiguration to work around.

## Why it is built the way it is

This is a program whose entire purpose is deleting files on a production server,
usually unattended, often as a privileged user. The failure mode is not keeping
a log a day too long; it is removing something that was never a log. Each rule
closes one route to that:

| Rule | Closes |
|---|---|
| No default directory; unset means refuse | A tool that guesses where logs are is a tool that deletes something else |
| `glob`, never `rglob` | A log directory containing a checkout becoming a repository deletion |
| Refuses a directory holding `.git`, `.env`, `alembic.ini`, `pyproject.toml`, `package.json`, `.venv`, `node_modules` | Being pointed at a project root |
| Refuses a filesystem root | The unrecoverable mistake |
| Name allowlist — only `*.log` and `*.log.*` | `dump.sql`, `config.json`, anything else |
| Containment re-checked after `resolve()`, before and again at unlink | A symlink pointing outside the directory |
| The un-rotated `*.log` is never deleted | Removing the file a process has open |
| Dry run is the default | An unattended first run doing something irreversible |

### The limitation, stated plainly

Because the live `*.log` file is never deleted, **retention applies to rotated
files only**. A deployment that persists logs without rotating them will grow
one file forever, and the 30-day period will not apply to it. If log persistence
is enabled, rotation must be configured at the same time, or the published
retention period is not true.

An earlier draft protected "the newest file per stem" instead. A test showed
that rule protected an abandoned service's stale log forever while leaving
nothing else to delete — exactly the wrong file. Selecting on the name rather
than modification time fixes it, and
`tests/unit/core/test_log_retention.py::TestWhatItSelects::test_a_stale_rotated_file_with_no_live_sibling_is_still_removed`
keeps it fixed.

## Rollback

The script deletes files; there is no undo. Rollback means restoring from a
backup, and there are no backups — so **rehearse with a dry run first**, every
time. That is why dry run is the default rather than a flag.

To stop it entirely: unset `LOG_DIRECTORY`. The next run refuses.

## Tests

`backend/tests/unit/core/test_log_retention.py` — 22 tests, no database or
network. Covers every refusal above, the exact cutoff boundary (a file on its
thirtieth day is kept, not deleted), non-recursion, the name allowlist,
idempotency, that building a plan writes nothing, and that a symlink escaping
the directory is skipped (that last one is skipped on Windows, where creating a
symlink needs privilege).
