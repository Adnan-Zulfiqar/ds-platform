# DP-CR-008 — Nested `.env` files kept out of Docker build contexts (PR #32)

- **Original IDs:** SEC-001 (found in this programme).
- **Base / head:** develop `2b71f65` → `2fd705c`; PR [#32](https://github.com/Adnan-Zulfiqar/ds-platform/pull/32).
- **Before:** `.env` / `.env.*` matched only the context root. `backend/.env` became `/app/.env` in the backend image (confirmed in the running local image); `frontend/.env.local` entered the frontend context.
- **After:** `**/.env`, `**/.env.*`, `!**/.env.example`.
- **Tests:** `backend/tests/unit/test_dockerignore_env_files.py` evaluates the real file with Docker's last-match-wins rules: 5 of 10 cases fail on the old file, all 10 pass on the new one.
- **Commands:** BuildKit probe (`COPY . /ctx`, then `find /ctx -name '.env*'`) before and after.
- **Limitations:** images already built locally keep `/app/.env` until rebuilt; CI images never contained it.
- **Author verification:** PASS (local); PR CI pending.
- **Independent review:** Cursor, 2026-10-02, on `e63508e` — implementation ACCEPTED WITH NON-BLOCKING NOTES; release NOT READY ([report](../INDEPENDENT_REVIEW_e63508e.md))
