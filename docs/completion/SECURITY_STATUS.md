# Security status — dependencies and application findings

A clean audit is evidence, not proof of security. "Exposure" names the code
path the package actually reaches in this application.

Last reassessed: 2026-10-01 against `develop` @ `df0e41f` (after #27–#31
merged) and branch `fix/backend-pyjwt-urllib3`.

## Frontend (npm, `frontend/package-lock.json`)

| Package/path | Installed version | Advisory/source | Exposure | Remediation | Test evidence | Remaining status |
|---|---|---|---|---|---|---|
| `next` | 15.5.27 (was 15.1.6) | GHSA-9qr9-h5gf-34mp (CVE-2025-66478, critical) and later `next` advisories incl. GHSA-2xp9-vwfh-vxw4, GHSA-p293-qw3h-jr36 (critical, fixed ≥15.5.24) | production server | PR #28, merged `249e18f` | PR CI 36933835735 10/10 at `e2095f5` | Fixed |
| `react`, `react-dom` | 19.0.8 (was 19.0.0) | react-server-dom RSC advisories through GHSA-wx67-qw84-cm4g | production | PR #28 | same | Fixed |
| `sharp` via `next` | per next 15.5.27 | GHSA-f88m-g3jw-g9cj, GHSA-rgj7-g3m4-5g8c | image optimisation, unused | PR #28 | same | Fixed |
| `axios` | 1.20.0 (was 1.19.0) | 12 advisories, 7 high | production API client | PR #29, merged `f87a1f5` | PR CI 36877363551 10/10 | Fixed |
| `@tiptap/*` | 3.31.4 (was 3.30.1); `prosemirror-model` 1.25.12, `prosemirror-view` 1.42.6 moved with it | GHSA-j95f-988m-3j2f (high), GHSA-cp6q-959q-f8rh | production description editor | PR #30, merged `4182219` | PR CI 36886931099 attempt 2, 10/10 | Fixed |
| `js-yaml` | 4.3.2 | GHSA-5p4m-2wfm-xmqj, GHSA-2883-xcg3-v3hh | dev/CI (eslintrc) | PR #30 | same | Fixed |
| `brace-expansion` | 1.1.21 / 5.0.12 | GHSA-q2hr-2g5m-vwhr, GHSA-qhr7-859c-m2p7, GHSA-6j4f-fj2g-mc7p | dev/CI (minimatch) | PR #30 | same | Fixed |
| `postcss` bundled in `next` | 8.4.31 (exact pin in next 15.5.27) | GHSA-6g55-p6wh-862q (high), GHSA-r28c-9q8g-f849 (high), GHSA-qx2v-qp2m-jg93, GHSA-fxqj-rqcc-2cmp | build time; processes first-party CSS only. Build-only is not automatically harmless — a malicious CSS input would need to enter the repository | None available in 15.x: 15.5.27 is the newest 15.x and pins 8.4.31; next 16.3.8 pins 8.5.23. Requires the Next 16 major in its own focused change | `npm audit` on integrated develop: only this path remains | **OPEN — release blocker until assessed for Next 16** |
| Next.js postponed fixes | — | Next.js blog "September 2026 Security Release" (2026-09-30): one critical and one high fix postponed pending upstream coordination, no public detail | unknown | Watch the Next.js security blog | — | **OPEN — unconfirmed scope** |

Integrated lockfile check (develop `df0e41f`): `npm ci` on `node:22-alpine`
(npm 10.9.9) exit 0, lockfile unchanged; 40 `libc` entries; `npm audit
--omit=dev` → 2 vulnerabilities (1 moderate `next` aggregate, 1 high
bundled `postcss`).

## Backend (pip, `backend/requirements/{runtime,dev}.txt`)

Tool: `pip-audit 2.10.1 --no-deps --disable-pip -r <lock>`.

| Package/path | Installed version | Advisory/source | Exposure | Remediation | Test evidence | Remaining status |
|---|---|---|---|---|---|---|
| `pyjwt` | 2.13.0 → 2.15.1 | 13 advisories: PYSEC-2026-4140…4152 (GHSA-2gx3-rcp4-g85q, GHSA-42vr-xj54-vc7v, GHSA-8wjv-2p76-3863, GHSA-9j54-fg26-wv3r, GHSA-9v7f-9g4p-ffgj, GHSA-ffc3-869f-jxw9, GHSA-gvp8-978c-rx2q, GHSA-jwrc-g2q2-pq5p, GHSA-hxm8-2xgr-2p9m, GHSA-p4g4-x82p-q773, GHSA-r6x4-923q-g947, GHSA-w2cx-738m-mc7w, GHSA-w6j9-cwv2-h6wq) | every authenticated request (`app/core/tokens.py`: HS256, single algorithm, `verify_signature=True`, no PyJWK/PyJWKClient). JWKS/redirect/algorithm-mixing advisories do not reach this code; signature-segment and `_load` error-translation advisories do | `fix/backend-pyjwt-urllib3` (`7fe78a9`): floor `>=2.15.0`, lock recompiled | pip-audit on the new lock: no known vulnerabilities; hash install, ruff, mypy clean; full gate in progress | Fixed on branch, PR pending. GHSA-gvp8-978c-rx2q (PYSEC-2026-4146) has no fixed release; not applicable (decode always passes `verify_signature=True` in a fresh dict) |
| `urllib3` | 2.7.0 → 2.8.0 | PYSEC-2026-4175/4176/4177 (GHSA-8988-9cw3-xx77, GHSA-gh4c-6fx4-qh6g, GHSA-vxq7-64xx-v4gw) | transitive (requests etc.): streaming decompression and HTTPS-proxy TLS | same branch, `--upgrade-package urllib3` | same | Fixed on branch |

## Application findings found during completion work

| ID | Finding | Exposure | Remediation | Evidence | Status |
|---|---|---|---|---|---|
| SEC-001 | `.dockerignore` excluded `.env` only at the context root; `backend/.env` reached the backend image as `/app/.env`, `frontend/.env.local` the frontend image | any image built from a developer checkout; CI images unaffected (no `backend/.env` in a clean checkout). The local `droppilot-backend` image contains `/app/.env` | PR #32 (`2fd705c`): `**/.env`, `**/.env.*`, `!**/.env.example` | BuildKit context probe before/after; unit test 5 failing → 10 passing | Fixed on branch, PR open. Local images need a rebuild |
