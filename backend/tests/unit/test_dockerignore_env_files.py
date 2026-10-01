"""No environment file from any directory reaches a Docker build context.

The root `.dockerignore` once excluded only `.env` at the context root, so
`backend/.env` was copied into the backend image as `/app/.env` and
`frontend/.env.local` into the frontend image. These tests evaluate the real
file with Docker's matching rules (last matching pattern wins, `!` re-includes,
`**` spans directories) rather than asserting on its text.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DOCKERIGNORE = _REPO_ROOT / ".dockerignore"


def _pattern_to_regex(pattern: str) -> re.Pattern[str]:
    out = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    # A pattern that matches a directory also excludes everything inside it.
    return re.compile("".join(out) + "(?:/.*)?")


def _excluded(path: str) -> bool:
    excluded = False
    for raw in _DOCKERIGNORE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negate = line.startswith("!")
        pattern = line[1:] if negate else line
        if _pattern_to_regex(pattern.strip("/")).fullmatch(path):
            excluded = not negate
    return excluded


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.production",
        "backend/.env",
        "backend/.env.local",
        "frontend/.env",
        "frontend/.env.local",
        "frontend/.env.production.local",
    ],
)
def test_environment_files_are_kept_out_of_every_build_context(path: str) -> None:
    assert _excluded(path), f"{path} would be copied into a Docker image"


@pytest.mark.parametrize("path", [".env.example", "frontend/.env.example"])
def test_committed_examples_stay_available_to_the_build(path: str) -> None:
    assert not _excluded(path)


def test_application_source_is_still_sent() -> None:
    assert not _excluded("backend/app/main.py")
    assert not _excluded("frontend/package.json")
