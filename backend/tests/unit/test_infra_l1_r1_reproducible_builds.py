"""INFRA-L1-R1 — reproducible dependencies, and a rehearsal that is honestly absent.

Two things are asserted here, and they are opposites.

The first is that dependency resolution is *fixed*: a lockfile with exact
versions and hashes, a build that installs frozen and refuses when the lock and
`pyproject.toml` disagree, and a runtime image that receives no test or lint
tooling. Without that, "the image we tested" and "the image we deployed" are two
different artefacts that happen to share a tag.

The second is that the Linux rehearsal is *not claimed*. The evidence template
starts in a `NOT EXECUTED` state, nothing reads it, and the documentation does
not say the stack was proven on Linux — because it was not. A file a human can
type is not evidence a machine should trust, and the tests below make sure
nobody wires it up as though it were.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "backend"
PYPROJECT = BACKEND / "pyproject.toml"
REQUIREMENTS = BACKEND / "requirements"
RUNTIME_LOCK = REQUIREMENTS / "runtime.txt"
DEV_LOCK = REQUIREMENTS / "dev.txt"
CHECKER = BACKEND / "scripts" / "check_dependency_lock.py"
UPDATER = BACKEND / "scripts" / "update_dependency_lock.py"

PYTHON_DOCKERFILES = {
    "backend": REPO / "docker" / "backend.Dockerfile",
    "worker": REPO / "docker" / "worker.Dockerfile",
    "ops": REPO / "docker" / "ops.Dockerfile",
}
REHEARSAL = REPO / "scripts" / "deploy" / "rehearse_linux.sh"
EVIDENCE = REPO / "docs" / "operations" / "rehearsal-evidence.template.md"
RUNBOOK = REPO / "docs" / "operations" / "LIGHTSAIL_DEPLOYMENT.md"

#: Tools that belong in a developer's environment and nowhere near a running
#: production container. Each is an extra attack surface and, for the linters,
#: a package that can execute arbitrary configuration.
DEVELOPMENT_ONLY = (
    "pytest",
    "ruff",
    "mypy",
    "fakeredis",
    "pytest-cov",
    "asgi-lifespan",
    "coverage",
)


def lock_packages(path: Path) -> set[str]:
    """The package names a lockfile pins, lowercased."""
    return {
        match.group(1).lower()
        for match in re.finditer(r"^([A-Za-z0-9._-]+)==", path.read_text(encoding="utf-8"), re.M)
    }


def prose(path: Path) -> str:
    """A document with its line wrapping removed.

    Assertions about wording must survive a reflow: a sentence is the same
    sentence whether or not a newline fell in the middle of it, and a test that
    broke on reformatting would be a test people stop reflowing around.
    """
    return " ".join(path.read_text(encoding="utf-8").split())


def directives(path: Path) -> str:
    """A script with its comment lines removed.

    Every check below asks "does this script *do* X", and a comment explaining
    why it deliberately does not do X names X.
    """
    return chr(10).join(
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


def run_checker(*arguments: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, this interpreter
        [sys.executable, str(CHECKER), *arguments],
        cwd=str(cwd or BACKEND),
        capture_output=True,
        text=True,
        check=False,
    )


# ---------------------------------------------------------------------------
# The lock exists and means something
# ---------------------------------------------------------------------------


class TestTheLockIsAnActualLock:
    def test_both_lockfiles_exist(self) -> None:
        assert RUNTIME_LOCK.is_file()
        assert DEV_LOCK.is_file()

    @pytest.mark.parametrize("path", [RUNTIME_LOCK, DEV_LOCK], ids=["runtime", "dev"])
    def test_every_requirement_is_pinned_to_an_exact_version(self, path: Path) -> None:
        """A range is a resolution deferred to build time, which is the problem."""
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            if not line or line.startswith((" ", "#", "-")):
                continue
            requirement = line.split(";")[0].split("\\")[0].strip()
            if not requirement:
                continue
            assert "==" in requirement, f"{path.name} does not pin: {requirement}"
            for operator in (">=", "<=", "~=", ">", "<"):
                assert operator not in requirement.split("==", 1)[0], requirement

    @pytest.mark.parametrize("path", [RUNTIME_LOCK, DEV_LOCK], ids=["runtime", "dev"])
    def test_every_requirement_carries_integrity_hashes(self, path: Path) -> None:
        """Without hashes a pinned version can still be a substituted artefact."""
        text = path.read_text(encoding="utf-8")
        blocks = [block for block in text.split("\n\n") if "==" in block]
        assert blocks, f"{path.name} has no requirement blocks"
        for block in blocks:
            for line in block.splitlines():
                if re.match(r"^[A-Za-z0-9._-]+==", line):
                    package = line.split("==")[0]
                    assert "--hash=sha256:" in block, f"{package} in {path.name} has no hash"

    def test_the_lockfile_records_the_declarations_it_was_compiled_from(self) -> None:
        for path in (RUNTIME_LOCK, DEV_LOCK):
            assert "# declarations-sha256: " in path.read_text(encoding="utf-8")

    def test_the_python_version_is_pinned_to_one_minor_release(self) -> None:
        """A resolution valid for 3.13 is not automatically valid for 3.14."""
        text = PYPROJECT.read_text(encoding="utf-8")
        match = re.search(r'requires-python = "([^"]+)"', text)
        assert match, "requires-python is not declared"
        assert "<3.14" in match.group(1), f"open upper bound: {match.group(1)}"

    def test_the_lock_targets_the_same_interpreter_as_the_images(self) -> None:
        for name, path in PYTHON_DOCKERFILES.items():
            assert "python:3.13-slim" in path.read_text(encoding="utf-8"), name
        assert "--python-version 3.13" in RUNTIME_LOCK.read_text(encoding="utf-8")


class TestRuntimeAndDevelopmentAreSeparate:
    @pytest.mark.parametrize("tool", DEVELOPMENT_ONLY)
    def test_no_development_tool_is_in_the_runtime_lock(self, tool: str) -> None:
        assert tool not in lock_packages(RUNTIME_LOCK), f"{tool} would ship in the image"

    @pytest.mark.parametrize("tool", DEVELOPMENT_ONLY)
    def test_every_development_tool_is_in_the_development_lock(self, tool: str) -> None:
        assert tool in lock_packages(DEV_LOCK), f"{tool} is missing from the dev lock"

    def test_the_development_lock_is_a_superset_of_the_runtime_lock(self) -> None:
        """The dev environment must be able to run what the image runs."""
        missing = lock_packages(RUNTIME_LOCK) - lock_packages(DEV_LOCK)
        assert missing == set(), f"runtime packages absent from the dev lock: {missing}"

    def test_the_runtime_lock_is_meaningfully_smaller(self) -> None:
        assert len(lock_packages(RUNTIME_LOCK)) < len(lock_packages(DEV_LOCK))


class TestTheLockIsUsableOnBothPlatforms:
    def test_it_was_resolved_universally_rather_than_for_one_platform(self) -> None:
        assert "--universal" in RUNTIME_LOCK.read_text(encoding="utf-8")

    def test_a_linux_only_dependency_survives_a_windows_resolution(self) -> None:
        """The concrete reason `--universal` is not optional.

        `uvicorn[standard]` depends on `uvloop ; sys_platform != "win32"`. A
        single-platform resolver run on a Windows workstation evaluates that
        marker as false and drops the line, so the Linux image is quietly built
        without uvloop and nobody finds out. A universal resolution keeps the
        requirement *and* its marker, and pip applies the marker at install
        time on whichever platform is installing.
        """
        text = RUNTIME_LOCK.read_text(encoding="utf-8")
        match = re.search(r"^uvloop==\S+\s*;\s*(.+)$", text, re.M)
        assert match, "uvloop is absent — the lock was not resolved universally"
        assert "sys_platform != 'win32'" in match.group(1)

    def test_platform_conditional_requirements_keep_their_markers(self) -> None:
        text = RUNTIME_LOCK.read_text(encoding="utf-8")
        assert re.search(r"^\S+==\S+ ;", text, re.M), "no environment markers survived"


# ---------------------------------------------------------------------------
# The consistency check
# ---------------------------------------------------------------------------


class TestTheBuildRefusesAStaleLock:
    def test_the_checker_passes_on_the_committed_state(self) -> None:
        result = run_checker()
        assert result.returncode == 0, result.stderr

    def test_it_refuses_when_a_dependency_is_added_without_recompiling(
        self, tmp_path: Path
    ) -> None:
        """The exact failure this exists for: an edit, and a forgotten recompile."""
        staged = tmp_path / "backend"
        staged.mkdir()
        (staged / "requirements").mkdir()
        (staged / "scripts").mkdir()
        (staged / "scripts" / "check_dependency_lock.py").write_text(
            CHECKER.read_text(encoding="utf-8"), encoding="utf-8"
        )
        for lock in (RUNTIME_LOCK, DEV_LOCK):
            (staged / "requirements" / lock.name).write_text(
                lock.read_text(encoding="utf-8"), encoding="utf-8"
            )
        original = PYPROJECT.read_text(encoding="utf-8")
        (staged / "pyproject.toml").write_text(
            original.replace('"redis>=5.2.0",', '"redis>=5.2.0",\n    "requests>=2.32.0",'),
            encoding="utf-8",
        )

        result = subprocess.run(  # noqa: S603 - fixed argv, this interpreter
            [sys.executable, str(staged / "scripts" / "check_dependency_lock.py")],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 2
        assert "out of date" in result.stderr
        assert "update_dependency_lock" in result.stderr

    def test_it_refuses_a_missing_lockfile(self, tmp_path: Path) -> None:
        staged = tmp_path / "backend"
        (staged / "requirements").mkdir(parents=True)
        (staged / "scripts").mkdir()
        (staged / "scripts" / "check_dependency_lock.py").write_text(
            CHECKER.read_text(encoding="utf-8"), encoding="utf-8"
        )
        (staged / "pyproject.toml").write_text(
            PYPROJECT.read_text(encoding="utf-8"), encoding="utf-8"
        )
        result = subprocess.run(  # noqa: S603 - fixed argv, this interpreter
            [sys.executable, str(staged / "scripts" / "check_dependency_lock.py")],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 2
        assert "is missing" in result.stderr

    def test_it_refuses_a_lockfile_with_no_digest_stamp(self, tmp_path: Path) -> None:
        """A hand-written requirements file is a resolution nobody verified."""
        staged = tmp_path / "backend"
        (staged / "requirements").mkdir(parents=True)
        (staged / "scripts").mkdir()
        (staged / "scripts" / "check_dependency_lock.py").write_text(
            CHECKER.read_text(encoding="utf-8"), encoding="utf-8"
        )
        (staged / "pyproject.toml").write_text(
            PYPROJECT.read_text(encoding="utf-8"), encoding="utf-8"
        )
        for lock in (RUNTIME_LOCK, DEV_LOCK):
            body = "\n".join(
                line
                for line in lock.read_text(encoding="utf-8").splitlines()
                if not line.startswith("# declarations-sha256: ")
            )
            (staged / "requirements" / lock.name).write_text(body, encoding="utf-8")
        result = subprocess.run(  # noqa: S603 - fixed argv, this interpreter
            [sys.executable, str(staged / "scripts" / "check_dependency_lock.py")],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 2
        assert "no declarations digest" in result.stderr

    def test_cosmetic_pyproject_edits_do_not_force_a_recompile(self, tmp_path: Path) -> None:
        """A checker that cried wolf over a comment would be worked around."""
        staged = tmp_path / "backend"
        (staged / "requirements").mkdir(parents=True)
        (staged / "scripts").mkdir()
        (staged / "scripts" / "check_dependency_lock.py").write_text(
            CHECKER.read_text(encoding="utf-8"), encoding="utf-8"
        )
        for lock in (RUNTIME_LOCK, DEV_LOCK):
            (staged / "requirements" / lock.name).write_text(
                lock.read_text(encoding="utf-8"), encoding="utf-8"
            )
        (staged / "pyproject.toml").write_text(
            PYPROJECT.read_text(encoding="utf-8") + "\n# a trailing comment\n", encoding="utf-8"
        )
        result = subprocess.run(  # noqa: S603 - fixed argv, this interpreter
            [sys.executable, str(staged / "scripts" / "check_dependency_lock.py")],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr

    def test_the_updater_is_the_documented_way_to_change_a_lock(self) -> None:
        assert UPDATER.is_file()
        text = UPDATER.read_text(encoding="utf-8")
        assert "--universal" in text and "--generate-hashes" in text


# ---------------------------------------------------------------------------
# The images consume it
# ---------------------------------------------------------------------------


class TestDockerfilesInstallFrozen:
    @pytest.mark.parametrize("name", sorted(PYTHON_DOCKERFILES))
    def test_the_install_requires_hashes(self, name: str) -> None:
        text = PYTHON_DOCKERFILES[name].read_text(encoding="utf-8")
        assert "--require-hashes" in text, f"{name} does not install frozen"

    @pytest.mark.parametrize("name", sorted(PYTHON_DOCKERFILES))
    def test_the_install_does_not_re_resolve_dependencies(self, name: str) -> None:
        """The lock holds the full transitive closure; resolving again is how a
        version outside it gets in."""
        text = PYTHON_DOCKERFILES[name].read_text(encoding="utf-8")
        assert "--no-deps" in text, name

    @pytest.mark.parametrize("name", sorted(PYTHON_DOCKERFILES))
    def test_the_lock_is_checked_before_anything_is_installed(self, name: str) -> None:
        text = PYTHON_DOCKERFILES[name].read_text(encoding="utf-8")
        check = text.index("check_dependency_lock.py")
        install = text.index("--require-hashes")
        assert check < install, f"{name} installs before checking the lock"

    @pytest.mark.parametrize("name", sorted(PYTHON_DOCKERFILES))
    def test_no_image_derives_requirements_from_pyproject_at_build_time(self, name: str) -> None:
        """The old approach extracted the dependency list with tomllib, which is
        a fresh resolution wearing a lockfile's clothes: the constraints are
        lower bounds, so two builds a month apart install different versions."""
        text = PYTHON_DOCKERFILES[name].read_text(encoding="utf-8")
        assert "tomllib" not in text, f"{name} still resolves at build time"

    @pytest.mark.parametrize("name", sorted(PYTHON_DOCKERFILES))
    def test_the_development_lock_is_never_copied_into_an_image(self, name: str) -> None:
        text = PYTHON_DOCKERFILES[name].read_text(encoding="utf-8")
        assert "requirements/dev.txt" not in text, f"{name} copies the dev lock"

    @pytest.mark.parametrize("name", sorted(PYTHON_DOCKERFILES))
    def test_the_dependency_layer_precedes_the_application_source(self, name: str) -> None:
        """Otherwise editing one service reinstalls seventy packages, and the
        layer's identity stops being the lock's identity."""
        text = PYTHON_DOCKERFILES[name].read_text(encoding="utf-8")
        install = text.index("--require-hashes")
        source = text.index("COPY --chown=droppilot:droppilot backend/ /app/")
        assert install < source, f"{name} copies source before installing dependencies"

    @pytest.mark.parametrize("name", sorted(PYTHON_DOCKERFILES))
    def test_only_the_dependency_inputs_are_copied_into_the_builder(self, name: str) -> None:
        builder = PYTHON_DOCKERFILES[name].read_text(encoding="utf-8").split("AS runtime", 1)[0]
        copies = re.findall(r"^COPY\s+(\S+)", builder, re.M)
        assert set(copies) <= {
            "backend/pyproject.toml",
            "backend/requirements/runtime.txt",
            "backend/scripts/check_dependency_lock.py",
        }, f"{name} copies more than the dependency inputs into the builder: {copies}"

    def test_only_the_operations_image_carries_postgresql_client_tools(self) -> None:
        assert "postgresql-client-17" in PYTHON_DOCKERFILES["ops"].read_text(encoding="utf-8")
        for name in ("backend", "worker"):
            text = PYTHON_DOCKERFILES[name].read_text(encoding="utf-8")
            assert "postgresql-client" not in text, f"{name} carries pg_dump"

    @pytest.mark.parametrize("name", sorted(PYTHON_DOCKERFILES))
    def test_the_hardening_from_infra_l1_survives(self, name: str) -> None:
        """A dependency change must not quietly undo the security controls."""
        text = PYTHON_DOCKERFILES[name].read_text(encoding="utf-8")
        assert "USER droppilot" in text
        assert "org.opencontainers.image.revision" in text
        assert "APP_SHA" in text
        assert "STOPSIGNAL SIGTERM" in text

    @pytest.mark.parametrize("name", sorted(PYTHON_DOCKERFILES))
    def test_no_secret_is_accepted_as_a_build_argument(self, name: str) -> None:
        text = PYTHON_DOCKERFILES[name].read_text(encoding="utf-8")
        for argument in re.findall(r"^ARG\s+([A-Za-z_]+)", text, re.M):
            lowered = argument.lower()
            assert not any(
                marker in lowered for marker in ("secret", "password", "token", "key")
            ), f"{name} takes {argument} as a build argument"


class TestTheLockCarriesNothingSensitive:
    @pytest.mark.parametrize("path", [RUNTIME_LOCK, DEV_LOCK], ids=["runtime", "dev"])
    def test_no_credential_or_private_index_appears(self, path: Path) -> None:
        """A lockfile compiled against an authenticated index can embed the
        credential in the URL, and it is committed for everyone to read."""
        text = path.read_text(encoding="utf-8")
        assert "@" not in text.split("--hash")[0] or "://" not in text
        for marker in ("--index-url", "--extra-index-url", "://", "token", "password"):
            assert marker not in text.lower(), f"{path.name} contains {marker}"


# ---------------------------------------------------------------------------
# The rehearsal is absent, and says so
# ---------------------------------------------------------------------------


class TestTheRehearsalRunnerRefusesByDefault:
    def text(self) -> str:
        return REHEARSAL.read_text(encoding="utf-8")

    def test_it_exists_and_is_executable(self) -> None:
        import shutil

        git = shutil.which("git")
        assert git, "git is required to read the recorded file mode"
        result = subprocess.run(  # noqa: S603 - fixed argv, resolved executable
            [git, "ls-files", "-s", "scripts/deploy/rehearse_linux.sh"],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.split()[0] == "100755"

    def test_it_fails_fast(self) -> None:
        assert "set -Eeuo pipefail" in self.text()

    def test_it_refuses_a_protected_database_name_case_insensitively(self) -> None:
        """BACKUP-B1-R1 established that a protection you can step over by
        holding shift is decoration. The same applies here."""
        text = self.text()
        assert "tr '[:upper:]' '[:lower:]'" in text
        assert "protected production database name" in text

    def test_it_trims_whitespace_before_comparing_the_database_name(self) -> None:
        assert "tr -d '[:space:]'" in self.text()

    def test_it_refuses_production_hostnames(self) -> None:
        text = self.text()
        for hostname in ("app.whiteto.com", "api.whiteto.com", "auth.whiteto.com"):
            assert hostname in text, f"{hostname} is not refused"
        assert "rds.amazonaws.com" in text

    def test_it_refuses_a_dirty_tree_and_the_wrong_commit(self) -> None:
        text = self.text()
        assert "git status --porcelain" in text
        assert "EXPECTED_SHA" in text

    def test_it_requires_an_isolated_environment_file(self) -> None:
        assert "REHEARSAL_ENV_FILE:?" in self.text()

    def test_it_never_selects_the_backup_profile(self) -> None:
        text = self.text()
        assert "--profile backup" not in text
        assert 'COMPOSE_PROFILES=""' in text

    def test_it_contacts_no_provider_and_installs_nothing(self) -> None:
        text = directives(REHEARSAL)
        for forbidden in (
            "systemctl",
            "cloudflared tunnel create",
            "aws s3",
            "resend.com",
            "accounts.google.com",
            "TERMS_PUBLISHED",
        ):
            assert forbidden not in text, f"the runner references {forbidden}"

    def test_it_builds_without_cache(self) -> None:
        """A rehearsal that reuses layers proves the layers, not the build."""
        assert "--no-cache" in self.text()

    def test_it_proves_no_port_is_published(self) -> None:
        assert "ss -ltnH" in self.text()

    def test_it_reuses_the_existing_health_authority(self) -> None:
        """Two authorities that drift is how a rehearsal proves something the
        deployment does not do."""
        assert "verify_health.sh" in self.text()

    def test_it_cleans_up_only_what_it_created(self) -> None:
        commands = directives(REHEARSAL)
        assert "docker system prune" not in commands
        assert "docker image prune" not in commands
        assert "droppilot-${image}:rehearsal" in commands


class TestEvidenceCannotBeMistakenForProof:
    def test_the_template_begins_not_executed(self) -> None:
        text = EVIDENCE.read_text(encoding="utf-8")
        assert "**STATUS: NOT EXECUTED**" in text
        assert text.count("NOT EXECUTED") > 20

    def test_nothing_in_the_application_reads_the_evidence_file(self) -> None:
        """The property that makes hand-editing it harmless.

        A file that could flip a readiness check would be a file worth forging
        under deadline pressure. Every gate derives its answer from the system.
        """
        for directory in (BACKEND / "app", BACKEND / "scripts"):
            for path in directory.rglob("*.py"):
                text = path.read_text(encoding="utf-8")
                assert "rehearsal-evidence" not in text, f"{path} reads the evidence file"
                assert "rehearsal_evidence" not in text, f"{path} reads the evidence file"

    def test_the_readiness_rules_do_not_consult_it(self) -> None:
        readiness = (BACKEND / "app" / "core" / "production_readiness.py").read_text(
            encoding="utf-8"
        )
        assert "rehearsal" not in readiness.lower()

    def test_the_template_says_plainly_that_it_is_not_a_gate(self) -> None:
        text = EVIDENCE.read_text(encoding="utf-8")
        assert "Nothing reads it" in text or "nothing reads it" in text

    def test_it_records_mocked_versus_real(self) -> None:
        text = EVIDENCE.read_text(encoding="utf-8")
        assert "Mocked versus real" in text
        assert "mocked" in text.lower()


class TestTheDocumentationClaimsNoLinuxExecution:
    #: Phrases that would each assert something nobody has observed.
    FORBIDDEN = (
        "production-ready",
        "production ready",
        "deployment-ready",
        "deployment ready",
        "proven on linux",
        "fully verified",
    )

    @pytest.mark.parametrize("phrase", FORBIDDEN)
    def test_the_runbook_avoids_overclaiming(self, phrase: str) -> None:
        assert phrase not in RUNBOOK.read_text(encoding="utf-8").lower()

    def test_the_runbook_states_what_has_not_been_done(self) -> None:
        text = prose(RUNBOOK)
        for absence in (
            "no image has been built",
            "no container has ever started",
            "no vulnerability scan has run",
            "no secret scan has run",
            "no Playwright suite has run against a Linux stack",
        ):
            assert absence in text, f"the runbook does not state: {absence}"

    def test_the_runbook_records_the_operator_decision(self) -> None:
        text = prose(RUNBOOK)
        assert "does not block integrating this foundation" in text
        assert "block every deployment" in text

    def test_the_deferral_is_the_first_thing_a_reader_meets(self) -> None:
        """Buried at the end it would be a disclaimer; at the top it is a
        finding."""
        text = RUNBOOK.read_text(encoding="utf-8")
        heading = text.index("## 0. The Linux rehearsal has not been run")
        assert heading < text.index("## 1. Topology")
