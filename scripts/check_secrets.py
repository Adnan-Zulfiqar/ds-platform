#!/usr/bin/env python3
"""Fail the build on committed secrets and insecure deployment defaults.

**Deliberately not a generic scanner.** Entropy heuristics on a repository this
size produce dozens of hits — base64 in fixtures, hashes in lockfiles, UUIDs in
tests — and a check that cries wolf is one people learn to skip. Every rule here
targets a specific mistake that has either already happened in this repository
or would be unrecoverable if it did.

Exit code 1 means a real risk. There is no "warning" level on purpose.

Run:  python scripts/check_secrets.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

#: Fernet keys printed in this repository's test suite, and therefore public.
#: They decode to the literal ASCII "test-key-N-NEVER-USE-IN-PROD-!!!".
PUBLISHED_TEST_KEYS = (
    "dGVzdC1rZXktMS1ORVZFUi1VU0UtSU4tUFJPRC0hISE=",
    "dGVzdC1rZXktMi1ORVZFUi1VU0UtSU4tUFJPRC0hISE=",
)

#: The one signing key that is safe to publish, because startup refuses it in
#: any deployed environment.
LOCAL_PLACEHOLDER_KEY = "insecure-local-development-key-change-me"

#: Files allowed to contain the published test keys — they are what defines them.
TEST_KEY_ALLOWED = {
    "backend/tests/conftest.py",
    "backend/app/core/config.py",
    "backend/tests/unit/test_security_hardening.py",
    "scripts/check_secrets.py",
    ".github/workflows/ci.yml",
}

#: Secret-bearing variables that must never carry a value in a committed file.
SECRET_VARIABLES = (
    "SECURITY_ENCRYPTION_KEYS",
    "ALIEXPRESS_APP_SECRET",
    "SHOPIFY_API_SECRET",
    "SHOPIFY_API_KEY",
    "REDIS_PASSWORD",
)

failures: list[str] = []


def fail(rule: str, detail: str) -> None:
    failures.append(f"{rule}: {detail}")


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout
    return [line for line in out.splitlines() if line]


def read(rel: str) -> str | None:
    path = REPO / rel
    try:
        if not path.is_file() or path.stat().st_size > 2_000_000:
            return None
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None


def check_no_env_file_tracked(files: list[str]) -> None:
    """A committed `.env` is the single worst outcome this script exists for."""
    for rel in files:
        name = Path(rel).name
        if name == ".env" or (name.startswith(".env.") and not name.endswith(".example")):
            fail("env-file-tracked", f"{rel} is committed and may contain real secrets")


def check_published_keys_not_leaked(files: list[str]) -> None:
    """The test keys are public. Anywhere else they imply a real deployment."""
    for rel in files:
        if rel in TEST_KEY_ALLOWED:
            continue
        content = read(rel)
        if content is None:
            continue
        for key in PUBLISHED_TEST_KEYS:
            if key in content:
                fail(
                    "published-key-outside-tests",
                    f"{rel} contains a Fernet key published in the test suite",
                )


def check_example_files_hold_no_secrets(files: list[str]) -> None:
    """A template with a real value in it stops being a template.

    Scans example files on disk rather than only tracked ones. Verifying this
    check found the reason: a brand-new template is untracked until its first
    commit, so a tracked-files-only scan would wave through exactly the moment
    the mistake is most likely — while the file is being written.
    """
    pattern = re.compile(rf"^({'|'.join(SECRET_VARIABLES)})=(.+)$", re.MULTILINE)

    on_disk = {
        str(path.relative_to(REPO)).replace("\\", "/")
        for path in REPO.glob("**/*.example")
        if ".git" not in path.parts and "node_modules" not in path.parts
    }

    for rel in sorted(set(files) | on_disk):
        if not rel.endswith(".example"):
            continue
        content = read(rel)
        if content is None:
            continue
        for match in pattern.finditer(content):
            variable, value = match.group(1), match.group(2).strip()
            if value and not value.startswith("#"):
                fail(
                    "example-holds-a-value",
                    f"{rel} sets {variable} to a non-empty value; templates must be blank",
                )


def check_production_template_is_complete() -> None:
    """The template is the deployment checklist. A missing line is a gap."""
    rel = ".env.production.example"
    content = read(rel)
    if content is None:
        fail("missing-template", f"{rel} is absent; deployments have no checklist")
        return

    required = (
        "ENVIRONMENT=production",
        "SECURITY_SECRET_KEY=",
        "SECURITY_ENCRYPTION_KEYS=",
        "SECURITY_COOKIE_SECURE=true",
        "LOG_INCLUDE_REQUEST_BODY=false",
        "POSTGRES_PASSWORD=",
        "RABBITMQ_PASSWORD=",
    )
    for line in required:
        if line not in content:
            fail("template-incomplete", f"{rel} is missing `{line}`")

    if LOCAL_PLACEHOLDER_KEY in content:
        fail("template-holds-placeholder", f"{rel} contains the local placeholder signing key")


def check_compose_has_no_secret_defaults() -> None:
    """`${SECRET:-value}` silently substitutes a published password.

    The application cannot detect it — it receives a working DSN — so this is
    the only place the mistake can be caught.
    """
    content = read("docker-compose.yml")
    if content is None:
        return

    for number, line in enumerate(content.splitlines(), start=1):
        if line.lstrip().startswith("#"):
            continue
        if re.search(r"\$\{[A-Z_]*(PASSWORD|SECRET|_KEY)[A-Z_]*:-", line):
            fail(
                "compose-secret-default",
                f"docker-compose.yml:{number} gives a secret a default value",
            )


def main() -> int:
    files = tracked_files()

    check_no_env_file_tracked(files)
    check_published_keys_not_leaked(files)
    check_example_files_hold_no_secrets(files)
    check_production_template_is_complete()
    check_compose_has_no_secret_defaults()

    print(f"Checked {len(files)} tracked files against 5 rules.")
    print()

    if not failures:
        print("  PASS - no committed secrets or insecure deployment defaults.")
        return 0

    print(f"  FAIL - {len(failures)} issue(s):")
    for failure in failures:
        print(f"    {failure}")
    print()
    print("  Each rule targets a specific unrecoverable mistake. If one is a")
    print("  false positive, fix the rule rather than ignoring the run.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
