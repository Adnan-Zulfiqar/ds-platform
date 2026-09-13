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

    def test_claude_patch_artifact_is_stable_across_run_attempts(self) -> None:
        workflow = (ROOT / ".github/workflows/claude-developer.yml").read_text(
            encoding="utf-8"
        )
        self.assertEqual(workflow.count("name: claude-patch-${{ github.run_id }}\n"), 2)
        self.assertNotIn(
            "claude-patch-${{ github.run_id }}-${{ github.run_attempt }}", workflow
        )
        self.assertIn("overwrite: true", workflow)

    def test_cursor_checkouts_do_not_persist_credentials(self) -> None:
        workflow = (ROOT / ".github/workflows/cursor-debugger.yml").read_text(
            encoding="utf-8"
        )
        self.assertEqual(workflow.count("persist-credentials: false"), 2)

    def test_worker_reports_are_preserved_before_best_effort_comments(self) -> None:
        for name in ("claude-developer.yml", "cursor-debugger.yml"):
            with self.subTest(workflow=name):
                workflow = (ROOT / ".github/workflows" / name).read_text(
                    encoding="utf-8"
                )
                self.assertIn("GITHUB_STEP_SUMMARY", workflow)
                self.assertIn("actions/upload-artifact@v4", workflow)
                self.assertIn("continue-on-error: true", workflow)

    def test_cursor_transport_cannot_hide_worker_or_readonly_failure(self) -> None:
        workflow = (ROOT / ".github/workflows/cursor-debugger.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("id: cursor", workflow)
        self.assertIn("id: readonly", workflow)
        self.assertIn(
            "steps.cursor.outcome == 'failure' || steps.readonly.outcome == 'failure'",
            workflow,
        )

    def test_reporters_use_rest_comments_with_existing_issue_permission(self) -> None:
        for name, report_count in (
            ("claude-developer.yml", 2),
            ("cursor-debugger.yml", 1),
            ("agent-manager-wakeup.yml", 1),
        ):
            with self.subTest(workflow=name):
                workflow = (ROOT / ".github/workflows" / name).read_text(
                    encoding="utf-8"
                )
                self.assertIn("issues: write", workflow)
                self.assertNotIn("pull-requests: write", workflow)
                self.assertNotIn('gh pr comment "', workflow)
                self.assertEqual(
                    workflow.count("gh api --method POST --silent"), report_count
                )
                self.assertEqual(
                    workflow.count(
                        '"repos/${GITHUB_REPOSITORY}/issues/${PR_NUMBER}/comments"'
                    ),
                    report_count,
                )
                self.assertEqual(workflow.count("--field body=@"), report_count)
                self.assertGreaterEqual(workflow.count("continue-on-error: true"), 1)

    def test_manager_wakeup_preserves_record_when_comment_transport_fails(self) -> None:
        workflow = (ROOT / ".github/workflows/agent-manager-wakeup.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("GITHUB_STEP_SUMMARY", workflow)
        self.assertIn("actions/upload-artifact@v4", workflow)
        self.assertIn("continue-on-error: true", workflow)


if __name__ == "__main__":
    unittest.main()
