# Admin Control Center (D-018, D-019)

Status: **phases 1–2 built**, phases 3–10 in progress. The platform console
from Track E5 ([E5 doc](../track-e/E5_PLATFORM_ADMIN.md)) grows into a full
operator console, one tested pull request per phase.

## Owner decisions

| Date | Decision |
|---|---|
| 2026-10-08 (D-018) | Roles, sessions, re-authentication, richer audit. Impersonation stays out. |
| 2026-10-09 (D-019) | **Operators may view and manage complete workspace data**: users, stores, products, drafts, listings, orders, inventory, jobs, billing, integrations, notifications and audit records. This supersedes D-018's "read-only". Secrets stay hidden. Sensitive actions need re-authentication, a reason and an immutable audit row. No arbitrary code execution. No deletion of customer data without a protected workflow. A support session only if it is safe. |

**How operators reach workspace data.** An operator request names one
workspace. `platform_workspace` then:

- checks the permission;
- audits the visit as `workspace_viewed`, recording the route;
- sets the tenant context to that workspace.

Everything after that runs through the merchant's own tenant-scoped
repositories. No unscoped query is added for workspace data, and one
request cannot span two workspaces. Cross-workspace numbers come only from
`PlatformMetrics`, which returns counts and never rows.

## Phases

| Phase | Scope | State |
|---|---|---|
| 1 | Operator roles and permission matrix, sessions with revoke, re-authentication, richer audit | Merged (#95) |
| 2 | Live dashboard; workspace entry (D-019); routed console with sidebar | Built (this PR) |
| 3 | Workspace drill-down: users, stores, products, drafts, listings, orders, inventory, notifications; filters, pagination, CSV export | Not started |
| 4 | User controls and support sessions | Not started |
| 5 | Stores and integrations: pause/resume (enforced), syncs, webhooks | Not started |
| 6 | Catalogue and order actions | Not started |
| 7 | Jobs: failed and stuck detection, retry, cancel | Not started |
| 8 | Billing: trial extension, plan override, feature flags | Not started |
| 9 | Audit and security centre; immutable audit at the database | Not started |
| 10 | System settings: maintenance mode, announcements, broadcasts | Not started |

## Phase 1 as built

### Roles and permissions

The matrix lives in code (`app/core/platform_permissions.py`), not in the
database. Changing who may do what is a security change and goes through
review. An operator's `role` column only names a row of the matrix; an
unknown role grants nothing.

| Permission | Super admin | Admin | Support | Finance | Operations | Auditor |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| `dashboard.read`: platform-wide metrics and health | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `tenants.read`: workspace list | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `tenants.suspend`: suspend or reactivate a workspace | ✓ | ✓ | | | ✓ | |
| `workspace.data.read`: everything inside one workspace | ✓ | ✓ | ✓ | | ✓ | ✓ |
| `support.session`: open a support session (needed for workspace changes) | ✓ | ✓ | ✓ | | ✓ | |
| `users.manage` | ✓ | ✓ | ✓ | | | |
| `stores.manage` | ✓ | ✓ | | | ✓ | |
| `catalog.manage` | ✓ | ✓ | | | ✓ | |
| `orders.manage` | ✓ | ✓ | | | ✓ | |
| `jobs.read` | ✓ | ✓ | ✓ | | ✓ | ✓ |
| `jobs.manage` | ✓ | ✓ | | | ✓ | |
| `billing.read` | ✓ | ✓ | | ✓ | | ✓ |
| `billing.manage` | ✓ | ✓ | | ✓ | | |
| `operators.read`: operator list and their sessions | ✓ | ✓ | | | | ✓ |
| `operators.manage`: role change, deactivate, end sessions | ✓ | | | | | |
| `audit.read` | ✓ | ✓ | | | | ✓ |
| `audit.export` | ✓ | ✓ | | | | ✓ |
| `settings.manage`: maintenance mode, announcements, broadcasts | ✓ | | | | | |

The matrix is code (`app/core/platform_permissions.py`); unit tests pin
the rules that matter:
- only the super admin manages operators and settings;
- the auditor holds only read permissions;
- every role that can change a workspace can also open a support session
  and read workspace data.

- **Server-side enforcement.** Each route declares its permission as a
  dependency (`require_platform_permission`). The console hides what a role
  cannot do, but only for convenience.
- **Refusals are audited.** A refusal returns 403 and writes a
  `permission_denied` audit row with outcome `failure`, in its own
  transaction.

### Sessions

- **One session per sign-in.** Every sign-in opens a row in
  `platform_admin_sessions`, and the token carries its id (`sid`).
- **Checked on every request.** The session must exist, belong to the
  operator, be unrevoked and unexpired, and the operator must be active.
  Revoking a session ends access at once, not at token expiry.
- **Old tokens refused.** Tokens issued before this change have no `sid`
  and are refused, so every operator signs in again once.
- **What ends a session:**
  - **Sign-out** revokes it on the server.
  - **The operator**, for their own other sessions.
  - **A super admin**, for all of another operator's sessions.
  - **A role change or a deactivation** revokes all of the target's
    sessions immediately.
- **Last seen** is recorded at most once a minute per session.

### Re-authentication

- **Which actions.** Changing access needs the password and a **new** TOTP
  code, entered in this session within the last 10 minutes:
  - suspending or reactivating a workspace;
  - every operator-management action.
- **Refusal.** Without it the server answers 403 `reauth_required`. The
  console then asks for both and retries the action once.
- **Replay.** The code is consumed like a sign-in code, so it cannot be
  replayed.
- **Rate limit.** Re-authentication has its own limit: 10 per 15 minutes
  per address.

### Protections on operator management

- **Nobody changes their own access.** No operator can change their own
  role or deactivate themselves; another operator must do it.
- **One super admin always remains.** The last active super admin cannot
  be demoted or deactivated. Every privilege change locks the active super
  admin rows first, so two super admins demoting each other at the same
  moment cannot both succeed.
- **Creating accounts stays on the server.**
  `scripts/create_platform_admin.py` now takes `--role`, which defaults to
  `super_admin`. There is still no web path that creates an operator.

### Audit

Each `platform_admin_audit` row now also records:

- the user agent and the request id (which matches the server log line);
- the actor's role at that moment;
- the outcome (`success` or `failure`);
- a generic target (`workspace`, `operator` or `session`, plus its id).

Existing rows stay valid (migration `0052`).

### Admin control inventory (phase 1)

| Control | Route | Permission | Re-auth | Audited as |
|---|---|---|:-:|---|
| Sign in | `POST /platform/auth/login` | — | | `login_succeeded` / `login_failed` |
| Sign out | `POST /platform/auth/logout` | signed in | | `logout` |
| Re-authenticate | `POST /platform/auth/reauth` | signed in | | `reauth_succeeded` / `reauth_failed` |
| My sessions | `GET /platform/auth/sessions` | signed in | | — |
| End one of my sessions | `POST /platform/auth/sessions/{id}/revoke` | signed in | | `session_revoked` |
| Operator list | `GET /platform/operators` | `operators.read` | | — |
| An operator's sessions | `GET /platform/operators/{id}/sessions` | `operators.read` | | — |
| Change role | `POST /platform/operators/{id}/role` | `operators.manage` | ✓ | `operator_role_changed` |
| Deactivate / reactivate | `POST /platform/operators/{id}/deactivate`, `…/reactivate` | `operators.manage` | ✓ | `operator_deactivated` / `operator_reactivated` |
| End all their sessions | `POST /platform/operators/{id}/sessions/revoke` | `operators.manage` | ✓ | `operator_sessions_revoked` |
| Workspace list | `GET /platform/tenants` | `tenants.read` | | — |
| Workspace health | `GET /platform/tenants/{id}/health` | `tenants.read` | | — |
| Suspend / reactivate workspace | `POST /platform/tenants/{id}/suspend`, `…/reactivate` | `tenants.suspend` | ✓ | `tenant_suspended` / `tenant_reactivated` |
| Audit log | `GET /platform/audit` | `audit.read` | | — |
| Any refusal for a missing permission | — | — | | `permission_denied` (failure) |

Reads are not audited in phase 1. Phase 3 audits every view into a
workspace, as decided.

## Phase 2 as built

- **Dashboard** (`GET /platform/dashboard`, `dashboard.read`). It gives
  live counts from the database:
  - workspaces by status and new this week or month;
  - active and new users;
  - stores by status and platform;
  - products, and listings by status;
  - orders imported in the last day and week;
  - subscriptions by plan and status, and trials ending this week;
  - failures in the last 24 hours, by kind;
  - stuck jobs:
    - order and inventory syncs `running` for over 30 minutes, which
      block that workspace's next sync;
    - AI pipeline runs with no heartbeat for 20 minutes;
  - open operator sessions, and refused operator actions in 24 hours;
  - workspace signups per day for 30 days, and orders per day for 14 days,
    with every day present, zeros included;
  - system: database (on the request's own connection), Redis, and the
    schema revision.

  The console refreshes it every minute.
- **Workspace entry** (`GET /platform/workspaces/{id}`,
  `workspace.data.read`). It shows users, stores, products, orders and
  listings by status, the subscription summary and 24-hour health, all read
  through tenant-scoped repositories. The Stripe customer id is reduced to
  "has one: yes/no". Every visit writes `workspace_viewed`.
- **Console.** It is now a routed app with a sidebar filtered by
  permission: Dashboard, Workspaces, workspace page with tabs, Operators,
  Audit log, My sessions. Suspend and reactivate moved onto the workspace
  page.
- **Redis probe.** `check_redis_health` now also reports a client error that
  is not a `RedisError` as "down" instead of raising.

### Verified (phase 2)

`test_platform_console_dashboard.py` covers:

- the dashboard counts two newly registered workspaces and their users;
- the dashboard reports database and schema;
- 31 and 14 daily points;
- every role reads the dashboard;
- the overview counts only its own workspace, carries no secret field, and
  is audited with its route;
- Finance gets 403 inside a workspace;
- an unknown workspace is 404 and not audited as a view.

Playwright covers the dashboard numbers, opening a workspace, suspension
with re-authentication, and Finance's restricted navigation.

## Verified (phase 1)

- **Unit tests:**
  - `test_platform_permissions.py`: the matrix, including that only the super
    admin manages operators and that an unknown role grants nothing;
  - `test_platform_tokens.py`: the session claim, and that a token without
    one is refused.
- **Integration tests on real Postgres, migrations applied:**
  `test_platform_admin_control.py` (14 tests) covers:
  - `/me` reports the role and permissions;
  - sign-out kills the token at once;
  - ending another of your own sessions, and 404 for someone else's;
  - a deactivated operator is refused at once;
  - Support and Finance get 403 on the audit log and on suspension, and each
    refusal is audited;
  - an Admin cannot manage operators;
  - suspension needs re-authentication, which refuses a wrong password and a
    replayed code;
  - a role change ends the target's sessions and is audited with
    before/after, role, address and user agent;
  - self-changes are refused;
  - deactivation ends sessions, and reactivation does not bring them back;
  - the last super admin is protected;
  - no secret fields appear in responses;
  - the audit view carries the request context.
- **Existing tests updated:** the existing E5 tests (auth, directory, health)
  were updated and pass. That is 46 platform tests in all.
- **Browser tests:** `platform-console.spec.ts` (Playwright, mocked API)
  covers:
  - re-auth then retry on suspend;
  - a Support operator sees no Operators or Audit tab and no suspend
    button;
  - a role change after re-authentication;
  - no self-management controls;
  - ending another session;
  - server-side sign-out;
  - the switched-off message.

## Not verified

- **No real browser run against the live API.** The console has not been
  used against the running stack with a real authenticator. The owner's
  existing operator becomes `super_admin` through the migration default, and
  must sign in again.
- **Concurrent demotion.** The guard for two super admins demoting each
  other at once relies on the row locks described above. No test runs the
  two requests concurrently.
