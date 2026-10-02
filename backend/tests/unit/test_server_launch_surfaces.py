"""Every way this application can be started must disable uvicorn's own proxy trust.

The application decides who a caller is in exactly one place —
:mod:`app.core.client_ip`, driven by ``SECURITY_TRUSTED_PROXIES``. That
decision is worthless if the ASGI server has already made a different one
underneath it, and uvicorn does exactly that by default: ``proxy_headers=True``
with ``forwarded_allow_ips`` defaulting to ``127.0.0.1``, so a request arriving
on loopback has its ``scope["client"]`` rewritten from ``X-Forwarded-For``
before a single line of application middleware runs.

That default was measured against this repository's real launch command: 700
requests rotating a forged ``X-Forwarded-For`` produced **zero** 429s and 650
separate rate-limit buckets, where 700 requests from one address produced 53.

So the flag is not a preference and not a deployment detail — it is the thing
that keeps the application's resolver authoritative. This module is the guard
against it being dropped from any one launch path while the others keep it,
which is the failure mode that would leave a production-capable command
vulnerable while every test still passed.

The assertions deliberately look only for a uvicorn invocation and the flag
that must accompany it. They do not pin surrounding text, so ports, hosts,
reload settings and comments can all change freely.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]

#: The flag that disables uvicorn's ProxyHeadersMiddleware entirely. Uvicorn
#: only installs the middleware when ``proxy_headers`` is true, so this removes
#: the rewrite rather than merely narrowing whom it trusts.
_REQUIRED_FLAG = "--no-proxy-headers"

#: Anything that would reintroduce a second proxy-trust authority. Narrowing
#: uvicorn's trust list with ``--forwarded-allow-ips`` is *not* a substitute:
#: it leaves uvicorn parsing the chain with its own rules, in parallel with the
#: application's, and the two can disagree.
_FORBIDDEN = (
    "--proxy-headers",
    "--forwarded-allow-ips",
    "FORWARDED_ALLOW_IPS",
    "proxy_headers=True",
)


@dataclass(frozen=True)
class LaunchSurface:
    """One place this repository can start the API server."""

    path: str
    environment: str
    #: Why this surface matters, shown when the assertion fails so whoever
    #: broke it learns what they broke rather than only that something is red.
    role: str
    #: Documentation, where the command appears alongside prose *about* the
    #: rejected alternatives. Both checks still run; the forbidden-token one is
    #: narrowed to the command lines, since a document has to be able to name
    #: ``--forwarded-allow-ips`` in order to reject it.
    prose: bool = False


#: Every launch surface found by auditing the repository. A new one must be
#: added here — see ``test_no_unaudited_uvicorn_invocation_exists``, which fails
#: when a uvicorn command appears in a file this list does not cover.
LAUNCH_SURFACES: tuple[LaunchSurface, ...] = (
    LaunchSurface(
        path="run-backend.ps1",
        environment="native host (the Cloudflare Tunnel production topology)",
        role="cloudflared connects to this process over loopback, which is "
        "precisely the peer uvicorn trusts by default",
    ),
    LaunchSurface(
        path="docker/backend.Dockerfile",
        environment="production container image",
        role="the CMD every deployed replica runs",
    ),
    LaunchSurface(
        path="docker-compose.yml",
        environment="compose stack",
        role="overrides the image CMD, so hardening the image alone is not enough",
    ),
    LaunchSurface(
        path=".github/workflows/ci.yml",
        environment="CI end-to-end job",
        role="the e2e suite must exercise the same server configuration that "
        "ships, or it verifies something nobody deploys",
    ),
    LaunchSurface(
        path="docs/DevelopmentSetup.md",
        environment="documented developer command",
        role="a developer who copies this command gets misleading results from "
        "any per-IP limit they try to test",
        prose=True,
    ),
    LaunchSurface(
        path="docs/operations/LOCAL_CANDIDATE_STACK.md",
        environment="documented local candidate stack (Compose override)",
        role="its override replaces the compose command to drop --reload, so it "
        "is a launch path of its own",
        prose=True,
    ),
    LaunchSurface(
        path="docs/PRODUCTION_SECURITY.md",
        environment="documented canonical production command",
        role="the command an operator copies when standing the service up, and "
        "the one this section exists to define",
        prose=True,
    ),
)

#: The one file that starts the server *without* the flag on purpose.
#:
#: ``test_proxy_boundary_real_server.py`` spawns real servers both hardened and
#: unhardened — the unhardened one is how the bypass is kept provably caused by
#: the missing flag rather than by coincidence. Asserting the flag is present on
#: every line there would forbid exactly the test that gives the rest of this
#: module its meaning. It builds its argv from ``_REQUIRED_FLAG`` below, so it
#: cannot drift away from what is enforced here.
_HARNESS_EXEMPTIONS = frozenset({"backend/tests/integration/test_proxy_boundary_real_server.py"})

#: The ASGI application path is the marker, not the word "uvicorn".
#:
#: ``run-backend.ps1`` invokes the server through a ``$Uvicorn`` variable, so a
#: pattern anchored on the literal "uvicorn" misses the one launch path that
#: fronts production. Every reference to ``app.main:app`` in this repository is
#: a command that starts the server, which makes it the honest marker — and a
#: new one appearing in prose is something this audit *should* look at.
_UVICORN_INVOCATION = re.compile(r"app\.main:app")


def _read(surface: LaunchSurface) -> str:
    return (_REPO_ROOT / surface.path).read_text(encoding="utf-8")


class TestEveryLaunchSurfaceIsHardened:
    @pytest.mark.parametrize("surface", LAUNCH_SURFACES, ids=lambda s: s.path)
    def test_the_surface_still_exists(self, surface: LaunchSurface) -> None:
        """A renamed or deleted surface must update this list, not slip past it."""
        assert (_REPO_ROOT / surface.path).is_file(), (
            f"{surface.path} no longer exists. If the launch path moved, update "
            "LAUNCH_SURFACES — do not delete the entry."
        )

    @pytest.mark.parametrize("surface", LAUNCH_SURFACES, ids=lambda s: s.path)
    def test_it_starts_uvicorn_with_proxy_headers_disabled(self, surface: LaunchSurface) -> None:
        source = _read(surface)
        invocations = [
            line
            for line in source.splitlines()
            if _UVICORN_INVOCATION.search(line) and not line.lstrip().startswith("#")
        ]
        assert invocations, (
            f"{surface.path} no longer starts uvicorn. If the launch path moved, "
            "update LAUNCH_SURFACES."
        )
        for line in invocations:
            assert _REQUIRED_FLAG in line, (
                f"{surface.path} starts uvicorn without {_REQUIRED_FLAG}:\n"
                f"    {line.strip()}\n"
                f"This is the {surface.environment} launch path — {surface.role}. "
                "Without the flag uvicorn rewrites scope['client'] from "
                "X-Forwarded-For for any loopback peer, and every per-IP limit "
                "can be bypassed by rotating one header."
            )

    @pytest.mark.parametrize("surface", LAUNCH_SURFACES, ids=lambda s: s.path)
    def test_it_does_not_reintroduce_uvicorn_side_proxy_trust(self, surface: LaunchSurface) -> None:
        """Two proxy authorities is the state this work exists to end.

        Documentation is scoped to its command lines rather than exempted.
        `PRODUCTION_SECURITY.md` has to *name* `--forwarded-allow-ips` in order
        to reject it, and a rule that forbade saying so would push the reasoning
        out of the document. What must never happen is the token appearing in a
        command someone copies — so for prose, only the lines that start the
        server are searched.
        """
        source = _read(surface)
        haystack = (
            "\n".join(line for line in source.splitlines() if _UVICORN_INVOCATION.search(line))
            if surface.prose
            else source
        )
        for token in _FORBIDDEN:
            if token == "--proxy-headers":
                # `--no-proxy-headers` contains `-proxy-headers`; match the
                # enabling form only.
                assert re.search(r"(?<!no)--proxy-headers", haystack) is None, (
                    f"{surface.path} enables uvicorn proxy-header parsing again."
                )
                continue
            assert token not in haystack, (
                f"{surface.path} contains {token!r}. Narrowing or re-enabling "
                "uvicorn's own proxy trust puts a second authority underneath "
                "app.core.client_ip; the application resolver must be the only one."
            )


class TestTheAuditIsComplete:
    """A new launch path must not be able to appear unnoticed."""

    #: Directories with no bearing on how the server is started.
    _IGNORED = (
        ".git",
        ".venv",
        "node_modules",
        ".next",
        ".mypy_cache",
        "__pycache__",
        ".ruff_cache",
        "htmlcov",
        "egg-info",
    )

    _CANDIDATE_SUFFIXES = (
        ".ps1",
        ".sh",
        ".yml",
        ".yaml",
        ".toml",
        ".md",
        ".py",
        ".cfg",
        ".service",
    )

    def _candidate_files(self) -> list[Path]:
        files: list[Path] = []
        for path in _REPO_ROOT.rglob("*"):
            if not path.is_file():
                continue
            text = str(path)
            if any(ignored in text for ignored in self._IGNORED):
                continue
            if path.suffix in self._CANDIDATE_SUFFIXES or path.name.startswith(
                ("Dockerfile", "Makefile", "Procfile")
            ):
                files.append(path)
        return files

    def test_no_unaudited_uvicorn_invocation_exists(self) -> None:
        """The guard on the guard.

        Hardening the known launch paths is worth nothing if another one is
        added later and nobody notices. This sweeps the repository for anything
        that starts ``app.main:app`` and requires it to be a surface this module
        knows about.
        """
        audited = {surface.path.replace("/", "\\") for surface in LAUNCH_SURFACES}
        audited |= {surface.path for surface in LAUNCH_SURFACES}
        audited |= _HARNESS_EXEMPTIONS
        audited |= {path.replace("/", "\\") for path in _HARNESS_EXEMPTIONS}
        audited.add(Path(__file__).relative_to(_REPO_ROOT).as_posix())
        audited.add(str(Path(__file__).relative_to(_REPO_ROOT)))

        unaudited: list[str] = []
        for path in self._candidate_files():
            relative = path.relative_to(_REPO_ROOT)
            if relative.as_posix() in audited or str(relative) in audited:
                continue
            try:
                source = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):  # pragma: no cover - binary/locked
                continue
            for line in source.splitlines():
                if _UVICORN_INVOCATION.search(line):
                    unaudited.append(f"{relative.as_posix()}: {line.strip()}")

        assert unaudited == [], (
            "These start the API server but are not in LAUNCH_SURFACES:\n  "
            + "\n  ".join(unaudited)
            + "\nAdd them to the manifest and give them --no-proxy-headers, or "
            "the hardening covers only the paths someone remembered."
        )

    @pytest.mark.parametrize("exempt", sorted(_HARNESS_EXEMPTIONS))
    def test_a_harness_exemption_is_still_earned(self, exempt: str) -> None:
        """An exemption nobody re-reads is how a hole opens.

        The exempted harness may start an unhardened server, but only because
        it takes the flag from this module — so the two cannot drift. If it
        stops importing it, or stops existing, the exemption is withdrawn.
        """
        path = _REPO_ROOT / exempt
        assert path.is_file(), (
            f"{exempt} is exempted from the launch-surface sweep but does not "
            "exist. Remove the exemption."
        )
        source = path.read_text(encoding="utf-8")
        assert "_REQUIRED_FLAG" in source, (
            f"{exempt} is exempted from the sweep on the grounds that it builds "
            "its command from _REQUIRED_FLAG. It no longer does, so it can now "
            "drift from what is enforced here."
        )

    def test_the_sweep_would_actually_catch_something(self, tmp_path: Path) -> None:
        """Proof the pattern is live rather than a regex that never matches."""
        assert _UVICORN_INVOCATION.search("uvicorn app.main:app --port 8000")
        assert _UVICORN_INVOCATION.search('CMD ["uvicorn", "app.main:app"]')
        # The PowerShell form, which an anchor on the literal "uvicorn" missed.
        assert _UVICORN_INVOCATION.search("& $Uvicorn app.main:app --reload")
        assert _UVICORN_INVOCATION.search("celery -A app.tasks worker") is None


class TestUvicornStillBehavesAsAssumed:
    """The premise, asserted rather than remembered.

    Every launch flag here is chosen because of what uvicorn does by default.
    If a future upgrade changes that default — in either direction — this
    module's reasoning needs revisiting, and a silent change is the worst case.
    """

    def test_uvicorn_still_parses_proxy_headers_by_default(self) -> None:
        import inspect

        from uvicorn.config import Config

        parameters = inspect.signature(Config.__init__).parameters
        assert parameters["proxy_headers"].default is True, (
            "uvicorn no longer parses proxy headers by default. The flags are "
            "still correct, but the justification in this module and in "
            "PRODUCTION_SECURITY.md should be re-read."
        )

    def test_disabling_proxy_headers_removes_the_middleware(self) -> None:
        """`--no-proxy-headers` must remove the rewrite, not just narrow it."""
        import inspect

        from uvicorn.config import Config

        source = inspect.getsource(Config.load)
        assert "if self.proxy_headers:" in source, (
            "uvicorn no longer gates ProxyHeadersMiddleware on `proxy_headers`. "
            "Re-verify that --no-proxy-headers still removes the rewrite."
        )
