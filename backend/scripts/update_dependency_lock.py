#!/usr/bin/env python
"""Recompile the dependency lockfiles. The only sanctioned way to change them.

    python scripts/update_dependency_lock.py
    python scripts/update_dependency_lock.py --upgrade-package urllib3

Run this after editing `dependencies`, `optional-dependencies` or
`requires-python` in `pyproject.toml`, and commit the result alongside that
edit. `scripts/check_dependency_lock.py` — which the Docker build runs — refuses
if the two ever drift apart.

**Hand-editing a lockfile is never correct.** The hashes make it impossible to
do quietly (pip refuses a hash that does not match), but the digest stamp makes
it impossible to do *at all* without the checker noticing, which is the point: a
lockfile edited by a human is a resolution nobody verified.

`--upgrade-package NAME` (repeatable) is passed to uv. Without it, uv keeps
every version already in the lock that still satisfies `pyproject.toml`, so a
security fix in a *transitive* package has no other sanctioned route in.

Requires `uv`, which is a development tool only — it never runs inside an image.
Install it however you like; `pip install uv` is enough.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_dependency_lock import (
    PYPROJECT,
    REQUIREMENTS,
    STAMP_PREFIX,
    declaration_digest,
)

#: The interpreter the lock is resolved for. Must match `requires-python` and
#: the base image in every Dockerfile; a lock resolved for one minor version is
#: not valid for another.
PYTHON_VERSION = "3.13"


def compile_lock(output: Path, *extra_arguments: str) -> None:
    uv = shutil.which("uv")
    if uv is None:
        sys.exit(
            "uv is not on PATH. It compiles the lock; pip installs from it.\n"
            "Install it with `pip install uv` and re-run."
        )

    command = [
        uv,
        "pip",
        "compile",
        # Resolve for every supported platform at once rather than for this
        # machine. Without it a lock compiled on Windows omits
        # `uvloop ; sys_platform != "win32"` and the Linux image is quietly
        # slower, with nothing to indicate why.
        "--universal",
        # Every requirement gets hashes for every distribution of its version,
        # so `pip install --require-hashes` can refuse a substituted artefact.
        "--generate-hashes",
        "--python-version",
        PYTHON_VERSION,
        # Suppress uv's own preamble so the digest stamp below is the only
        # generated line, and a recompile diffs as the packages that changed
        # rather than as a timestamp.
        "--no-header",
        *extra_arguments,
        str(PYPROJECT),
        "-o",
        str(output),
    ]
    print(f"compiling {output.name} ...")
    subprocess.run(command, check=True)  # noqa: S603 - fixed argv, resolved executable


def stamp(output: Path, digest: str) -> None:
    body = output.read_text(encoding="utf-8")
    header = (
        f"# Compiled by scripts/update_dependency_lock.py. Do not edit by hand.\n"
        f"# uv pip compile --universal --generate-hashes --python-version {PYTHON_VERSION}\n"
        f"{STAMP_PREFIX}{digest}\n"
        f"#\n"
        f"# The digest above covers the dependency declarations in pyproject.toml.\n"
        f"# scripts/check_dependency_lock.py recomputes it, and the Docker build\n"
        f"# refuses when the two disagree.\n"
        f"\n"
    )
    output.write_text(header + body, encoding="utf-8")


def _upgrade_arguments(argv: list[str]) -> list[str]:
    parser = argparse.ArgumentParser(description="Recompile the dependency lockfiles.")
    parser.add_argument("--upgrade-package", action="append", default=[], metavar="NAME")
    names: list[str] = parser.parse_args(argv).upgrade_package
    return [argument for name in names for argument in ("--upgrade-package", name)]


def main(argv: list[str] | None = None) -> int:
    upgrades = _upgrade_arguments(sys.argv[1:] if argv is None else argv)
    REQUIREMENTS.mkdir(exist_ok=True)
    digest = declaration_digest()

    compile_lock(REQUIREMENTS / "runtime.txt", *upgrades)
    compile_lock(REQUIREMENTS / "dev.txt", "--extra", "dev", *upgrades)

    for name in ("runtime.txt", "dev.txt"):
        stamp(REQUIREMENTS / name, digest)

    print(f"\nStamped both lockfiles with {digest[:12]}.")
    print("Commit them in the same change as the pyproject.toml edit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
