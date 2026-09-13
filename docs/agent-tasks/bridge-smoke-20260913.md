# Disposable bridge smoke test

## Manager task
Prove Claude can make one bounded documentation edit and Cursor can review it.

## Scope
Only docs/agent-tasks/bridge-smoke-20260913.md.

## Acceptance criteria
Replace the exact line `Worker result: pending` with `Worker result: Claude patch received`.
Do not change any other file or any other line. Cursor reports whether the exact line is present.

## Forbidden actions
No application changes, test changes, workflow changes, real environment or credential reads, production access, provider calls except the authorized Claude/Cursor worker services, merges, deployment, or new features. Never claim application readiness. This smoke PR is never merged.

## Verification
Manager checks exact diff and workflow completion. No application commands required.

Worker result: pending
