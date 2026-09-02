#!/usr/bin/env python
"""Refuse to build when the lockfiles and `pyproject.toml` disagree.

    python scripts/check_dependency_lock.py            # both lockfiles
    python scripts/check_dependency_lock.py --only runtime

**The problem this solves.** A lockfile is only a guarantee while it matches
what it was compiled from. Someone adds a dependency to `pyproject.toml`,
forgets to recompile, and the image builds happily from the stale lock — missing
the package they just added, or silently keeping one they removed. Nothing fails
until runtime, in the environment furthest from the person who made the change.

So each lockfile records a digest of the declarations it was compiled from, and
this script recomputes that digest and compares. The check runs in the Docker
build *before* dependencies are installed, so a stale lock is a build failure
rather than a deployment surprise.

**What the digest covers**, and why that exact set: the runtime dependency list,
the dev extra, and `requires-python`. Change any of them and the resolution can
change; change anything else in `pyproject.toml` — a ruff rule, a mypy override,
the description — and it cannot, so those must not force a recompile nobody
needs.

**Why two tools.** `uv` compiles the lock; `pip` installs from it. That is one
authority for resolution and one for installation, not two competing package
managers — uv never runs inside an image, and pip never resolves a version.
The reason for uv specifically is `--universal`: it resolves for every supported
platform at once, so a lock compiled on a Windows workstation still carries
`uvloop ; sys_platform != 'win32'` for the Linux container. A single-platform
resolver drops that line silently, and the image is quietly slower with nobody
the wiser.

Exit codes: 0 consistent, 1 usage, 2 a lockfile is stale or missing.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tomllib
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
PYPROJECT = BACKEND / "pyproject.toml"
REQUIREMENTS = BACKEND / "requirements"

#: The marker line each lockfile carries. `uv pip compile --no-header` is used
#: so that this is the only generated preamble, which keeps the diff of a
#: recompile to the packages that actually changed rather than a timestamp.
STAMP_PREFIX = "# declarations-sha256: "

LOCKFILES = {
    "runtime": REQUIREMENTS / "runtime.txt",
    "dev": REQUIREMENTS / "dev.txt",
}


def declaration_digest() -> str:
    """A digest of exactly the parts of `pyproject.toml` that affect resolution.

    Sorted and re-serialised rather than hashed as raw text: reordering a
    dependency list or reflowing a comment does not change what resolves, and a
    checker that demanded a recompile for either would be trained around within
    a week.
    """
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    project = data["project"]
    material = {
        "requires-python": project["requires-python"],
        "dependencies": sorted(project.get("dependencies", [])),
        "optional-dependencies": {
            name: sorted(values)
            for name, values in sorted(project.get("optional-dependencies", {}).items())
        },
    }
    canonical = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def recorded_digest(path: Path) -> str | None:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(STAMP_PREFIX):
            return line[len(STAMP_PREFIX) :].strip()
    return None


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="check_dependency_lock.py",
        description="Refuse when the lockfiles and pyproject.toml disagree.",
    )
    parser.add_argument(
        "--only",
        action="append",
        choices=sorted(LOCKFILES),
        help=(
            "Check just this lockfile. The runtime image copies only "
            "requirements/runtime.txt, so it verifies that one; a build has no "
            "opinion about whether the development lock is current, and "
            "demanding a file it does not need would be a check that fails for "
            "the wrong reason."
        ),
    )
    arguments = parser.parse_args(argv)
    selected = {name: LOCKFILES[name] for name in (arguments.only or sorted(LOCKFILES))}

    if not PYPROJECT.is_file():
        print(f"REFUSED: {PYPROJECT} not found.", file=sys.stderr)
        return 2

    expected = declaration_digest()
    problems: list[str] = []

    for name, path in selected.items():
        if not path.is_file():
            problems.append(f"{name}: {path.name} is missing")
            continue
        found = recorded_digest(path)
        if found is None:
            problems.append(f"{name}: {path.name} carries no declarations digest")
        elif found != expected:
            problems.append(
                f"{name}: {path.name} was compiled from different declarations "
                f"(records {found[:12]}, pyproject.toml is {expected[:12]})"
            )

    if problems:
        print("REFUSED: the dependency lock is out of date.\n", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        print(
            "\nRecompile both lockfiles and commit them:\n"
            "\n"
            "  python scripts/update_dependency_lock.py\n"
            "\n"
            "A lockfile is only a guarantee while it matches what it was compiled\n"
            "from. Installing from a stale one is the failure this check exists for.",
            file=sys.stderr,
        )
        return 2

    print(f"Dependency lock is consistent with pyproject.toml ({expected[:12]}).")
    for name, path in selected.items():
        pinned = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if "==" in line)
        print(f"  {name:8} {pinned} pinned requirements")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
