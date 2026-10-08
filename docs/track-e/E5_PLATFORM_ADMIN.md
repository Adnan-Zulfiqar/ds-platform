# Track E5 — platform admin panel

Status: **decided 2026-10-04 (D-015)**: option A, `PlatformTenantDirectory`
approved, impersonation out. Built in stages:

| Stage | Scope | State |
|---|---|---|
| E5a | Operator identity: table, password + TOTP sign-in, platform tokens, IP allow-list, audit, CLI | Done |
| E5b | `PlatformTenantDirectory`: workspace list + suspend / reactivate, audited | Done |
| E5c | Health counts per workspace (failed syncs/publishes, email outbox) | Done |
| E5d | Frontend `/platform` pages | Done |

## E5a as built

- **Tables (migration `0046`).** `platform_admins` holds the email, the
  Argon2id hash, the TOTP seed encrypted with the platform key, the last
  accepted TOTP step, and `is_active`. `platform_admin_audit` is append-only
  in the application.
- **Sign-in** is `POST /api/v1/platform/auth/login {email, password, code}`.
  - **Same refusal for everything.** An unknown email, a wrong password, a
    wrong code, a reused code and a disabled account all get the same 401
    and message.
  - **Rate limit.** 10 attempts per 15 minutes per address.
  - **Failed attempts** are audited in their own transaction.
  - **TOTP** is RFC 6238: SHA-1, 30 seconds, 6 digits, ±1 step for clock
    drift. A code works once.
  - **Token.** A successful sign-in returns a platform token that lasts 30
    minutes (`PLATFORM_ADMIN_TOKEN_TTL_MINUTES`) and cannot be refreshed.
- **Separation.** The platform token uses its own audience
  (`<api>:platform`), so tenant routes refuse it, and platform routes refuse
  tenant tokens.
- **Off by default.** The `PLATFORM_ADMIN_ALLOWED_CIDRS` allow-list is empty
  by default, and then every `/api/v1/platform/*` route answers 404.
- **Creating an operator** happens on the server only:
  `docker exec -it -e PYTHONPATH=/app droppilot-backend-1 python scripts/create_platform_admin.py --email you@example.com`
  (`PYTHONPATH=/app` is required; without it the script fails with
  `No module named 'app'`). The script reads the password twice and prints the `otpauth://` URI
  **once**, to add to an authenticator app.
- **Closed-list guard.** `tests/unit/test_unscoped_repositories_are_a_closed_list.py`
  now fails if any repository escapes tenant scoping without being on
  CLAUDE.md §4.

### Verified (E5a)

- RFC 6238 Appendix B test vectors, drift and malformed codes
  (`test_totp.py`).
- Token separation in both directions (`test_platform_tokens.py`).
- Integration tests (`test_platform_admin_auth.py`) on real Postgres:
  - sign-in to `/me`;
  - a uniform refusal for each failure, each audited;
  - one-time codes;
  - a disabled admin refused;
  - an owner token refused by the platform, and a platform token refused by
    tenant routes;
  - 404 when off, or outside the allow-list;
  - duplicate admin refused.

### Not verified

- The CLI script against the running stack. It needs the operator's own
  password at a terminal.

## E5b as built

- **Workspace list.** `GET /api/v1/platform/tenants?page&size&q` returns each
  workspace's id, name, slug, status, active flag and creation time, with
  two counts: active users and connected stores. Nothing tenant-owned is
  ever returned. Search is case-insensitive on name and slug, with `%` and
  `_` matched literally.
- **Suspend and reactivate.** `POST …/tenants/{id}/suspend` and
  `…/reactivate` each take a **reason** (required, 3–500 characters). They
  set `is_active` and `status` (`suspended` / `active`) and write an audit
  row with the reason and the previous state, in the same transaction.
- **When a suspension bites.** Sign-in, Google sign-in and token refresh
  already refuse an inactive workspace uniformly, so a suspension takes
  effect at the next refresh: within one access-token lifetime (15 minutes).
- **Audit view.** `GET /api/v1/platform/audit?limit` shows the most recent
  operator actions.

### Verified (E5b)

`tests/integration/test_platform_admin_directory.py` covers:

- the list returning only directory fields and counts;
- literal wildcards in search;
- a suspension refusing tenant login, a reactivation restoring it, and both
  being audited with their reasons;
- a missing reason getting 422, and an unknown workspace getting 404;
- a tenant owner refused by the directory.

## E5c as built

`GET /api/v1/platform/tenants/{id}/health` returns four **counts** for one
workspace, and never the rows behind them:

- failed order syncs in the last 24 hours;
- failed inventory syncs in the last 24 hours;
- listings currently in error;
- notification emails that failed in the last 24 hours (E3 outbox).

Every query is a `count` filtered by `tenant_id`, on the approved
directory class. An unknown workspace gets 404; a tenant token gets 401.

### Verified (E5c)

`test_platform_admin_health.py` checks that the window and the workspace
boundary are respected: a 30-hour-old failure and another workspace's
failure are not counted. It also checks the 404 and the 401.

## E5d as built

`/platform` is a single page outside the tenant app: no `AuthProvider`, no
tenant navigation, and `noindex`.

- **Sign-in form.** Email, password and the 6-digit code. If the panel is
  switched off for the caller's network (the API answers 404), the form says
  so.
- **Console.** The workspace list (search, paging), a workspace panel with
  the health counts and suspend/reactivate behind a required reason, and the
  last 20 audit entries.
