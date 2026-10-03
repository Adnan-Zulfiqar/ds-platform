# Track E4 — team invitations

Status: **implemented, and verified against a disposable stack with the
recording email provider.** No real mailbox was used.

## What it does

- **Settings → Team.** Every role sees the roster. Owners and admins can also
  invite an address as admin, member or viewer, see the open invitations, and
  revoke them.
- **The email.** The recipient gets a link, `/invite#token=<tenant id>.<secret>`.
  The secret is 256 random bits. The link works once and expires in 7 days.
- **Accepting.** The recipient sets a name and password and accepts the
  Terms and Privacy Notice (with the same version check as registration).
  They then join the workspace with the invited role, already verified, and
  are signed in.
- **Re-inviting.** Inviting an address that already has an open invitation
  rotates its secret and role, so the old link stops working. Revoking
  stops the link working too.
- **Limits.**
  - Ownership is never granted by invitation.
  - A workspace can have at most 50 open, unexpired invitations.
  - Invite requests are limited to 30 per user per hour.
  - Preview and accept requests are limited to 30 per address per 10 minutes.

## Decisions and trade-offs

- **D-013: the link names the tenant.** Acceptance has no session, so it does
  not know the workspace.
  - **What we do:** the link carries the tenant id. The invitation is looked
    up **inside** that tenant by the SHA-256 of the secret.
  - **Why it is safe:** a forged or swapped tenant id finds nothing and
    returns 404, the same as any dead link.
  - **What we avoided:** an unscoped lookup by token hash. That would put a
    cross-tenant query on a request path, which CLAUDE.md §4 forbids.
  - **Rule 4 still holds:** the tenant is trusted only after the secret matches
    a row in it.
- **The token travels in the URL fragment, not the query string.** Browsers
  never send a fragment to a server, so the token stays out of proxy and
  access logs and out of Referer headers.
- **One address, one workspace (limitation).**
  - **Why:** login checks a password against every account with that address.
    If two accounts share a password, the second one cannot be reached
    (docs/Authentication.md).
  - **What happens:** acceptance is refused (409) when the address already
    has a DropPilot account. The check happens at acceptance, so an admin
    cannot use invites to probe whether a stranger has an account.
  - **The real fix:** tenant-qualified login. It is not in this track.
- **A failed send rolls the invitation back** (503
  `invitation_email_failed`). An invitation nobody received only blocks a
  retry.
- **Not included:**
  - Changing a member's role.
  - Deactivating or removing members.
  - Showing roles in the roster.

  `UserUpdate` exists for the first two, but they are separate decisions,
  especially around removing the last owner.

## Schema (migration `0044`, additive)

`user_invitations` (tenant-scoped):

- Columns: `email`, `role`, `token_hash` (unique), `invited_by_user_id`,
  `expires_at`, `accepted_at`, `revoked_at`, `accepted_user_id`.
- A partial unique index allows one open invitation per address per tenant.
- On user erasure, both user references are **cleared**. The workspace keeps
  the record.

## API

| Method | Path | Who |
|---|---|---|
| GET | `/api/v1/users/invitations` | admin+ |
| POST | `/api/v1/users/invitations` `{email, role}` | admin+ |
| DELETE | `/api/v1/users/invitations/{id}` | admin+ |
| POST | `/api/v1/auth/invitations/preview` `{token}` | public |
| POST | `/api/v1/auth/invitations/accept` `{token, password, names, legal}` | public |

## Verified

- `tests/integration/test_team_invitations.py` uses real Postgres via
  migrations and the stub email provider. It covers:
  - the full join;
  - single use;
  - rotation and revocation;
  - a forged tenant id;
  - expiry;
  - roles that may invite, and who may be invited;
  - an account that already exists elsewhere;
  - terms and password-strength refusals that do not use up the link;
  - email failure rolling the invitation back.
- `tests/unit/test_team_invitation_repository_scoping.py` checks tenant
  isolation on the compiled SQL.
- `frontend/tests/e2e/team-invitations.spec.ts` runs against a mocked API.

## Not verified

- An invitation email arriving in a real inbox through Resend. Deployment must
  set `EMAIL_PROVIDER=resend` and `EMAIL_APP_BASE_URL`.
