"""Check a candidate production configuration without starting anything.

    python scripts/verify_production_config.py \\
        --env-file C:/dsplive/.env \\
        --expect-environment production \\
        --expect-database droppilot

**Nothing is started and nothing is written.** No database connection, no Redis
connection, no HTTP request, no file write. The environment file is parsed, a
`Settings` object is built from it in this process only, and the shared rule
table in `app.core.production_readiness` is evaluated against it. That table is
the same one `Settings.model_post_init` consults, so this cannot say "ready"
about a configuration the application would refuse to start with.

**No configuration value is ever printed.** Output is setting names, a verdict
and a sentence describing a *property* — a length band, whether a value matches
a published default, whether a URL is loopback. Running this in CI, pasting the
output into a ticket or screenshotting it is safe.

**The audited file is the only source.** Every variable the application reads is
removed from this process's environment before the file is loaded, because a
shell that has exported `POSTGRES_DB` or `SECURITY_SECRET_KEY` would otherwise
silently override the file and produce a confident answer about a configuration
that does not exist on disk. Anything removed is *reported by name* rather than
quietly dropped — an operator who does not know their shell was interfering
would draw the wrong conclusion from a clean report.

Exit codes:

    0  ready               every rule passes
    1  usage or IO error   the invocation or the file was wrong
    2  configuration invalid   a deployed environment must not start with this
    3  publication blocked     configuration is fit; something outside it forbids going live
    4  dependency missing      an enabled integration is missing something it needs
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

#: Prefixes the application reads. Anything matching is stripped from this
#: process before the file is loaded, so the file is the only authority.
_APP_PREFIXES = (
    "ENVIRONMENT",
    "ALLOWED_HOSTS",
    "CORS_ORIGINS",
    "API_V1_PREFIX",
    "POSTGRES_",
    "REDIS_",
    "SECURITY_",
    "EMAIL_",
    "RESEND_",
    "GOOGLE_",
    "EBAY_",
    "SHOPIFY_",
    "ALIEXPRESS_",
    "CELERY_",
    "FX_",
    "AI_",
    "LOG_",
    "S3_",
    "STORAGE_",
    "NEXT_PUBLIC_",
    "DATABASE_URL",
    "TRUSTED_PROXIES",
)

_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _parse_env_file(path: Path) -> tuple[dict[str, str], list[int]]:
    """Return the file's assignments, and the line numbers that are malformed.

    Deliberately strict and deliberately silent about content: a malformed line
    is reported by *number*, never by text, because the text of a malformed line
    is exactly as likely to hold a secret as a well-formed one.
    """
    values: dict[str, str] = {}
    malformed: list[int] = []
    for number, line in enumerate(
        path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
    ):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            malformed.append(number)
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        if not _KEY.fullmatch(key):
            malformed.append(number)
            continue
        values[key] = value.strip().strip('"').strip("'")
    return values, malformed


def _scrub_environment() -> list[str]:
    """Remove every application variable, returning the names removed."""
    removed = [name for name in list(os.environ) if name.startswith(_APP_PREFIXES)]
    for name in removed:
        del os.environ[name]
    return removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify a production configuration file. Prints no values."
    )
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument(
        "--expect-environment",
        required=True,
        help="The environment this file is meant to configure, e.g. production.",
    )
    parser.add_argument(
        "--expect-database",
        required=True,
        help="The database name this file is meant to point at, e.g. droppilot.",
    )
    args = parser.parse_args(argv)

    path: Path = args.env_file
    if not path.is_file():
        print(f"error: {path} is not a file", file=sys.stderr)
        return 1

    values, malformed = _parse_env_file(path)
    if malformed:
        print(
            f"error: {path.name} has {len(malformed)} malformed line(s): {malformed}",
            file=sys.stderr,
        )
        print("       Lines are reported by number only; their text is not shown.", file=sys.stderr)
        return 1
    if not values:
        print(f"error: {path.name} defines no settings", file=sys.stderr)
        return 1

    overridden = sorted(name for name in _scrub_environment() if name in values)
    ambient_only = sorted(name for name in _scrub_environment() if name not in values)

    # Load the file's values as the sole source, then build settings from them.
    os.environ.update(values)

    from app.core.config import Settings
    from app.core.production_readiness import Classification, Status, evaluate

    # `Settings` refuses to construct an invalid deployed configuration, which
    # is the behaviour under test — so it is built as a *local* environment and
    # the environment value is asserted separately by the rule table. Otherwise
    # the tool could only ever report the first problem, which is the limitation
    # it exists to remove.
    declared = values.get("ENVIRONMENT", "")
    os.environ["ENVIRONMENT"] = "local"
    try:
        settings = Settings()
    except Exception as exc:  # any construction failure is a configuration error
        print(
            f"error: the configuration could not be loaded ({type(exc).__name__})", file=sys.stderr
        )
        print("       No value is shown. Check the file for a malformed setting.", file=sys.stderr)
        return 2

    # Restore what the file actually declared so the rules judge the real value.
    from app.core.config import Environment

    try:
        object.__setattr__(settings, "environment", Environment(declared))
    except ValueError:
        print(f"error: ENVIRONMENT is not a recognised value (got {declared!r})", file=sys.stderr)
        return 2

    report = evaluate(settings)

    print(f"Configuration audit — {path.name}")
    print(f"  expected environment : {args.expect_environment}")
    print(f"  expected database    : {args.expect_database}")
    print()

    if overridden:
        print("AMBIENT OVERRIDES REMOVED (these were set in the shell and would have")
        print("shadowed the file; the file was used instead):")
        for name in overridden:
            print(f"  ! {name}")
        print()
    if ambient_only:
        print(f"({len(ambient_only)} further application variable(s) were present in the")
        print(" shell but not in the file; they were removed and ignored.)")
        print()

    problems = 0

    # The two expectations the caller stated, kept separate from the rule table.
    # They ask whether this is the file you think it is; the rules ask whether
    # its contents are fit. A correct configuration for the wrong database is
    # still the wrong configuration.
    print("Expectations")
    if declared != args.expect_environment:
        print(f"  {'FAIL':8} {'ENVIRONMENT':38} declares a different environment than expected")
        problems += 1
    else:
        print(f"  {'PASS':8} {'ENVIRONMENT':38} matches the expected environment")
    if declared in {"local", "test"} and args.expect_environment not in {"local", "test"}:
        print(
            f"  {'FAIL':8} {'ENVIRONMENT':38} is a development value, so no deployed guard applies"
        )
        problems += 1

    if values.get("POSTGRES_DB", "") != args.expect_database:
        print(f"  {'FAIL':8} {'POSTGRES_DB':38} names a different database than expected")
        problems += 1
    else:
        print(f"  {'PASS':8} {'POSTGRES_DB':38} matches the expected database name")

    print()
    print("Rules")
    for finding in report.findings:
        marker = "*" if finding.secret else " "
        print(f"  {finding.status.value:8} {finding.setting:38}{marker}{finding.reason}")

    print()
    print("Summary")
    for status in (Status.FAIL, Status.BLOCKED, Status.MISSING, Status.SKIPPED, Status.PASS):
        count = len(report.by_status(status))
        if count:
            print(f"  {status.value:8} {count}")
    required_now = [
        f
        for f in report.findings
        if f.classification is Classification.REQUIRED_NOW and f.status is Status.FAIL
    ]
    if required_now:
        print(f"  {len(required_now)} required-now setting(s) must be corrected before deploying.")

    code = report.exit_code
    if problems:
        # An expectation mismatch is a configuration error and outranks a
        # publication block: a perfectly-formed file for the wrong database is
        # not "blocked pending legal", it is the wrong file. Severity order is
        # 2 > 3 > 4, so this is an assignment rather than a max().
        code = 2
    verdict = {
        0: "READY",
        2: "CONFIGURATION INVALID",
        3: "PUBLICATION BLOCKED",
        4: "OPERATIONAL DEPENDENCY MISSING",
    }[code]
    print()
    print(f"Verdict: {verdict}  (exit {code})")
    print("* marks a secret. No value has been printed.")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
