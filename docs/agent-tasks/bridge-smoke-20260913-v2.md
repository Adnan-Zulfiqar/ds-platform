# Disposable hardened bridge smoke test

## Manager task
Prove the hardened bridge works end to end: Claude makes one bounded documentation edit, then Cursor independently reviews the exact pushed HEAD.

## Scope
Only docs/agent-tasks/bridge-smoke-20260913-v2.md.

## Acceptance criteria
Replace the exact final standalone line `Worker result: pending` with `Worker result: hardened bridge verified`.
Do not change any other line or file. Cursor reports the exact final Worker result line and confirms the diff is documentation-only.

## Forbidden actions
No application changes, test changes, workflow changes, credential reads, production access, merges, deployment, or provider calls except the authorized Claude/Cursor worker services. Never claim application readiness. This smoke PR is never merged.

## Verification
The active ChatGPT manager checks the exact diff, Claude apply result, Cursor read-only guard, preserved report artifact/summary, and workflow conclusions. No application commands are required.

Worker result: pending
