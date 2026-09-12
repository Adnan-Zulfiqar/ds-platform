from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from validate_patch import PatchPolicyError, validate_staged_patch  # noqa: E402


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()


class StagedPatchPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        _git(self.root, "init", "-q")
        _git(self.root, "config", "user.name", "Bridge test")
        _git(self.root, "config", "user.email", "bridge@example.invalid")
        (self.root / "README.md").write_text("base\n", encoding="utf-8")
        _git(self.root, "add", "README.md")
        _git(self.root, "commit", "-qm", "base")
        self.head = _git(self.root, "rev-parse", "HEAD")
        self.output = self.root / "out.patch"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _stage(self, path: str, content: str | bytes) -> None:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
        _git(self.root, "add", path)

    def test_accepts_a_small_regular_text_patch(self) -> None:
        self._stage("frontend/page.tsx", "export default function Page() {}\n")
        paths, size = validate_staged_patch(
            self.root,
            expected_head=self.head,
            patch_out=self.output,
        )
        self.assertEqual(paths, ["frontend/page.tsx"])
        self.assertGreater(size, 0)
        self.assertTrue(self.output.read_bytes().startswith(b"diff --git"))

    def test_validated_patch_round_trips_byte_for_byte(self) -> None:
        self._stage("frontend/page.tsx", "export const answer = 42;\n")
        validate_staged_patch(
            self.root,
            expected_head=self.head,
            patch_out=self.output,
        )
        expected = self.output.read_bytes()
        _git(self.root, "reset", "--hard", "HEAD")
        _git(self.root, "apply", "--index", str(self.output))
        rebuilt = self.root / "rebuilt.patch"
        validate_staged_patch(
            self.root,
            expected_head=self.head,
            patch_out=rebuilt,
        )
        self.assertEqual(rebuilt.read_bytes(), expected)

    def test_refuses_sensitive_workflow_path(self) -> None:
        self._stage(".github/workflows/ci.yml", "name: unsafe\n")
        with self.assertRaisesRegex(PatchPolicyError, "sensitive path"):
            validate_staged_patch(
                self.root,
                expected_head=self.head,
                patch_out=self.output,
            )

    def test_refuses_binary_file(self) -> None:
        self._stage("frontend/image.png", b"\x00\x01\x02")
        with self.assertRaisesRegex(PatchPolicyError, "binary files"):
            validate_staged_patch(
                self.root,
                expected_head=self.head,
                patch_out=self.output,
            )

    def test_refuses_exact_worker_secret(self) -> None:
        secret = "sk-ant-test-worker-secret-1234567890"
        self._stage("frontend/config.ts", f'export const value = "{secret}";\n')
        with self.assertRaisesRegex(PatchPolicyError, "worker credential"):
            validate_staged_patch(
                self.root,
                expected_head=self.head,
                patch_out=self.output,
                forbidden_secret=secret,
            )

    def test_refuses_a_changed_head(self) -> None:
        self._stage("frontend/page.tsx", "changed\n")
        with self.assertRaisesRegex(PatchPolicyError, "changed HEAD"):
            validate_staged_patch(
                self.root,
                expected_head="f" * 40,
                patch_out=self.output,
            )


if __name__ == "__main__":
    unittest.main()
