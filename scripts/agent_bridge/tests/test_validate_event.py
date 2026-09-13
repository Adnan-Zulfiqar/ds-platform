from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from validate_event import (  # noqa: E402
    PolicyError,
    validate_changed_paths,
    validate_comment_event,
)


OWNER = "Adnan-Zulfiqar"
REPOSITORY = "Adnan-Zulfiqar/ds-platform"
BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40


def _pr() -> dict[str, object]:
    return {
        "number": 42,
        "state": "open",
        "draft": True,
        "title": "[Agent] Fix the publish journey",
        "body": "\n".join(
            (
                "## Manager task",
                "Fix the journey.",
                "## Scope",
                "Frontend only.",
                "## Acceptance criteria",
                "Tests pass.",
                "## Forbidden actions",
                "No production.",
                "## Verification",
                "Run focused tests.",
            )
        ),
        "base": {"ref": "develop", "sha": BASE_SHA},
        "head": {
            "ref": "feature/agent-42-publish",
            "sha": HEAD_SHA,
            "repo": {"full_name": REPOSITORY},
        },
    }


def _event(
    *,
    comment: str = "/cursor-debug investigate the failed check",
    is_pr: bool = True,
) -> dict[str, object]:
    issue: dict[str, object] = {
        "state": "open",
        "title": "[Agent Task] Fix the publish journey",
        "body": "\n".join(
            (
                "## Goal",
                "Fix it.",
                "## Scope",
                "Frontend.",
                "## Acceptance criteria",
                "Tests pass.",
                "## Forbidden actions",
                "No production.",
                "## Verification",
                "Run tests.",
            )
        ),
    }
    if is_pr:
        issue["pull_request"] = {"url": "https://api.github.com/example"}
    return {
        "action": "created",
        "repository": {"full_name": REPOSITORY},
        "sender": {"login": OWNER},
        "comment": {"body": comment, "user": {"login": OWNER}},
        "issue": issue,
    }


class CommentPolicyTests(unittest.TestCase):
    def test_owner_can_send_cursor_to_same_repo_agent_pr(self) -> None:
        outputs, request = validate_comment_event(_event(), worker="cursor", pr=_pr())
        self.assertEqual(outputs["checkout_ref"], HEAD_SHA)
        self.assertEqual(outputs["base_sha"], BASE_SHA)
        self.assertEqual(request, "investigate the failed check")

    def test_owner_can_send_claude_to_same_repo_agent_pr(self) -> None:
        outputs, request = validate_comment_event(
            _event(comment="@claude implement only this task"),
            worker="claude",
            pr=_pr(),
        )
        self.assertEqual(outputs["checkout_ref"], HEAD_SHA)
        self.assertEqual(request, "implement only this task")

    def test_cursor_refuses_non_owner(self) -> None:
        event = _event()
        event["sender"] = {"login": "someone-else"}
        with self.assertRaisesRegex(PolicyError, "only the repository owner"):
            validate_comment_event(event, worker="cursor", pr=_pr())

    def test_cursor_refuses_fork(self) -> None:
        pr = _pr()
        head = copy.deepcopy(pr["head"])
        assert isinstance(head, dict)
        head["repo"] = {"full_name": "attacker/fork"}
        pr["head"] = head
        with self.assertRaisesRegex(PolicyError, "fork PRs"):
            validate_comment_event(_event(), worker="cursor", pr=pr)

    def test_cursor_refuses_protected_head(self) -> None:
        pr = _pr()
        head = copy.deepcopy(pr["head"])
        assert isinstance(head, dict)
        head["ref"] = "develop"
        pr["head"] = head
        with self.assertRaisesRegex(PolicyError, "approved prefix"):
            validate_comment_event(_event(), worker="cursor", pr=pr)

    def test_cursor_refuses_wrong_base(self) -> None:
        pr = _pr()
        pr["base"] = {"ref": "main", "sha": BASE_SHA}
        with self.assertRaisesRegex(PolicyError, "must target 'develop'"):
            validate_comment_event(_event(), worker="cursor", pr=pr)

    def test_cursor_refuses_non_agent_pr(self) -> None:
        pr = _pr()
        pr["title"] = "Ordinary change"
        with self.assertRaisesRegex(PolicyError, "must start"):
            validate_comment_event(_event(), worker="cursor", pr=pr)

    def test_cursor_refuses_ready_for_review_pr(self) -> None:
        pr = _pr()
        pr["draft"] = False
        with self.assertRaisesRegex(PolicyError, "only while the PR is draft"):
            validate_comment_event(_event(), worker="cursor", pr=pr)

    def test_cursor_refuses_issue_without_pr(self) -> None:
        with self.assertRaisesRegex(PolicyError, "only on an existing agent PR"):
            validate_comment_event(_event(is_pr=False), worker="cursor", pr=None)

    def test_claude_also_refuses_issue_without_pr(self) -> None:
        with self.assertRaisesRegex(PolicyError, "only on an existing agent PR"):
            validate_comment_event(
                _event(comment="@claude implement", is_pr=False),
                worker="claude",
                pr=None,
            )

    def test_claude_trigger_must_be_a_complete_word(self) -> None:
        with self.assertRaisesRegex(PolicyError, "does not contain"):
            validate_comment_event(
                _event(comment="please ask @claude-bot", is_pr=False),
                worker="claude",
                pr=None,
            )


