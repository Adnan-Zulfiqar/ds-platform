# Track E5 — platform admin panel: proposal (needs owner approval)

Status: **proposal only. Nothing built.** Blocker **B-013**.

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
