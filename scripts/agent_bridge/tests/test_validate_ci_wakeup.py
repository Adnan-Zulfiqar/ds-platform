from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from validate_ci_wakeup import PolicyError, validate_ci_wakeup  # noqa: E402


REPOSITORY = "Adnan-Zulfiqar/ds-platform"
BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40


def _pr() -> dict[str, object]:
    return {
        "number": 42,
        "state": "open",
        "draft": True,
        "title": "[Agent] Improve the product journey",
        "body": "\n".join(
            (
                "## Manager task",
                "Improve it.",
                "## Scope",
                "Frontend.",
                "## Acceptance criteria",
                "Tests.",
                "## Forbidden actions",
                "No production.",
                "## Verification",
                "Run CI.",
            )
        ),
        "base": {"ref": "develop", "sha": BASE_SHA},
        "head": {
            "ref": "feature/agent-42-product",
            "sha": HEAD_SHA,
            "repo": {"full_name": REPOSITORY},
        },
    }


def _event() -> dict[str, object]:
    return {
        "action": "completed",
        "repository": {"full_name": REPOSITORY},
        "workflow_run": {
            "id": 123,
            "run_attempt": 1,
            "name": "CI",
            "event": "pull_request",
            "head_sha": HEAD_SHA,
            "head_branch": "feature/agent-42-product",
            "head_repository": {"full_name": REPOSITORY},
            "conclusion": "success",
            "html_url": f"https://github.com/{REPOSITORY}/actions/runs/123",
        },
    }


class CiWakeupPolicyTests(unittest.TestCase):
    def test_accepts_exact_agent_pr_workflow_run(self) -> None:
        outputs = validate_ci_wakeup(_event(), [[_pr()]])
        self.assertEqual(outputs["pr_number"], "42")
        self.assertEqual(outputs["checkout_ref"], HEAD_SHA)
        self.assertEqual(outputs["conclusion"], "success")
        self.assertEqual(outputs["matched"], "true")

    def test_stale_workflow_head_is_a_clean_noop(self) -> None:
        pr = _pr()
        head = copy.deepcopy(pr["head"])
        assert isinstance(head, dict)
        head["sha"] = "c" * 40
        pr["head"] = head
        self.assertEqual(
            validate_ci_wakeup(_event(), [pr]), {"matched": "false"}
        )

    def test_non_draft_pr_is_a_clean_noop(self) -> None:
        pr = _pr()
        pr["draft"] = False
        self.assertEqual(
            validate_ci_wakeup(_event(), [pr]), {"matched": "false"}
        )

    def test_ordinary_pr_is_a_clean_noop(self) -> None:
        pr = _pr()
        pr["title"] = "Fix the ordinary application bug"
        self.assertEqual(
            validate_ci_wakeup(_event(), [pr]), {"matched": "false"}
        )

    def test_malformed_agent_contract_fails_loudly(self) -> None:
        pr = _pr()
        pr["body"] = "## Manager task\nMissing the other sections."
        with self.assertRaises(PolicyError):
            validate_ci_wakeup(_event(), [pr])

    def test_refuses_fork_workflow(self) -> None:
        event = _event()
        run = copy.deepcopy(event["workflow_run"])
        assert isinstance(run, dict)
        run["head_repository"] = {"full_name": "attacker/fork"}
        event["workflow_run"] = run
        with self.assertRaisesRegex(PolicyError, "fork workflow runs"):
            validate_ci_wakeup(event, [_pr()])

    def test_refuses_non_ci_workflow(self) -> None:
        event = _event()
        run = copy.deepcopy(event["workflow_run"])
        assert isinstance(run, dict)
        run["name"] = "Deploy production"
        event["workflow_run"] = run
        with self.assertRaisesRegex(PolicyError, "application CI"):
            validate_ci_wakeup(event, [_pr()])


if __name__ == "__main__":
    unittest.main()
