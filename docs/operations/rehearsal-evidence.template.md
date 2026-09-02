# Linux rehearsal evidence

**STATUS: NOT EXECUTED**

This file is a template. Every field below is empty because the rehearsal has
not been run. It is checked into the repository in this state deliberately, so
that the gap is visible in the tree rather than remembered by whoever last read
the report.

---

## What this file is not

**Filling it in by hand does not make anything ready.** Nothing reads it: no
readiness rule parses it, `scripts/verify_production_config.py` does not look at
it, and PROD-H1's `BACKUPS` and configuration rules are unaffected by its
contents. It is a record for humans, and a record a human can type is not
evidence a machine should trust.

That is the point. A file that *could* flip a readiness check would be a file
worth forging under deadline pressure. The things that gate deployment are the
readiness rules, the preflight script and the tests — all of which derive their
answers from the system rather than from a document.

**So what is it for?** It is where the operator who runs
`scripts/deploy/rehearse_linux.sh` writes down what actually happened, so that a
reviewer can tell a rehearsal that was run from one that was described. Leave it
in `NOT EXECUTED` until then.

---

## 1. Provenance

| | |
|---|---|
| Commit | `NOT EXECUTED` |
| Tree hash | `NOT EXECUTED` |
| Rehearsed by | `NOT EXECUTED` |
| Date (UTC) | `NOT EXECUTED` |

## 2. Host

| | |
|---|---|
| OS / kernel (`uname -a`) | `NOT EXECUTED` |
| Docker server version | `NOT EXECUTED` |
| Docker Compose version | `NOT EXECUTED` |
| CPU / memory available | `NOT EXECUTED` |

## 3. Images

Built with `--no-cache`. A rehearsal that reuses layers proves the layers, not
the build.

| Image | Digest / ID | Size | `image.revision` label |
|---|---|---|---|
| `droppilot-backend` | `NOT EXECUTED` | | |
| `droppilot-worker` | `NOT EXECUTED` | | |
| `droppilot-ops` | `NOT EXECUTED` | | |
| `droppilot-frontend` | `NOT EXECUTED` | | |

## 4. Scans

| Scan | Tool and version | Result |
|---|---|---|
| Vulnerability (HIGH/CRITICAL) | `NOT EXECUTED` | |
| Secret in image history | `NOT EXECUTED` | |

If a scanner was unavailable, write "not installed" rather than leaving it
blank — an absent tool and a clean result are different outcomes.

## 5. Compose and exposure

| Check | Result |
|---|---|
| `docker compose config` renders | `NOT EXECUTED` |
| Services started and healthy | `NOT EXECUTED` |
| `ss -ltn` shows no public internal port | `NOT EXECUTED` |
| Backup profile selected | must be **no** |

## 6. Migration

| | |
|---|---|
| Isolated database name | `NOT EXECUTED` |
| Revision before | `NOT EXECUTED` |
| Revision after | `NOT EXECUTED` (expected `0032`) |
| Migration container exit code | `NOT EXECUTED` |

## 7. Health

| Probe | Result |
|---|---|
| `backend /health/live` | `NOT EXECUTED` |
| `backend /health/ready` | `NOT EXECUTED` |
| `frontend` serves | `NOT EXECUTED` |
| `worker` answers over RabbitMQ | `NOT EXECUTED` |

## 8. Playwright

| | |
|---|---|
| Suite and count | `NOT EXECUTED` |
| Passed / failed / skipped | `NOT EXECUTED` |

## 9. Restart and recovery

| Scenario | Result |
|---|---|
| `SIGTERM` to backend, graceful drain | `NOT EXECUTED` |
| Stack healthy after backend restart | `NOT EXECUTED` |
| Worker reconnects after broker outage | `NOT EXECUTED` |
| Stack healthy after full `down`/`up` | `NOT EXECUTED` |

## 10. Cleanup

| | |
|---|---|
| Containers removed | `NOT EXECUTED` |
| Volumes removed | `NOT EXECUTED` |
| Rehearsal images removed | `NOT EXECUTED` |
| Anything *not* created by this run touched | must be **no** |

## 11. Mocked versus real

State plainly which dependencies were real and which were substituted. A
rehearsal is only useful if the reader knows what it did not exercise.

| Dependency | Real or mocked |
|---|---|
| PostgreSQL | `NOT EXECUTED` — expected: real, isolated container |
| Redis | `NOT EXECUTED` — expected: real, isolated container |
| RabbitMQ | `NOT EXECUTED` — expected: real, isolated container |
| Google Identity Services | expected: **mocked**, fake GIS transport, no network call |
| Resend | expected: **stub provider**, no real send |
| Cloudflare tunnel | expected: **not started** |
| AWS S3 / backups | expected: **not touched** |
| Production database | expected: **never contacted** |

---

## After filling this in

A completed rehearsal removes one blocker. It does not remove the others: the
Terms are unpublished, no backup regime is operational, and nothing is
provisioned on AWS or Cloudflare. See
[LIGHTSAIL_DEPLOYMENT.md](LIGHTSAIL_DEPLOYMENT.md) §11.
