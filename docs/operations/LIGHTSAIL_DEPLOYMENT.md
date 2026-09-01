# AWS Lightsail deployment — topology, secrets and runbook

**Status: foundation only. Nothing is provisioned.** No Lightsail instance
exists, no managed database exists, no Cloudflare tunnel exists, no S3 bucket
exists, and no production secret has been generated. This milestone produced the
images, the Compose authority, the templates, the scripts and the tests. Every
`.example` file in `deploy/lightsail/` is uninstalled by design.

Production today remains the old Windows host, unchanged.

---

## 1. Topology

```
                    Internet
                       │
                 Cloudflare edge          TLS terminates here
                       │
          ┌────────────┴────────────┐
   app.whiteto.com          api.whiteto.com
          │                         │
          └──────── outbound tunnel ┘         cloudflared dials OUT.
                       │                      Nothing dials in.
  ═══════════════════ Lightsail Ubuntu instance (eu-west-2) ═══════════
                       │
                 ┌─────┴─────┐  docker network: edge
                 │           │
            frontend:3000  backend:8000
                                 │
                 ┌───────────────┴──────────┐  docker network: internal
                 │        │        │        │  (no default gateway)
              worker    beat    redis   rabbitmq
                 │        │
  ═══════════════╪════════╪════════════════════════════════════════════
                 └────────┴──► Lightsail Managed PostgreSQL
                                private networking, sslmode=verify-full

  Later, separately authorised:  S3 eu-west-2 (encrypted backups)
  External:                      Resend (OTP email), Google Identity Services
```

**Not one container publishes a host port.** The tunnel reaches `frontend` and
`backend` by container name on the `edge` network. `redis`, `rabbitmq`,
`worker` and `beat` sit only on `internal`, which is declared
`internal: true` — no default gateway, so they cannot reach the internet at
all, and nothing outside can reach them.

`backend` is the only service on both networks, because it is the only one that
must serve the tunnel *and* talk to the broker.

**PostgreSQL is not a container.** A database inside the Compose project dies
with the instance it was protecting against losing, and takes its volume with
it. It is a Lightsail Managed Database with public mode disabled.

**No nginx.** cloudflared routes to the two services directly. A second reverse
proxy would be a second place to get the forwarded-header trust story wrong, and
it terminates nothing.

---

## 2. What the old Windows host assumed, and what replaces it

| Windows assumption | Lightsail replacement |
|---|---|
| `C:\dsplive` drive-letter paths | `/opt/droppilot`, set explicitly as `WorkingDirectory=` |
| `run-backend.ps1` | `docker compose up` under a systemd unit |
| `.venv\Scripts\python.exe` | the image's `/opt/venv/bin/python`, on `PATH` |
| Task Scheduler XML | `droppilot.service` and `droppilot-backup.timer` |
| `Get-Process -Id 1776` for liveness | `docker compose ps` plus `/health/ready` |
| `C:\Program Files\PostgreSQL\17\bin` probing | `postgresql-client-17` in the ops image |
| CRLF line endings | `.gitattributes` governs; scripts are LF and mode 755 |

The Task Scheduler template from BACKUP-B1 stays in the repository — it is still
the scheduler for the old host until cutover. Nothing on Lightsail reads it.

---

## 3. Images

Four, all multi-stage, all non-root, all labelled with the commit they were
built from so `docker inspect` answers "what is actually running".

| Image | Runtime contents | Notes |
|---|---|---|
| `droppilot-backend` | venv + application, `libpq5`, `curl` | read-only root, `/health/ready` health check |
| `droppilot-worker` | same venv + application | Celery refuses to run as root by design |
| `droppilot-frontend` | Next.js **standalone** output only | no `node_modules`, no source, no build tooling |
| `droppilot-ops` | venv + `postgresql-client-17` + `ca-certificates` | migrations, readiness audit, backup tooling |