- **A separate API client** (`lib/platform/client.ts`):
  - it never sends the tenant token and never refreshes;
  - it sends no cookies;
  - the platform token lives in memory only, so a reload ends the operator
    session;
  - errors use the same `ApiError` shape through the shared `toApiError`.

### Turning it on (owner)

1. On the server, create an operator:
   `docker exec -it -e PYTHONPATH=/app droppilot-backend-1 python scripts/create_platform_admin.py --email you@example.com`.
   Type the password twice, then add the printed `otpauth://` URI to your
   authenticator app. It is shown only once.
2. Set `PLATFORM_ADMIN_ALLOWED_CIDRS` to your own network, e.g.
   `203.0.113.4/32`. On this PC, browsing through `localhost`, that is
   `127.0.0.1/32,172.16.0.0/12` (local only). The API sees Docker's bridge address
   unless the proxy forwards the client IP.
3. Recreate the backend container, then open `/platform`.

### Verified (E5d)

`frontend/tests/e2e/platform-console.spec.ts` runs against a mocked API. It
checks that:

- sign-in sends exactly email, password and code, with no tenant token;
- later calls carry the platform token;
- the list, health and audit render;
- suspend is disabled until a reason is typed, then sends exactly that
  reason;
- a switched-off panel shows its own message.

---

*Original proposal (kept for the record):*

## Why this needs approval before any code

A platform admin panel is, by definition, a view **across** workspaces. Every
other request path in DropPilot is confined to one tenant by
`TenantScopedRepository`, and CLAUDE.md §4 says the unscoped list is closed:
"Do not add to this list without explicit approval, and never on a request
path." A panel needs both an addition and a request path. It is also a new
kind of identity, a person who belongs to no tenant, which is an
architecture change under hard stop 6.

## What a first version would do (proposed scope)

Read-mostly. Each item is something that today needs a database shell:

1. **Workspaces list.** Name, slug, status, created date, user count and
   connected channels. Paged and searchable by name or slug.
2. **Suspend / reactivate a workspace.** This flips `tenants.is_active` and
   `status`. Login already refuses suspended tenants uniformly, so no new
   enforcement is needed.
3. **Operational health.** Counts of failed syncs, failed publishes and the
   email outbox (`failed` rows) per workspace, for the last 24 hours.
4. **Audit log of every admin action**: who, what, which workspace and when.
   This is written in the same transaction as the action.

Explicitly **out**:

- Impersonating a user ("log in as").
- Reading product, order or buyer data in a workspace.
- Editing anything inside a workspace.

Impersonation is the most useful support tool and the most dangerous. It
deserves its own decision.

## Options for the platform-admin identity

| Option | How | For | Against |
|---|---|---|---|
| **A. Separate `platform_admins` table + separate login** (recommended) | Own table, own Argon2id password plus a **mandatory TOTP**; tokens carry `typ: "platform"` and an audience the tenant API rejects; routes under `/api/v1/platform/*` | No tenant user can be promoted into it by a role bug; the blast radius of a tenant-token bug excludes the panel | A second login flow to build and test; TOTP is new code |
| B. A `platform_admin` role on an ordinary user | One more role in `roles` | Least code | That user still belongs to a tenant. A role-check bug becomes a cross-tenant breach, which is the worst failure this platform has |
| C. No web panel; CLI scripts only | `scripts/admin_*.py` run on the server | Zero new attack surface | Needs a shell for every support action, and leaves no audit trail unless added |

## Data access

- **New unscoped class, `PlatformTenantDirectory`.** It reads `tenants` plus
  aggregate counts and never returns a tenant-owned row. It goes on the
  CLAUDE.md §4 list as a **request-path** exception and is reachable only
  through a `RequirePlatformAdmin` dependency.
- **Writes** (suspend/reactivate) go through the existing `TenantRepository`.
- **Audit:**
  - Recorded in a new append-only `platform_admin_audit` table.
  - Its migration revokes `UPDATE`/`DELETE` on that table from the application
    role where the deployment allows it.

## Security controls (each with a test, per CLAUDE.md §8)

- **Token separation:**
  - A tenant access token gets 401 on every `/platform/*` route.
  - A platform token gets 401 on every tenant route.
- **TOTP:**
  - Required at login.
  - A wrong code is a uniform failure.
  - Login is throttled like the tenant login.
- **IP allowlist** via `PLATFORM_ADMIN_ALLOWED_CIDRS`. It is empty by default,
  which **disables** the panel. The panel is off until an operator turns it on.
- **The audit row is written in the same transaction** as the action. If the
  audit insert fails, the action fails too.
- **No `NEXT_PUBLIC_*` flag reveals the panel.** The frontend route exists, but
  the API refuses.

## Effort and rollout

About 1,500 lines with tests. It splits into PRs:

1. identity, login and TOTP;
2. directory and suspend;
3. health and audit view;
4. frontend.

The first platform admin would be created by a CLI script on the server
(`scripts/create_platform_admin.py`). It is never seeded and never created
through the web.

## Owner decision needed

1. Approve **option A, B or C** for the identity.
2. Approve adding `PlatformTenantDirectory` to CLAUDE.md §4 as a
   request-path exception, or choose option C, which needs none.
3. Confirm scope: in or out for impersonation (recommendation: **out**).

Until then, E5 stays open. The agent moves on to the next Track E item.
