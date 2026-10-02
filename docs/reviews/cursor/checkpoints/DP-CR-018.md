# DP-CR-018 — Request transaction commits before the response is sent (PR #35)

- **Original IDs:** N-5 follow-ups (`global-rules-impact.spec.ts:300`, `:635`; probable cause of `draft-editor-real-conflict.spec.ts:211`). New defect found in this programme.
- **Requirement:** a 2xx means the write is durable; a client may act on it at once. A failed commit is never reported as success. (`get_db_session`'s docstring already promised "a request either fully succeeds or leaves no trace".)
- **Base / head:** develop `df0e41f` → `4d6c254` (`50a0c30` fix + `4d6c254` formatting); PR [#35](https://github.com/Adnan-Zulfiqar/ds-platform/pull/35).
- **Before:** on the locked FastAPI 0.141.1, a default ("request"-scoped) yield dependency's exit code runs after the response is sent. `get_db_session` commits there. Measured directly: response at 0.03 s, teardown at 1.03 s.
- **After:** `Depends(get_db_session, scope="function")` — commit after the handler, before the response.
- **Why each flake fits:**
  - `global-rules-impact` :300 / :635 — `POST /auth/register` returned 201; the next `POST /global-rules/pricing` failed with a foreign-key `validation_error` ("references a resource that does not exist"): the user/tenant rows were not yet committed. Both CI logs show exactly this error.
  - `draft-editor-real-conflict` :211 — the hook calls `loginViaApi` (inserts a refresh-token row) then navigates; the app's first `/auth/refresh` looks that row up (`AuthService.refresh` → `get_by_token`); a not-yet-committed row is `refresh_token_not_recognised` → 401 → session over → editor never appears. **Mechanism inferred from code, not reproduced.**
- **Production impact (beyond tests):** any client chaining calls (register → create, refresh → refresh after rotation) could hit missing rows; users could be logged out by back-to-back refreshes; a commit failure (connection loss) was reported as success.
- **Design choice and alternatives:** function scope on the one yield dependency. Alternatives considered: a middleware that commits (duplicates the transaction owner), explicit `commit()` in handlers (violates "handlers never commit"), pinning FastAPI below 0.118 (blocks security updates). FastAPI raises `DependencyScopeError` at start-up if a request-scoped yield dependency depends on a function-scoped one — verified — so a future dependency cannot silently undo the ordering. No other API dependency yields.
- **Tenant/security:** none changed; error envelope unchanged (a failed commit now returns the existing 5xx envelope).
- **Tests:** `backend/tests/unit/test_commit_before_response.py` (2). Without the fix: `['handler', 'response-201', 'commit', 'close']` and a failed commit returned 201 — both fail. With it: both pass.
- **Commands and results:** backend gate (git-archive of the commit, non-root user, disposable Postgres 17 / Redis 7) at `50a0c30`: pytest **3462 passed**, mypy clean, alembic 0036; ruff found 2 E501 in the new test → fixed in `4d6c254`, where ruff, format (469 files), mypy (236) and the new tests pass. PR CI pending at the time of writing.
- **Known limitations:** the third flake's link is inferred; it stays open until CI history shows it gone. Request latency now includes the commit (it did before too, just after the bytes left).
- **Recovery:** revert the one-line dependency change.
- **Cursor must inspect:** that no endpoint relied on work after the response (no `BackgroundTasks` use found); streaming responses (none use `DbSession` today).
- **Author verification:** PASS (local gate; PR CI pending).
- **Independent review:** Cursor, 2026-10-02, on `e63508e` — implementation ACCEPTED WITH NON-BLOCKING NOTES; release NOT READY ([report](../INDEPENDENT_REVIEW_e63508e.md))
