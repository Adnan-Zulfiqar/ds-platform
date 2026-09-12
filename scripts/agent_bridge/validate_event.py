from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


REPOSITORY = "Adnan-Zulfiqar/ds-platform"
OWNER = "Adnan-Zulfiqar"
DEVELOP_BRANCH = "develop"
PROTECTED_BRANCHES = {"main", DEVELOP_BRANCH}
ALLOWED_HEAD_PREFIXES = ("feature/", "feat/", "fix/", "chore/", "docs/", "test/")
AGENT_PR_PREFIX = "[Agent] "
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
REF_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
CLAUDE_TRIGGER = re.compile(r"(?:^|\s)@claude(?:\s|$)", re.IGNORECASE)
CURSOR_TRIGGER = re.compile(r"^\s*/cursor-debug(?:\s|$)", re.IGNORECASE)

PR_SECTIONS = (
    "## Manager task",
    "## Scope",
    "## Acceptance criteria",
    "## Forbidden actions",
    "## Verification",
)
SENSITIVE_EXACT_PATHS = {
    ".claude.json",
    ".gitattributes",
    ".gitmodules",
    ".mcp.json",
    "agent_bridge.md",
    "agents.md",
    "claude.local.md",
    "claude.md",
    "codeowners",
}
SENSITIVE_PREFIXES = (
    ".claude/",
    ".github/",
    ".husky/",
    "scripts/agent_bridge/",
)


class PolicyError(ValueError):
    """Raised when an event falls outside the deliberately narrow trust boundary."""


