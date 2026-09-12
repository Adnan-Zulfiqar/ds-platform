from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

from validate_event import PolicyError, validate_changed_paths


MAX_PATCH_BYTES = 2_000_000
MAX_FILE_BYTES = 1_000_000
ALLOWED_MODES = {"100644", "100755"}
SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"AKIA[0-9A-Z]{16}"),
    re.compile(rb"gh[pousr]_[A-Za-z0-9_]{30,}"),
    re.compile(rb"sk-ant-[A-Za-z0-9_-]{16,}"),
)


class PatchPolicyError(PolicyError):
    """Raised when a worker-produced patch crosses the bridge boundary."""


def _git(workspace: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(workspace), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _require_git_ok(result: subprocess.CompletedProcess[bytes], label: str) -> bytes:
    if result.returncode != 0:
        raise PatchPolicyError(f"git could not {label}")
    return result.stdout


def _split_paths(data: bytes) -> list[str]:
    try:
        return [part.decode("utf-8") for part in data.split(b"\0") if part]
    except UnicodeDecodeError as exc:
        raise PatchPolicyError("changed paths must be valid UTF-8") from exc


def _staged_paths(workspace: Path) -> list[str]:
    data = _require_git_ok(
        _git(workspace, "diff", "--cached", "--no-renames", "--name-only", "-z"),
        "list staged paths",
    )
    paths = _split_paths(data)
    try:
        return validate_changed_paths([{"filename": path} for path in paths])
    except PolicyError as exc:
        raise PatchPolicyError(str(exc)) from exc


def _non_deleted_paths(workspace: Path) -> list[str]:
    data = _require_git_ok(
        _git(
            workspace,
            "diff",
            "--cached",
            "--no-renames",
            "--diff-filter=ACMRTUXB",
            "--name-only",
            "-z",
        ),
        "list staged files",
    )
    return _split_paths(data)


def _validate_regular_text_files(workspace: Path, paths: list[str]) -> None:
    for path in paths:
        candidate = workspace / path
        if candidate.is_symlink() or not candidate.is_file():
            raise PatchPolicyError(f"worker patch must contain regular files only: {path}")
        if candidate.stat().st_size > MAX_FILE_BYTES:
            raise PatchPolicyError(f"worker file exceeds 1 MB: {path}")
        stage = _require_git_ok(
            _git(workspace, "ls-files", "--stage", "--", path),
            "inspect staged file mode",
        )
        mode = stage.split(maxsplit=1)[0].decode("ascii", errors="replace")
        if mode not in ALLOWED_MODES:
            raise PatchPolicyError(f"worker file mode is refused: {path}")

    numstat = _require_git_ok(
        _git(workspace, "diff", "--cached", "--no-renames", "--numstat"),
        "inspect staged file types",
    )
    for line in numstat.splitlines():
        columns = line.split(b"\t", 2)
        if len(columns) >= 2 and b"-" in columns[:2]:
            raise PatchPolicyError("binary files are outside the V1 worker boundary")


def _scan_added_lines(patch: bytes) -> None:
    added = b"\n".join(
        line
        for line in patch.splitlines()
        if line.startswith(b"+") and not line.startswith(b"+++")
    )
    if any(pattern.search(added) for pattern in SECRET_PATTERNS):
        raise PatchPolicyError("worker patch resembles a credential or private key")


def validate_staged_patch(
    workspace: Path,
    *,
    expected_head: str,
    patch_out: Path,
    forbidden_secret: str = "",
) -> tuple[list[str], int]:
    workspace = workspace.resolve()
    if not workspace.is_dir() or not (workspace / ".git").exists():
        raise PatchPolicyError("workspace must be a Git checkout")

    actual_head = _require_git_ok(_git(workspace, "rev-parse", "HEAD"), "read HEAD")
    if actual_head.decode("ascii", errors="replace").strip() != expected_head:
        raise PatchPolicyError("worker changed HEAD instead of producing a local patch")

    if _git(workspace, "diff", "--quiet").returncode != 0:
        raise PatchPolicyError("worker left unstaged changes after patch collection")

    paths = _staged_paths(workspace)
    _validate_regular_text_files(workspace, _non_deleted_paths(workspace))
    if _git(workspace, "diff", "--cached", "--check").returncode != 0:
        raise PatchPolicyError("worker patch fails git diff --check")

    patch = _require_git_ok(
        _git(
            workspace,
            "diff",
            "--cached",
            "--binary",
            "--full-index",
            "--no-ext-diff",
            "--no-renames",
            "--unified=3",
        ),
        "build the staged patch",
    )
    if not patch:
        raise PatchPolicyError("worker patch is empty")
    if len(patch) > MAX_PATCH_BYTES:
        raise PatchPolicyError("worker patch exceeds 2 MB; split the task")
    if forbidden_secret and forbidden_secret.encode("utf-8") in patch:
        raise PatchPolicyError("worker patch contains the worker credential")
    _scan_added_lines(patch)

    patch_out.parent.mkdir(parents=True, exist_ok=True)
    patch_out.write_bytes(patch)
    return paths, len(patch)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate a staged worker patch")
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--patch-out", required=True, type=Path)
    parser.add_argument("--forbidden-secret-env")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    secret = os.environ.get(args.forbidden_secret_env, "") if args.forbidden_secret_env else ""
    try:
        paths, size = validate_staged_patch(
            args.workspace,
            expected_head=args.expected_head,
            patch_out=args.patch_out,
            forbidden_secret=secret,
        )
    except (OSError, PatchPolicyError) as exc:
        print(f"Agent bridge refused the worker patch: {exc}", file=sys.stderr)
        return 2
    print(f"Validated worker patch: {len(paths)} text files, {size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
