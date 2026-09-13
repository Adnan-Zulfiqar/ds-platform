from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from validate_event import PolicyError, validate_agent_pr


REPOSITORY = "Adnan-Zulfiqar/ds-platform"
WORKFLOW_NAME = "CI"
RUN_URL_PREFIX = f"https://github.com/{REPOSITORY}/actions/runs/"
CONCLUSIONS = {
    "action_required",
    "cancelled",
    "failure",
    "neutral",
    "skipped",
    "stale",
    "startup_failure",
    "success",
    "timed_out",
}


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PolicyError(f"{label} is missing")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PolicyError(f"{label} is missing")
    return value.strip()


def _positive_int(value: object, label: str) -> int:
    if not isinstance(value, int) or value <= 0:
        raise PolicyError(f"{label} is invalid")
    return value


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PolicyError(f"could not read trusted wake-up JSON: {exc}") from exc


def _flatten_prs(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise PolicyError("open PR response must be an array")
    entries = [entry for page in value for entry in page] if value and all(
        isinstance(page, list) for page in value
    ) else value
    if not all(isinstance(entry, dict) for entry in entries):
        raise PolicyError("open PR response contains an invalid entry")
    return entries


def validate_ci_wakeup(
    event: dict[str, Any], open_prs: Any
) -> dict[str, str]:
    repository = _mapping(event.get("repository"), "repository")
    if _text(repository.get("full_name"), "repository") != REPOSITORY:
        raise PolicyError("workflow run came from an unexpected repository")
    if _text(event.get("action"), "event action") != "completed":
        raise PolicyError("only completed workflow runs may wake the manager")

    run = _mapping(event.get("workflow_run"), "workflow run")
    if _text(run.get("name"), "workflow name") != WORKFLOW_NAME:
        raise PolicyError("only the application CI workflow may wake the manager")
    if _text(run.get("event"), "workflow event") != "pull_request":
        raise PolicyError("only PR-triggered CI may wake the manager")
    head_repository = _mapping(run.get("head_repository"), "head repository")
    if _text(head_repository.get("full_name"), "head repository") != REPOSITORY:
        raise PolicyError("fork workflow runs cannot wake the manager")

    head_sha = _text(run.get("head_sha"), "workflow head SHA").lower()
    head_branch = _text(run.get("head_branch"), "workflow head branch")
    conclusion = _text(run.get("conclusion"), "workflow conclusion")
    if conclusion not in CONCLUSIONS:
        raise PolicyError("workflow conclusion is not recognised")

    run_id = _positive_int(run.get("id"), "workflow run ID")
    run_attempt = _positive_int(run.get("run_attempt"), "workflow run attempt")
    run_url = _text(run.get("html_url"), "workflow run URL")
    if run_url != f"{RUN_URL_PREFIX}{run_id}":
        raise PolicyError("workflow run URL is outside the repository")

    matches: list[dict[str, Any]] = []
    for pr in _flatten_prs(open_prs):
        head = pr.get("head")
        if not isinstance(head, dict):
            continue
        if head.get("ref") == head_branch and str(head.get("sha", "")).lower() == head_sha:
            matches.append(pr)
    if not matches:
        return {"matched": "false"}
    if len(matches) != 1:
        raise PolicyError("workflow run maps to more than one open PR")

    candidate = matches[0]
    base = candidate.get("base")
    candidate_head = candidate.get("head")
    title = candidate.get("title")
    if (
        candidate.get("state") != "open"
        or candidate.get("draft") is not True
        or not isinstance(title, str)
        or not title.startswith("[Agent] ")
        or not isinstance(base, dict)
        or base.get("ref") != "develop"
        or not isinstance(candidate_head, dict)
        or not isinstance(candidate_head.get("repo"), dict)
        or candidate_head["repo"].get("full_name") != REPOSITORY
    ):
        return {"matched": "false"}

    outputs = validate_agent_pr(candidate)
    if outputs["checkout_ref"] != head_sha or outputs["head_ref"] != head_branch:
        raise PolicyError("workflow run and agent PR identity differ")
    return {
        **outputs,
        "matched": "true",
        "conclusion": conclusion,
        "run_attempt": str(run_attempt),
        "run_id": str(run_id),
        "run_url": run_url,
    }


def _write_outputs(path: Path, values: dict[str, str]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as output:
        for key, value in values.items():
            if "\n" in value or "\r" in value:
                raise PolicyError(f"unsafe multiline workflow output: {key}")
            output.write(f"{key}={value}\n")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate a CI manager wake-up")
    parser.add_argument("--event", required=True, type=Path)
    parser.add_argument("--open-prs", required=True, type=Path)
    parser.add_argument("--github-output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    try:
        event = _load(args.event)
        if not isinstance(event, dict):
            raise PolicyError("workflow event must be an object")
        outputs = validate_ci_wakeup(event, _load(args.open_prs))
        _write_outputs(args.github_output, outputs)
    except (OSError, PolicyError) as exc:
        print(f"Agent bridge refused CI wake-up: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
