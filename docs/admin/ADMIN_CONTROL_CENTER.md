# Admin Control Center (D-018)

Status: **phase 1 of 8 built.** The platform console from Track E5
([E5 doc](../track-e/E5_PLATFORM_ADMIN.md)) grows into a full operator
console, one tested pull request per phase.

## Owner decisions (2026-10-08)

| Question | Decision |
|---|---|
| Can operators see inside a workspace? | **Yes, read-only and audited.** One workspace at a time; the server switches to that workspace's tenant-scoped queries; every view is audited; no secrets. Needs a CLAUDE.md §4 amendment, which lands with phase 3. |
| What may operators change? | **Safe operational actions only:** suspend or reactivate a user, force logout, require a password reset or MFA, pause or resume sync, a safe retry, extend a trial, override a plan, feature flags. **Never:** delete customer data, transfer ownership, change roles inside a workspace. |
| Impersonation ("log in as")? | **Kept out.** D-015 stands. |
| Delivery | **Phase by phase**, each a tested PR. |

## Phases

| Phase | Scope | State |
|---|---|---|
| 1 | Foundation: operator roles and permission matrix, sessions with revoke, re-authentication, richer audit | Built (this PR) |
| 2 | Live dashboard | Not started |
| 3 | Workspace list and read-only detail | Not started |
| 4 | User controls | Not started |
| 5 | Stores and integrations | Not started |
| 6 | Jobs and operations | Not started |
| 7 | Billing, trial and feature controls | Not started |
| 8 | Audit and security centre; system settings | Not started |

## Phase 1 as built

### Roles and permissions

The matrix lives in code (`app/core/platform_permissions.py`), not in the
database. Changing who may do what is a security change and goes through
review. An operator's `role` column only names a row of the matrix; an
unknown role grants nothing.

| Permission | Super admin | Admin | Support | Finance | Operations | Auditor |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| `tenants.read`: workspace list, counts, health | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `tenants.suspend`: suspend or reactivate a workspace | ✓ | ✓ | | | ✓ | |
| `operators.read`: operator list and their sessions | ✓ | ✓ | | | | ✓ |
| `operators.manage`: role change, deactivate, end sessions | ✓ | | | | | |
| `audit.read`: the operator audit trail | ✓ | ✓ | | | | ✓ |

Later phases add permissions (users, stores, jobs, billing, settings) and
grant them per role in the same review. Support and Finance can do little
today because the screens they need arrive in phases 3–7.

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
