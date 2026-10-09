# Admin Control Center (D-018, D-019)

Status: **phases 1–6 built**, phases 7–10 in progress. The platform console
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
| 2 | Live dashboard; workspace entry (D-019); routed console with sidebar | Merged (#96) |
| 3 | Workspace drill-down: users, stores, products, drafts, listings, orders, inventory, notifications; filters, pagination, CSV export | Merged (#97) |
| 4 | User controls and support sessions | Merged (#98) |
| 5 | Stores and integrations: pause/resume (enforced), syncs, webhooks | Merged (#99) |
| 6 | Catalogue and order actions | Built (this PR) |
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

## Phase 6 as built

All actions need a support session and re-authentication. Each reuses the
merchant's own service, with its guards.

| Action | Route | Permission | Reuses / guard | Audited as |
|---|---|---|---|---|
| Import attempts (view) | `GET …/imports?status=` | `workspace.data.read` | tenant-scoped list | `workspace_viewed` |
| Retry a failed import | `POST …/imports/{iid}/retry` | `catalog.manage` | `ProductImportService.retry_import` (only `failed`) | `workspace_import_retried` |
| Push price and stock again | `POST …/products/{pid}/resync-listings` | `catalog.manage` | the merchant's after-commit channel push; paused stores are skipped | `workspace_listings_resync_queued` (with listing count) |
| Refresh an order | `POST …/orders/{oid}/refresh` | `orders.manage` | `OrderSyncService.refresh_order` | `workspace_order_refreshed` |
| Release a stuck supplier order | `POST …/orders/{oid}/supplier-order/release` | `orders.manage` | `SupplierOrderingService.release` (only from `placing`, D-017) | `workspace_supplier_order_released` |

- **Releasing a stuck supplier order** is recorded on the row as
  `released_by_support`, not as the merchant's own release.
- **The console warns**, and asks for a second click, before a release:
  if AliExpress did create the order, releasing it lets it be bought
  twice.
- **Failed attempts** (no supplier connection, a non-failed import) are
  audited as `failure` in their own transaction, as in phase 5.

### Verified (phase 6)

`test_platform_workspace_catalog.py` covers:

- failed imports are listed by status, and a retry that cannot run is
  audited as a failure;
- a re-push is queued and audited with its listing count;
- a stuck supplier order is released as support, and a second release is a
  409;
- an order refresh without a supplier connection is audited as a failure;
- Support gets `permission_denied`;
- another workspace's order is a 404 and untouched.

Playwright (`platform-workspace-catalog.spec.ts`) covers the release with
a two-click confirm and the exact reason.

## Phase 5 as built

### An operator pause on a store (migration `0054`)

`stores.sync_paused_at` and `sync_paused_reason`. While they are set,
**DropPilot stops writing to that store**:

- **Price and stock pushes.** The listing is skipped silently, not marked
  as an error the merchant must fix, for:
  - Shopify (`shopify.push_price_quantity`);
  - eBay (`EbayPriceQuantitySync.push`);
  - WooCommerce (`WooCommercePriceQuantitySync.push`).

  This covers automatic and merchant-triggered pushes alike.
- **New publishes** to any channel are refused by the shared
  `PublishReadinessService` with the blocker `store_paused` and a message
  saying support paused it.
- **Orders keep flowing in.** Imports and webhooks still record them, so
  nothing is lost while a store is paused.

The merchant gets a notification on pause and on resume.

**Decision recorded:** the earlier per-store switches
(`order_sync_enabled`, `inventory_sync_enabled`, `pricing_sync_enabled`)
are written by the merchant's settings but are **not enforced anywhere**.
That was found during this phase and is left unchanged.

- **Why not enforce them here.** Their exact semantics are a merchant
  product decision, and each channel combines price and stock differently.
- **What the operator gets instead.** One explicit pause, enforced at every
  write.

The unenforced switches are a known limitation.

### Actions (`stores.manage`, support session, re-authentication)

| Action | Route | Reuses | Audited as |
|---|---|---|---|
| Pause / resume | `POST …/stores/{sid}/pause`, `…/resume` | — | `workspace_store_paused` / `workspace_store_resumed` |
| Supplier order sync now | `POST …/sync/orders` | `OrderSyncService.sync_orders` (a running sync is a 409) | `workspace_order_sync_started` |
| Inventory sync now | `POST …/sync/inventory {storeId?}` | `InventorySyncService.sync`, then the same after-commit channel push as the merchant's button | `workspace_inventory_sync_started` |
| Re-register Shopify webhooks | `POST …/stores/{sid}/shopify/webhooks` | `ShopifyService.register_webhooks` (creates only what is missing) | `workspace_shopify_webhooks_reconciled` |

- **Failed actions are audited too.** A sync or registration that fails
  rolls the request back, so its attempt is written as `outcome=failure`
  in its own transaction, with the error.
- **Disconnecting a store is not offered.** Reconnecting needs the
  merchant's own OAuth consent, so a disconnect by an operator could not be
  undone by an operator. The pause covers the "stop it now" need.

### Verified (phase 5)

- `test_platform_workspace_stores.py` covers:
  - pause and resume are visible on the list and in workspace
    notifications, and audited with the reason;
  - Support is refused (`permission_denied`);
  - another workspace's store is a 404;
  - a sync that cannot start leaves a `failure` audit row.
- `test_woocommerce_price_quantity.py`, against the in-memory store fake:
  - a paused store receives no PUT and its listing status is untouched;
  - publishing to a paused store is refused with `store_paused`.
- Playwright (`platform-workspace-stores.spec.ts`) covers pause with a
  two-click confirm and the exact reason, the paused badge, and an
  inventory sync.

## Phase 4 as built

### Support sessions: the gate for every workspace change

- **What a change needs.** A change inside a workspace passes
  `platform_workspace(permission, write=True)`, which requires all three
  of:
  1. the permission;
  2. a re-authentication within 10 minutes;
  3. an **open support session for that workspace, held by this operator**.

  Without the session the server answers 403 `support_session_required`,
  and the console says so.
- **Opening one.** `POST /platform/workspaces/{id}/support-session
  {reason, minutes}` needs `support.session` and re-authentication.
  - It lasts 5–120 minutes.
  - It is stored in `platform_support_sessions` (migration `0053`).
  - Opening a new one closes the previous one.
- **Who can see it.** Opening one posts a notification **inside the
  workspace** ("DropPilot support is working in your workspace", with the
  reason), so the merchant sees it. It is audited as
  `support_session_opened`.
- **Ending one.** `POST …/support-session/end`, audited as
  `support_session_ended`. Expiry also ends it, without an action.
- **Why not impersonation.** The operator never acts as the merchant. Each
  change is a named console action, with its own permission, reason and
  audit row. This is the "support session only if it can be implemented
  safely" the owner allowed. No tenant token is ever minted.

### User controls (`users.manage`)

| Action | Route | What it does | Audited as |
|---|---|---|---|
| Disable | `POST …/users/{uid}/disable` | `is_active=false` and every refresh token revoked. **The only active owner cannot be disabled (409)**; suspend the workspace instead. | `workspace_user_disabled` |
| Enable | `POST …/users/{uid}/enable` | `is_active=true` | `workspace_user_enabled` |
| End sessions | `POST …/users/{uid}/end-sessions` | Revokes every refresh token; access tokens end within 15 minutes | `workspace_user_sessions_ended` |
| Require password reset | `POST …/users/{uid}/require-password-reset` | Clears the password hash and ends sessions. Sign-in then fails **exactly like a wrong password**, so no new state leaks. The user sets a new password with "Forgot password", and a linked Google sign-in keeps working | `workspace_user_password_reset_required` |
| Change role | `POST …/users/{uid}/role {role}` | Only `admin`, `member` or `viewer`. **An owner's role is never changed, and nobody is made owner (no ownership transfer).** Their sessions end, so the role applies now | `workspace_user_role_changed`, with before and after |
| Revoke invitation | `POST …/invitations/{iid}/revoke` | Reuses `TeamInvitationService.revoke` | `workspace_invitation_revoked` |

Every action takes a reason (3–500 characters). It is written in the same
transaction as the change, with the target user and the workspace. A user
id from another workspace is a 404, because the tenant-scoped repository
never finds it.

### Verified (phase 4)

`test_platform_workspace_users.py` (11 tests) checks:

- a change is refused first for re-authentication, then for a missing
  support session;
- opening a session is visible in the workspace's notifications and is
  audited;
- ending the session stops further changes;
- a disabled member cannot sign in and an enabled one can;
- the last active owner is protected;
- a role change is audited with before and after, an owner is refused, and
  "owner" is a 422;
- after a required reset the old password fails with the same status and
  message as a wrong one;
- another workspace's user is a 404;
- Finance and Auditor cannot open sessions;
- Operations can open a session but cannot manage users.

Playwright (`platform-workspace-users.spec.ts`) covers:

- the refusal message when no session is open;
- opening a session through re-authentication, with its exact body;
- a two-click destructive disable with its exact reason;
- no role control for an owner.

## Phase 3 as built

Every route is `GET /platform/workspaces/{id}/…`, entered through
`platform_workspace`. Each request checks `workspace.data.read`, is audited
as `workspace_viewed` with its route, and reads through tenant-scoped
repositories.

| View | Route | Filters |
|---|---|---|
| Users | `/users`. Shows roles and live session counts (batched, no N+1). | `active`, search |
| Invitations | `/invitations` | — |
| Stores | `/stores`. Shows sync switches, last sync, last error and health. | `platform`, `status`, search |
| Connections | `/connections`. Covers AliExpress, Shopify (with webhook registration) and eBay. Shows status, expiry and last error, **never a credential**. | — |
| Products and drafts | `/products?publication=published\|draft` | search |
| Product detail | `/products/{id}`: variants, images, listings | — |
| Listings | `/listings` | `status`, `store_id` |
| Orders | `/orders` | `fulfillment_status`, `source`, search |
| Order detail | `/orders/{id}`: buyer and address, items, shipments, history, supplier order | — |
| Sync runs | `/sync-runs?kind=orders\|inventory` | `status` |
| Notifications | `/notifications` | `kind`, search |
| **Export** | `/export/{users\|stores\|products\|drafts\|listings\|orders}` | — |

- **Unknown filter values.** A filter value outside the enum is a 422, not a
  500.
- **Another workspace's id** under this workspace's path is a 404, because
  the tenant predicate never finds it.
- **Exports** need re-authentication.
  - At most 5000 rows each, newest first.
  - They are audited as `workspace_data_exported` with the dataset and the
    row count.
  - Every text cell starting with `= + - @`, a tab or a carriage return is
    prefixed with `'`. Merchant data such as product titles and buyer names
    then cannot run as a spreadsheet formula.
  - Responses carry `Cache-Control: no-store`.
- **Console.** The workspace page gets tabs for Users, Stores, Products,
  Listings, Orders, Sync runs, Notifications and Export.
  - One shared table component provides search, filters, paging, totals,
    and loading, empty and error states.
  - Orders and products open in a side panel.
  - The export reads an error body that arrives as a file, so the re-auth
    prompt works there too.

### Verified (phase 3)

`test_platform_workspace_data.py` (8 tests) covers:

- the users list carries roles and sessions and no password or token hash;
- two workspaces with orders and stores, where each view shows only its own;
- another workspace's order or product id under this workspace's path is a
  404;
- the store's encrypted credentials never appear;
- all 11 list views answer and are audited with their route;
- unknown filter values are a 422;
- an export is refused without re-authentication;
- after re-authentication an export is a CSV whose formula-looking buyer
  name is neutralised, with an audit row recording 1 row;
- Auditor and Support read, Finance is refused.

Playwright (`platform-workspace-data.spec.ts`) covers the users, stores and
connections tabs, an order's detail panel, and an export through
re-authentication to the download.

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
