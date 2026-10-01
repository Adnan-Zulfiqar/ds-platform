"""CI must install the backend from the hash lock, as the images do.

On 2026-10-01 every PR's backend job failed mypy 70 times on unchanged
code: CI ran ``pip install -e ".[dev]"``, which resolved pyproject.toml's
open ranges to SQLAlchemy 2.1.1 while the lock (and every image) pins
2.0.52. CI was testing a dependency set nothing ships with.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

CI = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "ci.yml"


def _pip_lines() -> list[str]:
    return [
        line.strip()
        for line in CI.read_text(encoding="utf-8").splitlines()
        if re.match(r"\s*pip install ", line)
    ]


def test_no_job_resolves_backend_dependencies_from_pyproject() -> None:
    for line in _pip_lines():
        if "-e" in line.split():
            assert "--no-deps" in line, f"editable install resolves dependencies: {line}"


def test_every_dependency_install_uses_a_hashed_lockfile() -> None:
    requirement_installs = [line for line in _pip_lines() if " -r " in line]
    assert requirement_installs, "no lockfile install found in CI"
    for line in requirement_installs:
        assert "--require-hashes" in line, line
        assert re.search(r"requirements/(dev|runtime)\.txt", line), line


def test_each_lock_install_is_preceded_by_the_lock_consistency_check() -> None:
    text = CI.read_text(encoding="utf-8")
    assert text.count("pip install --require-hashes") == text.count(
        "scripts/check_dependency_lock.py"
    )