**Why `ops` is separate.** The backend image deliberately has no `pg_dump`.
Putting database-dumping tools inside the internet-facing process is the one
place they must not be. The ops image runs for thirty seconds under an
operator's hand and exits; its default command does nothing, so starting it by
accident cannot migrate anything.

**`NEXT_PUBLIC_API_URL` is a build-time authority.** Next.js inlines every
`NEXT_PUBLIC_*` value into the client bundle during `npm run build`. Setting it
at runtime changes nothing. `scripts/deploy/build_images.sh` takes it as a
required argument and refuses a non-https or loopback value, because that is the
last moment the mistake can be caught — after this the frontend looks fine and
calls the wrong host.

**Honest limitation: dependency installation is pinned, not locked.** The
backend images derive a requirements list from `pyproject.toml`, whose
constraints are lower bounds (`>=`). Two builds a month apart can therefore
resolve different patch versions. The frontend is genuinely deterministic —
`npm ci` against a committed lockfile. Closing the backend gap needs a lockfile
(`uv.lock` or `pip-compile`), which is a change to how dependencies are managed
and out of scope here. Digests recorded at build time are what make a given
deployment reproducible in the meantime.

---

## 4. Secrets

```
/etc/droppilot/            700  root:root
├── app.env                600  application configuration and secrets
├── deploy.env             600  Compose variables (image digests, hostnames)
├── images.env             600  written by build_images.sh
├── images.previous.env    600  written by deploy.sh — the rollback target
├── tls/
│   └── rds-ca-eu-west-2.pem   the AWS CA bundle, read-only into containers
└── cloudflared/
    ├── config.yml
    └── <tunnel-uuid>.json 600  the tunnel's private key
```

Outside Git. `scripts/deploy/preflight.sh` refuses to deploy if the directory is
not 700, if either env file is not 600 and root-owned, or if a deployment env
file has been committed.

**Where a secret must never appear**, each enforced by a test:

* not in a Compose file — `compose.lightsail.yml` names variables and holds no
  values, and every mandatory one uses `${VAR:?}` so Compose *refuses* rather
  than substituting a published placeholder;
* not in an image — nothing secret is a build argument, and a build argument is
  recorded in `docker history` for anyone who can pull the image;
* not on a command line — readable by every user on the host through `/proc`;
* not in a systemd unit — units reference `EnvironmentFile=`, and
  `systemctl show` does not render its contents;
* never in a `NEXT_PUBLIC_*` variable — those are shipped to every browser.

### Rotation

| Secret | Rotation | Cost of rotating |
|---|---|---|
| `SECURITY_SECRET_KEY` | any time | every session ends; users log in again |
| `SECURITY_OTP_HMAC_KEY` | any time | in-flight reset codes stop working |
| `SECURITY_ENCRYPTION_KEYS` | prepend the new key | **none if prepended** — decryption tries every key, encryption uses the first. Removing the old key requires re-encrypting first |
| `POSTGRES_PASSWORD` | in the Lightsail console, then the env file | a restart; connections drop |
| `RESEND_API_KEY` | in the Resend dashboard | none |
| `RABBITMQ_PASSWORD` | env file + `rabbitmq` volume reset | queued tasks are lost — drain first |
| Cloudflare tunnel credential | new tunnel, new DNS | the old hostname stops resolving |
| `BACKUP_ENCRYPTION_KEY` | **cannot be rotated freely** | rotating without re-wrapping makes every existing backup unopenable |

Rollback for any of these is the same shape: keep the previous value until the
new one is confirmed working, then remove it. For the Fernet list that is
literal — both keys live in the list at once.

### The Google client secret that does not exist

The implementation verifies an ID token issued to the browser by Google
Identity Services. It never exchanges an authorization code, so there is no
client secret to hold. Inventing one would put an unused credential in a file —
a liability, not a control. Adding one is a change of flow, not of
configuration, and would need its own review.

---

## 5. Google sign-in

Unchanged from AUTH-G1/R1/R2/R3; this milestone only supplies production
configuration. The protections that must not regress:

