from __future__ import annotations

import argparse
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from run_cursor_debugger import (  # noqa: E402
    DebuggerInputError,
    _prompt,
    _read_limited,
    _redact,
    _validate_args,
    _validate_workspace,
)


class CursorDebuggerTests(unittest.TestCase):
    def test_accepts_only_the_configured_repository_and_full_shas(self) -> None:
        args = argparse.Namespace(
            repository="Adnan-Zulfiqar/ds-platform",
            pr_number=7,
            base_sha="a" * 40,
            head_sha="b" * 40,
        )
        self.assertEqual(_validate_args(args), ("a" * 40, "b" * 40))

    def test_refuses_another_repository(self) -> None:
        args = argparse.Namespace(
            repository="attacker/fork",
            pr_number=7,
            base_sha="a" * 40,
            head_sha="b" * 40,
        )
        with self.assertRaisesRegex(DebuggerInputError, "unexpected repository"):
            _validate_args(args)

    def test_context_size_is_bounded_before_cursor_is_contacted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "context.txt"
            path.write_text("x" * 11, encoding="utf-8")
            with self.assertRaisesRegex(DebuggerInputError, "exceeds"):
                _read_limited(path, 10, "context")

    def test_workspace_must_be_a_git_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(DebuggerInputError, "checked-out PR"):
                _validate_workspace(Path(directory))

    def test_workspace_accepts_checkout_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            self.assertEqual(_validate_workspace(root), root.resolve())

    def test_report_redacts_cursor_key_and_database_password(self) -> None:
        key = "crsr_test_key"
        value = f"key={key} postgresql+psycopg://user:password@localhost/db"
        redacted = _redact(value, key)
        self.assertNotIn(key, redacted)
        self.assertNotIn("password", redacted)

    def test_prompt_is_explicitly_report_only(self) -> None:
        prompt = _prompt(
            repository="Adnan-Zulfiqar/ds-platform",
            pr_number=7,
            base_sha="a" * 40,
            head_sha="b" * 40,
            request="Find the failing test",
            ci_context="CI failed",
        )
        self.assertIn("read-only debugger", prompt)
        self.assertIn("Do not edit, commit, push", prompt)
        self.assertIn("Never read or print .env files", prompt)
        self.assertIn(".agent-bridge/pr.diff", prompt)


if __name__ == "__main__":
    unittest.main()
