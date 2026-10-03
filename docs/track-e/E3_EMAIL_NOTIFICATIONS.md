# Track E3 — notifications by email

Status: **implemented, verified against a disposable stack with the recording
email provider.** No real mailbox was used; sending through Resend in
production is the same code path as password-reset email, which was already
live-capable (`EMAIL_PROVIDER=resend`, `EMAIL_RESEND_API_KEY`).

## What it does

- Every notification except `info` is written with `email_status='pending'`.
- A beat task (`notifications.send_emails`, every 2 minutes) walks each
  active workspace and mails its pending notifications (50 per workspace per
  run), then marks each row `sent`, `skipped` (nobody wanted it) or
  `failed` (the provider refused).
- **Who receives it:** a notification aimed at one user goes only to that
  user (if active). A workspace-wide notification goes to active owners and
  admins.
- **What they receive:** each user chooses kinds under Settings →
  Notifications. Until they choose, the default is the failures that need
  action: `sync_failed`, `webhook_failure`, `task_failure`,
  `automation_failed`. Routine progress stays in-app unless opted into.
- Links use `EMAIL_APP_BASE_URL` (default `http://localhost:3000`), never a
  request header. **Deployment must set it** to the public web origin.

## Decisions and trade-offs

- **Outbox, not send-in-`notify`.** Notifications are raised inside
  transactions that may roll back; a row that commits is a notification that
  happened. The cost is up to ~2 minutes of delay.
- **At-least-once.** A crash after the provider accepted a message but
  before the status commit can resend it next sweep. Rows are taken with
  `FOR UPDATE SKIP LOCKED`, so concurrent sweeps never double-send by race.
- **A provider failure is recorded, not retried.** A broken provider must
  not turn into a backlog mailed in a burst hours later. The in-app
  notification is unaffected.
- **Existing rows are never emailed.** The migration leaves old rows with a
  null status.
- **No digest, no per-workspace switch.** One email per notification per
  recipient. If volume becomes a complaint, a digest is the next step.

## Schema (migration `0043`, additive)

- `notifications.email_status` (varchar 16, nullable), `emailed_at`;
  partial index `(tenant_id, created_at) WHERE email_status='pending'`.
- `notification_email_preferences` (tenant-scoped; one row per user;
  `kinds` jsonb). `downgrade()` drops exactly these.

## API

- `GET /api/v1/notifications/email-preferences` → `{kinds, available}`
- `PUT /api/v1/notifications/email-preferences` `{kinds}` — the caller's own
  preferences; any role. Unknown kinds → 422.

## Verified

- `tests/integration/test_notification_email.py` (real Postgres via
  migrations, stub provider): default recipients and kinds, opt-in via the
  API, user-targeted delivery, provider failure, no double send, tenant
  isolation of the outbox.
- `frontend/tests/e2e/notification-email-preferences.spec.ts` (mocked API).

## Not verified

- Delivery through Resend to a real inbox (owner: set the Resend key and
  `EMAIL_APP_BASE_URL`, trigger a failure, check the inbox).