class ChangedPathPolicyTests(unittest.TestCase):
    def test_accepts_product_source_and_env_examples(self) -> None:
        paths = validate_changed_paths(
            [
                {"filename": "frontend/app/page.tsx"},
                {"filename": ".env.example"},
                {"filename": ".env.production.example"},
            ]
        )
        self.assertEqual(len(paths), 3)

    def test_accepts_paginated_github_response(self) -> None:
        paths = validate_changed_paths(
            [
                [{"filename": "frontend/app/page.tsx"}],
                [{"filename": "CHANGELOG.md"}],
            ]
        )
        self.assertEqual(paths, ["frontend/app/page.tsx", "CHANGELOG.md"])

    def test_refuses_real_env_and_private_keys(self) -> None:
        for path in (".env", "backend/.env.local", "certs/provider.pem"):
            with self.subTest(path=path), self.assertRaisesRegex(
                PolicyError, "sensitive path"
            ):
                validate_changed_paths([{"filename": path}])

    def test_refuses_bridge_self_modification(self) -> None:
        for path in (
            "scripts/agent_bridge/validate_event.py",
            ".github/workflows/ci.yml",
            "CLAUDE.md",
            ".claude/settings.json",
            ".mcp.json",
        ):
            with self.subTest(path=path), self.assertRaisesRegex(
                PolicyError, "sensitive path"
            ):
                validate_changed_paths([{"filename": path}])

    def test_refuses_sensitive_previous_name_on_rename(self) -> None:
        with self.assertRaisesRegex(PolicyError, "sensitive path"):
            validate_changed_paths(
                [
                    {
                        "filename": "docs/renamed.md",
                        "previous_filename": ".github/workflows/ci.yml",
                    }
                ]
            )

    def test_refuses_build_and_evidence_artifacts(self) -> None:
        for path in (
            "frontend/.next/cache/file",
            "evidence/run.json",
            "backend/logs/app.log",
        ):
            with self.subTest(path=path), self.assertRaisesRegex(
                PolicyError, "sensitive path"
            ):
                validate_changed_paths([{"filename": path}])

    def test_refuses_oversized_agent_pr(self) -> None:
        files = [{"filename": f"frontend/file-{number}.ts"} for number in range(101)]
        with self.assertRaisesRegex(PolicyError, "more than 100 files"):
            validate_changed_paths(files)


if __name__ == "__main__":
    unittest.main()
