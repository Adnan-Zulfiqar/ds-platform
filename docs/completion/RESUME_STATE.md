# Resume state

Single continuation point after an interruption. Overwritten, not appended.

**Updated:** 2026-10-02 — second handoff for Cursor review.

| Item | Value |
|---|---|
| Candidate | `develop` @ `9a62b9e818350fa8dafc16276a3ea0200411971b` (CI 36995948409 10/10) |
| `main` | `3ce66d4` — untouched |
| Untracked files to preserve | `C:\Projects\ds-platform\AGENTS.md`; root `.env` (local-only key, D-004); `backend/.env` |

## Running resources owned by this work

- Compose project **`dp-candidate`** (http://localhost:18080, API :18000):
  containers `dp-candidate-*`, volumes `dp-candidate_postgres_data`,
  `dp-candidate_rabbitmq_data`, images `dp-candidate-*`. Stop with the
  commands in `docs/operations/LOCAL_CANDIDATE_STACK.md`.
- Image `dp-e2e-clean` (harness). Renamed directories
  `%LOCALAPPDATA%\Docker\run.stale-*` and
  `%LOCALAPPDATA%\docker-secrets-engine.stale-*` (B-008) — safe to delete
  once Docker has run normally for a while.

## Next safe action

Hand the candidate and `docs/reviews/cursor/CURSOR_REVIEW_INDEX.md` to
Cursor. On acceptance: tag `phase-9-complete` on the accepted SHA (D-008).
Owner: rebuild the `droppilot` stack from `develop` (B-007).
