from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


class WorkflowContractTests(unittest.TestCase):
    def test_claude_prepare_job_has_no_repository_write_credential(self) -> None:
        workflow = (ROOT / ".github/workflows/claude-developer.yml").read_text(
            encoding="utf-8"
        )
        prepare, apply = workflow.split("\n  apply:\n", maxsplit=1)
        self.assertNotIn("contents: write", prepare)
        self.assertNotIn("AGENT_BRANCH_PUSH_TOKEN", prepare)
        self.assertNotIn("id-token: write", workflow)
        self.assertIn("contents: write", apply)
        self.assertIn("AGENT_BRANCH_PUSH_TOKEN", apply)

    def test_claude_uses_oauth_compatible_restricted_base_action(self) -> None:
        workflow = (ROOT / ".github/workflows/claude-developer.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "anthropics/claude-code-base-action@"
            "935fb86e3d3e9aa4bf665896e778208ffee8ae7a",
            workflow,
        )
        self.assertNotIn("anthropics/claude-code-action@", workflow)
        self.assertNotIn("--bare", workflow)
        self.assertIn("--safe-mode", workflow)
        self.assertIn("--restricted", workflow)
        self.assertIn("--permission-prompts none", workflow)
        self.assertIn("claude_code_oauth_token:", workflow)
        self.assertIn('--tools "Read,Glob,Grep,Edit,Write"', workflow)
        self.assertIn("--mcp-config '{\"mcpServers\":{}}'", workflow)
        self.assertNotIn('tools "default"', workflow)

    def test_push_is_exact_branch_and_force_with_lease(self) -> None:
        workflow = (ROOT / ".github/workflows/claude-developer.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            '--force-with-lease="refs/heads/${HEAD_REF}:${EXPECTED_HEAD}"',
            workflow,
        )
        self.assertIn('origin "HEAD:refs/heads/${HEAD_REF}"', workflow)

    def test_cursor_checkouts_do_not_persist_credentials(self) -> None:
        workflow = (ROOT / ".github/workflows/cursor-debugger.yml").read_text(
            encoding="utf-8"
        )
        self.assertEqual(workflow.count("persist-credentials: false"), 2)


if __name__ == "__main__":
    unittest.main()
