# DP-CR-017 — pyjwt 2.15.1 and urllib3 2.8.0 (PR #34)

- **Original IDs:** SEC-002 (first backend dependency audit in this programme).
- **Base / head:** develop `2b71f65` → `7fe78a9`; PR [#34](https://github.com/Adnan-Zulfiqar/ds-platform/pull/34).
- **Finding:** `pip-audit 2.10.1` over the hash lock: 13 advisories in pyjwt 2.13.0 (fixed 2.14.0/2.15.0), 3 in urllib3 2.7.0 (fixed 2.8.0). IDs in `docs/completion/SECURITY_STATUS.md`.
- **Exposure:** `app/core/tokens.py` — HS256, one fixed algorithm, `verify_signature=True`, no PyJWK/PyJWKClient. JWKS/redirect/algorithm-mixing advisories do not reach it; decode-path advisories do. GHSA-gvp8-978c-rx2q has no fix and does not apply (fresh options dict, signature verification always on).
- **Change:** pyproject floor `pyjwt[crypto]>=2.15.0`; `update_dependency_lock.py` gains a repeatable `--upgrade-package` passthrough (uv otherwise keeps every locked version that satisfies pyproject, so a transitive fix had no sanctioned route); locks recompiled with uv 0.12.21 — only pyjwt and urllib3 changed.
- **Design choice:** a passthrough flag rather than a pyproject entry for urllib3 — urllib3 is not a direct dependency, and declaring it would make it look like one.
- **Evidence:** `check_dependency_lock` consistent; hash-verified install; pip-audit "No known vulnerabilities found"; backend gate at `7fe78a9`: ruff clean, format 468, mypy 236, alembic 0036, **pytest 3460 passed, 0 failed**. PR CI pending at the time of writing.
- **Recovery:** revert; the previous lock restores 2.13.0/2.7.0.
- **Cursor must inspect:** pyjwt 2.14/2.15 changelogs for behaviour changes on `decode` with `require`, `leeway`, `audience`, `issuer`.
- **Author verification:** PASS (local).
- **Independent review:** PENDING — NOT YET PERFORMED
