from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path


REPOSITORY = "Adnan-Zulfiqar/ds-platform"
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
MAX_REQUEST_BYTES = 8_000
MAX_CONTEXT_BYTES = 64_000
MAX_REPORT_CHARS = 55_000


class DebuggerInputError(ValueError):
    """Raised before Cursor is contacted when a requested review is unsafe."""


def _read_limited(path: Path, limit: int, label: str) -> str:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise DebuggerInputError(f"could not read {label}: {exc}") from exc
    if len(data) > limit:
        raise DebuggerInputError(f"{label} exceeds its {limit}-byte limit")
    return data.decode("utf-8", errors="replace").strip()


def _validate_sha(value: str, label: str) -> str:
    candidate = value.lower()
    if SHA_PATTERN.fullmatch(candidate) is None:
        raise DebuggerInputError(f"{label} must be a full commit SHA")
    return candidate


def _validate_args(args: argparse.Namespace) -> tuple[str, str]:
    if args.repository != REPOSITORY:
        raise DebuggerInputError("unexpected repository")
    if args.pr_number <= 0:
        raise DebuggerInputError("PR number must be positive")
    return (
        _validate_sha(args.base_sha, "base SHA"),
        _validate_sha(args.head_sha, "head SHA"),
    )


def _validate_workspace(path: Path) -> Path:
    candidate = path.resolve()
    if not candidate.is_dir() or not (candidate / ".git").exists():
        raise DebuggerInputError("workspace must be the checked-out PR repository")
    return candidate


def _redact(text: str, secret: str) -> str:
    cleaned = text.replace(secret, "[REDACTED]") if secret else text
    cleaned = re.sub(
        r"(?i)(postgres(?:ql)?(?:\+\w+)?://[^:\s/]+:)[^@\s]+@",
        r"\1[REDACTED]@",
        cleaned,
    )
    return cleaned[:MAX_REPORT_CHARS]


def _prompt(
    *,
    repository: str,
    pr_number: int,
    base_sha: str,
    head_sha: str,
    request: str,
    ci_context: str,
) -> str:
    extra = request or (
        "Diagnose the current PR, prioritising failed checks and likely regressions."
    )
    checks = ci_context or "No check summary was available; state that limitation."
    return f"""You are the read-only debugger for DropPilot AI.

Repository: {repository}
Pull request: #{pr_number}
Base SHA: {base_sha}
Head SHA: {head_sha}

Manager request:
{extra}

GitHub check summary:
{checks}

The trusted base-to-head patch is stored at `.agent-bridge/pr.diff` inside the
workspace. Read it before opening the affected files.

Rules:
- Read CLAUDE.md completely before analysis; it is authoritative.
- Inspect the full base..head diff and relevant callers, tests, schemas,
  migrations, and CI config.
- Diagnose with concrete evidence from the patch, source, tests, and CI
  summary.
- This is report-only. Do not edit, commit, push, create a PR, merge, deploy,
  or contact production or real providers.
- Never read or print .env files, credentials, tokens, customer data, or
  production data.
- Separate product defects from harness/environment failures. Never call an
  unrun check passed.
- Report findings first, ordered Critical, High, Medium, Low, then verification
  and limitations.
- If no defect is found, say so plainly; do not invent work.
"""


def _write_report(path: Path, *, status: str, body: str) -> None:
    report = (
        "## Cursor debugger report\n\n"
        f"**Run status:** `{status}`\n\n"
        "This was a read-only diagnostic run; it was not authorised to push "
        "or merge.\n\n"
        f"{body.strip()}\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report, encoding="utf-8")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Cursor as a read-only PR debugger"
    )
    parser.add_argument("--repository", required=True)
    parser.add_argument("--pr-number", required=True, type=int)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--request-file", required=True, type=Path)
    parser.add_argument("--ci-context-file", required=True, type=Path)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    key = os.environ.get("CURSOR_API_KEY", "")
    try:
        if not key:
            raise DebuggerInputError("CURSOR_API_KEY is not configured")
        base_sha, head_sha = _validate_args(args)
        workspace = _validate_workspace(args.workspace)
        request = _read_limited(
            args.request_file, MAX_REQUEST_BYTES, "manager request"
        )
        ci_context = _read_limited(
            args.ci_context_file, MAX_CONTEXT_BYTES, "CI context"
        )

        # Malformed events must never start the SDK bridge.
        from cursor_sdk import Agent, AgentOptions, LocalAgentOptions

        with Agent.create(
            AgentOptions(
                model="composer-2.5",
                api_key=key,
                tools=["read", "grep", "glob", "ls"],
                local=LocalAgentOptions(cwd=workspace, setting_sources=[]),
            )
        ) as agent:
            run = agent.send(
                _prompt(
                    repository=args.repository,
                    pr_number=args.pr_number,
                    base_sha=base_sha,
                    head_sha=head_sha,
                    request=request,
                    ci_context=ci_context,
                )
            )
            result = run.wait()

        status = str(result.status)
        body = _redact(str(result.result or "Cursor returned no report."), key)
        _write_report(args.output, status=status, body=body)
        return 0 if status == "finished" else 1
    except Exception as exc:  # A failure must still become a visible PR comment.
        message = _redact(f"{type(exc).__name__}: {exc}", key)
        _write_report(
            args.output,
            status="error",
            body=f"Debugger could not complete: {message}",
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