* **Split endpoints.** Login and signup are different routes. Login never
  creates a tenant, so an unknown Google account cannot silently become a
  workspace; signup is where an account is created and is where the Terms gate
  applies.
* **Signup requires published Terms.** `TERMS_PUBLISHED` is `False`, so a
  deployed environment refuses every registration. That is intentional and is
  not resolved by editing configuration.
* **Nonce and intent binding.** The nonce is generated server-side and bound to
  the intent (login versus signup), so a token minted for one cannot be replayed
  at the other.
* **Audience validation.** The backend checks `aud` against
  `GOOGLE_OAUTH_CLIENT_ID`, `iss` against Google, and `email_verified`.
* **No Google token is persisted.** Verified, then discarded; only the subject
  identifier is stored.
* **Linking needs step-up.** Attaching a Google identity to an existing local
  account requires an authenticated password step-up, rate-limited by
  AUTH-G1-R3's atomic throttle.

Production configuration: authorised JavaScript origin `https://app.whiteto.com`
in the Google Cloud console, client ID in `GOOGLE_OAUTH_CLIENT_ID` **and** baked
into the frontend bundle as `NEXT_PUBLIC_GOOGLE_CLIENT_ID`. **The console has
not been touched.**

---

## 6. Resend

* `EMAIL_PROVIDER=stub` in the template. `stub` generates reset codes without
  delivering them; switching to `resend` begins sending real email to real
  people and is separately authorised.
* Sender `security@auth.whiteto.com` on the already-verified `auth.whiteto.com`
  domain. Mail from any other domain fails DMARC alignment and lands in spam.
* The API key is a secret in `app.env` only.
* No key, OTP code or address is logged — `LOG_INCLUDE_REQUEST_BODY=false`, and
  the OTP path never logs the code.

**Data residency, stated honestly.** Resend sends from an Ireland region when
configured to, which keeps message *delivery* in the EU. Resend is a US company,
and account metadata — recipient addresses, delivery events, logs — is processed
in the United States. That is an international transfer and needs a documented
basis before real sending begins. It is not resolved by this milestone and must
not be described as if it were.

---

## 7. Cloudflare

`app.whiteto.com` → `http://frontend:3000`, `api.whiteto.com` →
`http://backend:8000`, and a catch-all of `http_status:404`. The catch-all is
required by cloudflared and is deliberately a refusal: pointing it at the
backend would publish the API under every hostname anyone ever aims at this
tunnel.

The tunnel dials out. No inbound web rule is needed, there is no origin IP to
find and go around the WAF with, and there is no origin certificate to expire.

### Lightsail firewall

| Rule | Value |
|---|---|
| SSH (22) | **one explicit operator IP**, never `0.0.0.0/0` |
| HTTP (80) | **removed** — Lightsail adds it by default |
| HTTPS (443) | **removed** — the tunnel needs no inbound port |
| Everything else | denied |

**Review IPv4 and IPv6 separately.** Lightsail's console presents them as one
list but they are two rule sets, and an IPv6 `::/0` beside a restricted IPv4
range is the exact shape of this mistake. An instance with IPv6 enabled and an
unreviewed `::/0` on port 22 is open to the internet.

`SECURITY_TRUSTED_PROXIES` must contain the connector's address on the `edge`
network, or every request appears to come from the tunnel and rate limiting
collapses to one bucket for the whole internet.

Rollback: `systemctl stop cloudflared` — or stop the container — and the
hostnames stop resolving to this origin. Nothing else is affected, and no DNS
change is needed.

---

## 8. Managed PostgreSQL

* **Version.** The old host runs PostgreSQL 17.10. Lightsail's managed offering
  must be created as 17.x; the ops image carries `postgresql-client-17`, and a
  client older than the server cannot read its custom-format dumps.
* **Private only.** Public mode disabled in the console. A publicly-reachable
  managed database is on the internet with a password in front of it.
* **TLS.** `sslmode=verify-full` with the regional AWS CA bundle. `require`
  encrypts without checking who answered; `verify-full` checks the chain and the
  hostname. PROD-H1 now refuses anything weaker in a deployed environment, and
  refuses `verify-full` without a CA bundle that exists.
