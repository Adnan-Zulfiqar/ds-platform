# DP-CR-002 — CI installs backend dependencies from the hash lock (PR #27)

- **Original IDs:** N-4.
- **Requirement:** CI tests the dependency set production installs. Range installs picked up SQLAlchemy 2.1.1 (lock 2.0.52) and produced 70 mypy errors on unchanged code.
- **Base / head:** `72e7692` → `78c7f06b89ef54029dcf8ae121c702dc96bd0c76`; merge `b838fdb`.
- **Change:** backend and celery jobs run `check_dependency_lock.py --only dev`, `pip install --require-hashes --no-deps -r requirements/dev.txt`, `pip install --no-deps -e .`; the e2e job uses `runtime.txt`; caches key on the lockfiles.
- **Tests:** `backend/tests/unit/test_ci_installs_from_lock.py` (3).
- **Results:** PR CI 36855424195 10/10. Playwright 716 passed with **6 retry-passes** on develop-baseline specs (N-5); five share the root cause fixed in #31 (DP-CR-006).
- **Security:** CI hash pinning now matches the Docker build.
- **Recovery:** revert the merge commit.
- **Cursor must inspect:** every job that installs Python uses the lock.
- **Author verification:** PASS.
- **Independent review:** PENDING — NOT YET PERFORMED