def _load_json_value(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PolicyError(f"could not read trusted event JSON: {exc}") from exc


def _load_json(path: Path) -> dict[str, Any]:
    value = _load_json_value(path)
    if not isinstance(value, dict):
        raise PolicyError("event JSON must be an object")
    return value


def _require_mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PolicyError(f"{label} is missing")
    return value


def _require_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PolicyError(f"{label} is missing")
    return value.strip()


def _nested_text(data: dict[str, Any], keys: tuple[str, ...], label: str) -> str:
    current: object = data
    for key in keys:
        current = _require_mapping(current, label).get(key)
    return _require_text(current, label)


def _validate_sha(value: str, label: str) -> str:
    candidate = value.lower()
    if SHA_PATTERN.fullmatch(candidate) is None:
        raise PolicyError(f"{label} must be a full 40-character commit SHA")
    return candidate


def _validate_ref(value: str) -> str:
    if (
        REF_PATTERN.fullmatch(value) is None
        or ".." in value
        or "//" in value
        or value.endswith(("/", "."))
        or value in PROTECTED_BRANCHES
        or not value.startswith(ALLOWED_HEAD_PREFIXES)
    ):
        allowed = ", ".join(ALLOWED_HEAD_PREFIXES)
        raise PolicyError(f"PR head branch must use an approved prefix ({allowed})")
    return value


def _require_sections(body: str, sections: tuple[str, ...], label: str) -> None:
    missing = [section for section in sections if section not in body]
    if missing:
        raise PolicyError(f"{label} is missing required sections: {', '.join(missing)}")


def _is_sensitive_path(path: str) -> bool:
    lowered = path.lower()
    parts = lowered.split("/")
    name = parts[-1]
    if lowered in SENSITIVE_EXACT_PATHS or lowered.startswith(SENSITIVE_PREFIXES):
        return True
    if name == ".env" or (name.startswith(".env.") and not name.endswith(".example")):
        return True
    if name.endswith((".pem", ".key", ".p12", ".pfx")):
        return True
    return any(
        part in {"node_modules", "evidence", "logs", ".git"} or part.startswith(".next")
        for part in parts
    )


def _validate_path(path: str) -> str:
    if (
        path.startswith(("/", "\\"))
        or "\\" in path
        or "\x00" in path
        or any(part in {"", ".", ".."} for part in path.split("/"))
    ):
        raise PolicyError("PR contains an unsafe path")
    if _is_sensitive_path(path):
        raise PolicyError(f"worker access is refused for sensitive path: {path}")
    return path


def validate_changed_paths(value: Any) -> list[str]:
    pages = value if isinstance(value, list) else None
    if pages is None:
        raise PolicyError("PR file response must be a JSON array")
    if pages and all(isinstance(page, list) for page in pages):
        entries = [entry for page in pages for entry in page]
    else:
        entries = pages
    if not entries:
        raise PolicyError("agent PR must contain at least one changed file")
    paths: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise PolicyError("PR file response contains an invalid entry")
        candidates = [_require_text(entry.get("filename"), "changed filename")]
        previous = entry.get("previous_filename")
        if previous is not None:
            candidates.append(_require_text(previous, "previous changed filename"))
        for candidate in candidates:
            path = _validate_path(candidate)
            if path not in seen:
                seen.add(path)
                paths.append(path)
    if len(paths) > 100:
        raise PolicyError("agent PR changes more than 100 files; split the task")
    return paths


def validate_agent_pr(pr: dict[str, Any]) -> dict[str, str]:
    if _require_text(pr.get("state"), "PR state") != "open":
        raise PolicyError("worker commands are accepted only on open PRs")
    if pr.get("draft") is not True:
        raise PolicyError("worker commands are accepted only while the PR is draft")

    number = pr.get("number")
    if not isinstance(number, int) or number <= 0:
        raise PolicyError("PR number is invalid")

    title = _require_text(pr.get("title"), "PR title")
    if not title.startswith(AGENT_PR_PREFIX):
        raise PolicyError(f"PR title must start with {AGENT_PR_PREFIX!r}")
    _require_sections(
        _require_text(pr.get("body"), "PR body"), PR_SECTIONS, "PR body"
    )

    base = _require_mapping(pr.get("base"), "PR base")
    head = _require_mapping(pr.get("head"), "PR head")
    if _require_text(base.get("ref"), "PR base branch") != DEVELOP_BRANCH:
        raise PolicyError(f"agent PRs must target {DEVELOP_BRANCH!r}")
    if _nested_text(head, ("repo", "full_name"), "PR head repository") != REPOSITORY:
        raise PolicyError("fork PRs cannot invoke workers")

    head_ref = _validate_ref(_require_text(head.get("ref"), "PR head branch"))
    return {
        "base_sha": _validate_sha(
            _require_text(base.get("sha"), "PR base SHA"), "PR base SHA"
        ),
        "checkout_ref": _validate_sha(
            _require_text(head.get("sha"), "PR head SHA"), "PR head SHA"
        ),
        "head_ref": head_ref,
        "is_pr": "true",
        "pr_number": str(number),
    }


def _extract_request(comment: str, worker: str) -> str:
    trigger = CLAUDE_TRIGGER if worker == "claude" else CURSOR_TRIGGER
    match = trigger.search(comment)
    if match is None:
        raise PolicyError(f"comment does not contain the {worker} trigger")
    request = comment[match.end() :].strip()
    if len(request) > 4_000:
        raise PolicyError("worker request is longer than 4,000 characters")
    return request


def validate_comment_event(
    event: dict[str, Any], *, worker: str, pr: dict[str, Any] | None
) -> tuple[dict[str, str], str]:
    if worker not in {"claude", "cursor"}:
        raise PolicyError("unknown worker")
    if _nested_text(event, ("repository", "full_name"), "repository") != REPOSITORY:
        raise PolicyError("event came from an unexpected repository")
    if _nested_text(event, ("sender", "login"), "event actor") != OWNER:
        raise PolicyError("only the repository owner may invoke workers")
    if _require_text(event.get("action"), "event action") != "created":
        raise PolicyError("only newly created comments may invoke workers")

    comment = _require_mapping(event.get("comment"), "comment")
    if _nested_text(comment, ("user", "login"), "comment author") != OWNER:
        raise PolicyError("comment author does not match the repository owner")
    request = _extract_request(
        _require_text(comment.get("body"), "comment body"), worker
    )

    issue = _require_mapping(event.get("issue"), "issue")
    if not isinstance(issue.get("pull_request"), dict):
        raise PolicyError("workers run only on an existing agent PR")
    if pr is None:
        raise PolicyError("resolved PR metadata is required")
    return validate_agent_pr(pr), request


def _write_outputs(path: Path, values: dict[str, str]) -> None:
    try:
        with path.open("a", encoding="utf-8", newline="\n") as output:
            for key, value in values.items():
                if "\n" in value or "\r" in value:
                    raise PolicyError(f"unsafe multiline workflow output: {key}")
                output.write(f"{key}={value}\n")
    except OSError as exc:
        raise PolicyError(f"could not write workflow outputs: {exc}") from exc


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate an agent-bridge GitHub event"
    )
    parser.add_argument("--event", required=True, type=Path)
    parser.add_argument("--worker", required=True, choices=("claude", "cursor"))
    parser.add_argument("--pr-json", type=Path)
    parser.add_argument("--files-json", type=Path)
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--request-output", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    try:
        event = _load_json(args.event)
        pr = _load_json(args.pr_json) if args.pr_json else None
        if args.files_json is None:
            raise PolicyError("resolved PR file metadata is required")
        validate_changed_paths(_load_json_value(args.files_json))
        outputs, request = validate_comment_event(event, worker=args.worker, pr=pr)
        if args.github_output:
            _write_outputs(args.github_output, outputs)
        if args.request_output:
            args.request_output.write_text(request, encoding="utf-8")
    except (OSError, PolicyError) as exc:
        print(f"Agent bridge policy refused this event: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