* **Pooling.** `(pool_size + max_overflow) × processes` must stay under the
  plan's `max_connections` with headroom for the migration and ops containers.
  10 + 5 across backend, worker and beat reaches 45; a 2 GB plan allows ~200.

### Migration runbook

`0029 → 0030 → 0031 → 0032`. The `migrate` service runs once and exits;
`backend`, `worker` and `beat` all wait on `service_completed_successfully`, so
a failed migration keeps the application down rather than letting it serve
against a half-migrated schema.

**Checkpoints.** `scripts/deploy/migrate.sh` records the revision before and
after. That pair is the rollback checkpoint — and today it is the *only* one:
there is no production backup to restore from, so the recovery path for a bad
migration is its own `downgrade()`. That is a thin guarantee and is the strongest
argument for activating backups before cutover, not after.

---

## 9. systemd

`droppilot.service` starts the stack after `docker.service` and
`network-online.target` — the difference between an interface existing and it
being able to reach the managed database. `ExecStart` uses `--wait`, so systemd
reports the unit active only once every health check passes; without it a
crash-looping stack reports success.

Restarts are throttled to five in ten minutes. A stack that restarts forever
hammers the managed database's connection limit and buries the original failure.

Logs go to the journal (size-capped in `journald.conf`) and container logs are
separately capped by the json-file driver at 10 MiB × 3, so neither can fill the
disk.

`droppilot-backup.service` and `.timer` are written and **must not be
installed** until backup activation is authorised. `Persistent=true` runs a
missed window once the machine returns; the readiness check's age test is the
real detector, not the timer.

---

## 10. Deploying

```bash
./scripts/deploy/preflight.sh    <accepted-sha>          # reads, changes nothing
./scripts/deploy/build_images.sh <accepted-sha> https://api.whiteto.com
./scripts/deploy/deploy.sh       <accepted-sha>          # preflight, migrate, up --wait, verify
./scripts/deploy/verify_health.sh                        # any time
./scripts/deploy/rollback.sh                             # previous images, typed confirmation
```

Preflight refuses a dirty tree, a commit that is not the accepted one, loose
secret-file permissions, a published port, a privileged container, host
networking, a development server, an image not pinned by digest, and a
configuration PROD-H1 rejects. It changes nothing, so it can be run as often as
you like.

Rollback moves frontend and backend together — a frontend built against one API
contract talking to a backend serving another is a broken deployment that
reports itself healthy. It asks the schema question out loud rather than
assuming, because a rollback across a migration that removed a column is a
decision no script should make. Nothing in any script deletes an image, a volume
or a row.

---

## 11. What must happen before cutover

Not code. None of this is resolved by this milestone.

1. **Provision**: Lightsail instance in eu-west-2, managed PostgreSQL 17 with
   public mode off, S3 bucket for backups.
2. **Generate every secret** into `/etc/droppilot/app.env`. Nothing may be
   reused from the old host's `.env` without rotating it.
3. **Create the Cloudflare tunnel**, install the credential, point
   `app.whiteto.com` and `api.whiteto.com` at it.
4. **Add `https://app.whiteto.com`** as an authorised origin in the Google Cloud
   console.
5. **Lock the firewall**: SSH from one IP, no 80, no 443, IPv6 reviewed
   separately.
6. **Run the migration** `0029 → 0032` against the managed database, having
   first taken a backup of the old host — which requires backup activation.
7. **Activate backups**: key into a secret store, off-site S3 destination,
   retention agreed, a restore drill from a real production backup.
8. **Resolve the Resend transfer basis** before real sending.
9. **Publish the Terms** after solicitor approval. Until then a deployed
   environment refuses every registration, by design.
10. **Migrate the data** from the old host — a separate, authorised operation
    that this milestone does not describe and must not be improvised.

Steps 6, 7 and 10 are the ones with no undo. They need the backup regime to
exist first.
