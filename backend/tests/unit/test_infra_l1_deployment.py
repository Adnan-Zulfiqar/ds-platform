"""INFRA-L1 — the Lightsail deployment foundation, asserted rather than intended.

Deployment configuration fails differently from application code. It fails
silently, in production, at the moment somebody is depending on it — a published
port nobody noticed, a development server in a production image, a database
connection that was encrypted but never authenticated. None of it shows up in
a request-handling test.

So these tests read the artefacts themselves. They parse the production Compose
file, the Dockerfiles, the deployment scripts and the cloudflared template, and
assert the properties those files exist to have. A reviewer can check the
assertions against the brief; the files cannot drift away from them without a
failure.

**What these tests are not.** They do not prove the stack runs. That is the
Linux rehearsal's job, and it needs a Docker daemon. What they prove is that
what *would* be run is shaped correctly — which is the half that can be checked
deterministically, on any machine, in under a second.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, ClassVar

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[3]
COMPOSE = REPO / "docker" / "compose.lightsail.yml"
DEV_COMPOSE = REPO / "docker-compose.yml"
DOCKERFILES = {
    "backend": REPO / "docker" / "backend.Dockerfile",
    "worker": REPO / "docker" / "worker.Dockerfile",
    "frontend": REPO / "docker" / "frontend.Dockerfile",
    "ops": REPO / "docker" / "ops.Dockerfile",
}
ALL_DEPLOY_SCRIPTS = sorted((REPO / "scripts" / "deploy").glob("*.sh"))

#: The rehearsal runner is held to different rules from the deployment scripts
#: and is excluded here deliberately. A deployment must never delete anything; a
#: rehearsal must remove exactly what it created, or the next run starts from
#: someone else's leftovers. `test_infra_l1_r1_reproducible_builds.py` asserts
#: its side of that.
REHEARSAL_SCRIPT = REPO / "scripts" / "deploy" / "rehearse_linux.sh"
DEPLOY_SCRIPTS = [s for s in ALL_DEPLOY_SCRIPTS if s != REHEARSAL_SCRIPT]
CLOUDFLARED = REPO / "deploy" / "lightsail" / "cloudflared" / "config.yml.example"
SYSTEMD = REPO / "deploy" / "lightsail" / "systemd"
APP_ENV_EXAMPLE = REPO / "deploy" / "lightsail" / "app.env.example"

#: Ports that must never be reachable from outside the instance. The tunnel is
#: the only ingress, and it reaches services by container name on a private
#: Docker network.
INTERNAL_PORTS = ("3000", "8000", "6379", "5672", "15672", "5432")


def directives(path: Path) -> str:
    """The file with comment lines removed.

    Every check below asks "does this file *do* X", and a comment explaining why
    it deliberately does not do X names X. Grepping raw text therefore fails on
    exactly the files that document themselves best, which is the wrong
    incentive to create.
    """
    kept = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        kept.append(line.split(" # ")[0] if " # " in line else line)
    return chr(10).join(kept)


def git_file_mode(relative: str) -> str:
    """The mode Git records, which is what a Linux checkout will have.

    A filesystem check is useless here: this repository is developed on Windows,
    where NTFS has no execute bit and `stat` reports whatever it likes. What
    lands on the Lightsail instance is what Git stored.
    """
    import shutil
    import subprocess

    git = shutil.which("git")
    assert git, "git is required to read the recorded file mode"
    result = subprocess.run(  # noqa: S603 - fixed argv, resolved executable
        [git, "ls-files", "-s", relative],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip(), f"{relative} is not tracked"
    return result.stdout.split()[0]


def compose_document() -> dict[str, Any]:
    """The production Compose file as data.

    Parsed rather than rendered by `docker compose config`, deliberately: these
    tests must run on a machine with no Docker daemon, and the properties being
    asserted are all present in the source. The rehearsal renders it for real.
    """
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def services() -> dict[str, dict[str, Any]]:
    return compose_document()["services"]


# ---------------------------------------------------------------------------
# Nothing is exposed
# ---------------------------------------------------------------------------


class TestNoServiceIsReachableFromOutsideTheInstance:
    def test_not_one_service_publishes_a_host_port(self) -> None:
        """The single most consequential property in the file.

        A published port is reachable the moment the Lightsail firewall is
        edited by anyone, and the tunnel makes every one of them unnecessary.
        """
        published = {name: s["ports"] for name, s in services().items() if s.get("ports")}
        assert published == {}, f"these services publish host ports: {published}"

    def test_the_raw_text_contains_no_port_mapping(self) -> None:
        """Belt and braces: a `ports:` key added under any nesting is caught."""
        for line in COMPOSE.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("- ") and re.match(r'- "?\d+:\d+', stripped):
                pytest.fail(f"a port mapping appears in the production Compose file: {stripped}")

    def test_no_service_is_privileged(self) -> None:
        assert [n for n, s in services().items() if s.get("privileged")] == []

    def test_no_service_uses_host_networking(self) -> None:
        assert [n for n, s in services().items() if s.get("network_mode") == "host"] == []

    def test_no_service_mounts_the_docker_socket(self) -> None:
        """A container with the Docker socket is root on the host, transitively."""
        for name, service in services().items():
            for volume in service.get("volumes", []):
                assert "docker.sock" not in str(volume), f"{name} mounts the Docker socket"

    def test_no_service_mounts_a_broad_host_path(self) -> None:
        """`/etc/droppilot` is deliberate and narrow; `/` or `/etc` would not be."""
        forbidden = ("/:/", "/etc:/", "/var:/", "/home:/", "/root:/", "/usr:/")
        for name, service in services().items():
            for volume in service.get("volumes", []):
                text = str(volume)
                for prefix in forbidden:
                    assert not text.startswith(prefix), f"{name} mounts {text}"

    def test_the_broker_and_cache_networks_have_no_route_out(self) -> None:
        """`internal: true` removes the gateway, so Redis and RabbitMQ cannot
        reach the internet even if something in them tries."""
        networks = compose_document()["networks"]
        assert networks["internal"]["internal"] is True

    @pytest.mark.parametrize("name", ["redis", "rabbitmq", "worker", "beat", "migrate"])
    def test_backend_services_are_not_on_the_edge_network(self, name: str) -> None:
        """cloudflared sits only on `edge`. Anything not on `edge` is
        unreachable from the tunnel — which is stronger than merely unrouted."""
        assert "edge" not in services()[name]["networks"]

    def test_only_the_backend_bridges_both_networks(self) -> None:
        both = {
            n for n, s in services().items() if set(s.get("networks", [])) >= {"edge", "internal"}
        }
        assert both == {"backend"}, f"unexpected services bridge edge and internal: {both}"

    def test_rabbitmq_does_not_run_the_management_plugin(self) -> None:
        """The management UI is an admin interface with its own auth. The only
        way to be certain it is not exposed is for it not to be running."""
        assert "-management" not in services()["rabbitmq"]["image"]


# ---------------------------------------------------------------------------
# Hardening
# ---------------------------------------------------------------------------


class TestContainerHardening:
    #: Redis and RabbitMQ write to their data volumes, so a read-only root
    #: filesystem is not applicable to them. Everything that only reads code is
    #: held to it.
    READ_ONLY_EXPECTED = ("frontend", "backend", "worker", "beat", "cloudflared", "migrate")

    @pytest.mark.parametrize("name", READ_ONLY_EXPECTED)
    def test_application_containers_have_a_read_only_root_filesystem(self, name: str) -> None:
        assert services()[name]["read_only"] is True

    @pytest.mark.parametrize("name", READ_ONLY_EXPECTED)
    def test_a_read_only_container_is_given_writable_scratch_explicitly(self, name: str) -> None:
        """Read-only without a tmpfs is a container that cannot write a temp
        file and fails in a way nobody predicted."""
        assert services()[name].get("tmpfs"), f"{name} is read-only with no writable scratch"

    def test_every_service_drops_all_capabilities(self) -> None:
        for name, service in services().items():
            assert service.get("cap_drop") == ["ALL"], f"{name} does not drop all capabilities"

    def test_every_service_forbids_privilege_escalation(self) -> None:
        for name, service in services().items():
            assert "no-new-privileges:true" in service.get("security_opt", []), name

    def test_every_service_has_bounded_logging(self) -> None:
        """One crash-looping container must not fill the instance's disk and
        take the database connection pool down with it."""
        for name, service in services().items():
            options = service.get("logging", {}).get("options", {})
            assert options.get("max-size"), f"{name} has unbounded logs"
            assert options.get("max-file"), f"{name} has unbounded log rotation"

    def test_long_running_services_have_resource_limits(self) -> None:
        for name, service in services().items():
            if service.get("restart") == "no":
                continue  # one-off jobs
            limits = service.get("deploy", {}).get("resources", {}).get("limits", {})
            assert limits.get("memory"), f"{name} has no memory limit"

    def test_one_off_jobs_never_restart(self) -> None:
        """A migration that failed must be looked at, not retried in a loop
        against a database it may have partially changed."""
        for name in ("migrate", "readiness", "backup"):
            assert services()[name]["restart"] == "no", name


# ---------------------------------------------------------------------------
# Dependencies are readiness, not existence
# ---------------------------------------------------------------------------


class TestDependenciesMeanReady:
    @pytest.mark.parametrize("name", ["backend", "worker"])
    def test_application_services_wait_for_health_not_start(self, name: str) -> None:
        """`depends_on` alone means "the container was created". A worker that
        starts against a broker that is still booting crash-loops."""
        for dependency, condition in services()[name]["depends_on"].items():
            assert condition["condition"] in (
                "service_healthy",
                "service_completed_successfully",
            ), f"{name} depends on {dependency} without waiting for readiness"

    @pytest.mark.parametrize("name", ["backend", "worker", "beat"])
    def test_nothing_starts_before_the_migration_has_succeeded(self, name: str) -> None:
        """`service_completed_successfully` is the only condition that means the
        work is done rather than that the process started."""
        assert (
            services()[name]["depends_on"]["migrate"]["condition"]
            == "service_completed_successfully"
        )

    def test_the_backend_health_check_touches_its_dependencies(self) -> None:
        """Liveness would report a process that is up and can serve nothing —
        the state that most needs to fail a health check."""
        test = " ".join(services()["backend"]["healthcheck"]["test"])
        assert "/health/ready" in test
        assert "/health/live" not in test

    def test_every_long_running_service_has_a_health_check(self) -> None:
        for name, service in services().items():
            if service.get("restart") == "no" or name == "beat":
                continue
            assert service.get("healthcheck"), f"{name} has no health check"

    def test_the_worker_is_given_time_to_finish_in_flight_tasks(self) -> None:
        """Celery's warm shutdown finishes what it is holding. A short grace
        period SIGKILLs a task mid-write."""
        assert services()["worker"]["stop_grace_period"] == "60s"


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------


class TestProductionImages:
    @pytest.mark.parametrize("name", sorted(DOCKERFILES))
    def test_every_image_is_multi_stage(self, name: str) -> None:
        text = DOCKERFILES[name].read_text(encoding="utf-8")
        assert text.count("FROM ") >= 2, f"{name} is not multi-stage"

    @pytest.mark.parametrize("name", sorted(DOCKERFILES))
    def test_every_image_runs_as_a_non_root_user(self, name: str) -> None:
        text = DOCKERFILES[name].read_text(encoding="utf-8")
        users = re.findall(r"^USER\s+(\S+)", text, re.MULTILINE)
        assert users, f"{name} never switches away from root"
        assert users[-1] != "root", f"{name} ends as root"

    @pytest.mark.parametrize("name", sorted(DOCKERFILES))
    def test_every_image_records_the_commit_it_was_built_from(self, name: str) -> None:
        """`docker inspect` answers "what is actually running", which is the
        first question in every incident."""
        text = DOCKERFILES[name].read_text(encoding="utf-8")
        assert "org.opencontainers.image.revision" in text
        assert "APP_SHA" in text

    @pytest.mark.parametrize("name", sorted(DOCKERFILES))
    def test_no_compiler_toolchain_survives_into_the_runtime_stage(self, name: str) -> None:
        runtime = directives(DOCKERFILES[name]).split("AS runtime", 1)[-1]
        for tool in ("build-essential", "gcc", "libpq-dev", "npm ci", "npm run build"):
            assert tool not in runtime, f"{name}'s runtime stage installs {tool}"

    @pytest.mark.parametrize("name", sorted(DOCKERFILES))
    def test_no_image_carries_a_secret_as_a_build_argument(self, name: str) -> None:
        """A build argument is recorded in the image's history, readable by
        anyone who can pull it."""
        text = DOCKERFILES[name].read_text(encoding="utf-8")
        for arg in re.findall(r"^ARG\s+([A-Z_]+)", text, re.MULTILINE):
            lowered = arg.lower()
            assert not any(
                marker in lowered for marker in ("secret", "password", "token", "key")
            ), f"{name} takes {arg} as a build argument"

    def test_the_frontend_builds_the_standalone_production_output(self) -> None:
        text = DOCKERFILES["frontend"].read_text(encoding="utf-8")
        assert "npm run build" in text
        assert ".next/standalone" in text
        assert "NODE_ENV=production" in text

    def test_no_image_runs_a_development_server(self) -> None:
        for name, path in DOCKERFILES.items():
            text = directives(path)
            for marker in ("--reload", "next dev", "npm run dev", "--debug"):
                assert marker not in text, f"{name} runs a development server ({marker})"

    def test_the_operations_image_has_no_default_action(self) -> None:
        """Starting it by accident must not migrate the database."""
        command = re.search(r"^CMD\s+(.+)$", directives(DOCKERFILES["ops"]), re.MULTILINE)
        assert command is not None
        # The message it prints may name alembic; what matters is that it does
        # not *run* it.
        assert "alembic" not in command.group(1).split("sys.exit", 1)[0]

    def test_the_operations_image_carries_a_matching_postgresql_client(self) -> None:
        """A client older than the server cannot read its custom-format dumps,
        and discovering that during a recovery is the worst possible time."""
        text = DOCKERFILES["ops"].read_text(encoding="utf-8")
        assert "postgresql-client-17" in text

    def test_only_the_operations_image_carries_database_tools(self) -> None:
        """Dump tools inside the internet-facing process is the one place they
        must not be."""
        for name in ("backend", "worker", "frontend"):
            text = DOCKERFILES[name].read_text(encoding="utf-8")
            assert "postgresql-client" not in text, f"{name} carries pg_dump"

    @pytest.mark.parametrize("name", sorted(DOCKERFILES))
    def test_every_image_declares_its_stop_signal(self, name: str) -> None:
        """Graceful shutdown depends on it, and a silent change breaks draining
        in a way that only shows up as truncated requests under load."""
        assert "STOPSIGNAL SIGTERM" in DOCKERFILES[name].read_text(encoding="utf-8")

    @pytest.mark.parametrize("name", sorted(DOCKERFILES))
    def test_base_images_are_pinned_to_a_version(self, name: str) -> None:
        for line in DOCKERFILES[name].read_text(encoding="utf-8").splitlines():
            if line.startswith("FROM "):
                reference = line.split()[1]
                assert ":" in reference, f"{name} uses an unpinned base image: {reference}"
                assert not reference.endswith(":latest"), f"{name} tracks :latest"


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------


class TestSecretsStayOutOfEveryArtefact:
    def test_the_compose_file_names_variables_and_holds_no_values(self) -> None:
        text = COMPOSE.read_text(encoding="utf-8")
        for marker in ("SECURITY_SECRET_KEY:", "RESEND_API_KEY:", "SECURITY_ENCRYPTION_KEYS:"):
            assert marker not in text, f"the Compose file assigns {marker}"

    def test_mandatory_variables_have_no_defaults(self) -> None:
        """`${VAR:-fallback}` on a secret is how a deployment silently runs on a
        published placeholder. `${VAR:?}` refuses instead."""
        text = COMPOSE.read_text(encoding="utf-8")
        for variable in (
            "POSTGRES_PASSWORD",
            "RABBITMQ_PASSWORD",
            "ENVIRONMENT",
            "DEPLOY_ENV_FILE",
            "BACKEND_IMAGE",
            "FRONTEND_IMAGE",
            "WORKER_IMAGE",
            "OPS_IMAGE",
        ):
            assert f"${{{variable}:-" not in text, f"{variable} has a default"
            assert f"${{{variable}:?" in text, f"{variable} is not mandatory"

    #: The scanner requires these assignments to be blank rather than a
    #: `CHANGE-ME` placeholder, since a placeholder that merely looks like a
    #: secret is still a string an eager reviewer could ship as one.
    SCANNER_REQUIRED_BLANK: ClassVar[set[str]] = {
        "SECURITY_ENCRYPTION_KEYS",
        "SHOPIFY_API_KEY",
        "SHOPIFY_API_SECRET",
        "ALIEXPRESS_APP_SECRET",
        "ALIEXPRESS_CATALOG_ACCESS_TOKEN",
    }

    def test_the_environment_template_contains_no_real_value(self) -> None:
        text = APP_ENV_EXAMPLE.read_text(encoding="utf-8")
        for line in text.splitlines():
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            stripped_name = name.strip()
            if stripped_name in self.SCANNER_REQUIRED_BLANK:
                assert value == "", f"{name} must be blank, not a placeholder"
                continue
            if stripped_name in {
                "ENVIRONMENT",
                "POSTGRES_PORT",
                "POSTGRES_DB",
                "POSTGRES_SSLMODE",
                "POSTGRES_SSLROOTCERT",
                "POSTGRES_POOL_SIZE",
                "POSTGRES_MAX_OVERFLOW",
                "SECURITY_COOKIE_SECURE",
                "SECURITY_TRUSTED_PROXIES",
                "RABBITMQ_USER",
                "EMAIL_PROVIDER",
                "EMAIL_FROM",
                "ALLOWED_HOSTS",
                "CORS_ORIGINS",
                "LOG_LEVEL",
                "LOG_JSON_OUTPUT",
                "LOG_INCLUDE_REQUEST_BODY",
                "NEXT_PUBLIC_API_URL",
                "SHOPIFY_FRONTEND_RETURN_URL",
                # Blank on purpose: the platform admin panel stays off (D-015).
                "PLATFORM_ADMIN_ALLOWED_CIDRS",
                "ALIEXPRESS_FRONTEND_RETURN_URL",
                "EBAY_FRONTEND_RETURN_URL",
            }:
                continue
            assert "CHANGE-ME" in value, f"{name} in the template is not a placeholder"

    def test_no_secret_is_exposed_through_a_public_variable(self) -> None:
        """NEXT_PUBLIC_* is inlined into the bundle and shipped to every
        browser. Only the Google client ID belongs there, and it is public."""
        text = APP_ENV_EXAMPLE.read_text(encoding="utf-8")
        public = re.findall(r"^(NEXT_PUBLIC_\w+)=", text, re.MULTILINE)
        assert set(public) <= {"NEXT_PUBLIC_API_URL", "NEXT_PUBLIC_GOOGLE_CLIENT_ID"}, public

    def test_no_google_client_secret_is_invented(self) -> None:
        """The flow verifies an ID token issued to the browser; it never
        exchanges an authorization code. A secret would be an unused credential
        sitting in a file, which is a liability rather than a control."""
        for path in (APP_ENV_EXAMPLE, COMPOSE):
            assert "GOOGLE_OAUTH_CLIENT_SECRET" not in path.read_text(encoding="utf-8")

    def test_the_deployment_directory_is_outside_the_repository(self) -> None:
        assert not (REPO / "deploy" / "lightsail" / "app.env").exists()
        assert not (REPO / "etc").exists()

    def test_no_deployment_script_echoes_an_environment_file(self) -> None:
        """`cat`ing the env file into a deployment log puts every secret in the
        journal, where it is kept and rotated by nobody."""
        for script in DEPLOY_SCRIPTS:
            text = script.read_text(encoding="utf-8")
            for marker in (
                "cat $DEPLOY_ENV",
                'cat "$DEPLOY_ENV"',
                "cat $APP_ENV",
                'cat "$APP_ENV"',
            ):
                assert marker not in text, f"{script.name} prints an environment file"
            assert "env | " not in text, f"{script.name} dumps its environment"


# ---------------------------------------------------------------------------
# Deployment scripts
# ---------------------------------------------------------------------------


class TestDeploymentScripts:
    def test_the_expected_scripts_exist(self) -> None:
        assert {s.name for s in DEPLOY_SCRIPTS} == {
            "preflight.sh",
            "build_images.sh",
            "migrate.sh",
            "deploy.sh",
            "verify_health.sh",
            "rollback.sh",
        }

    def test_the_rehearsal_runner_is_present_but_separate(self) -> None:
        """It shares the directory and not the rules — see the note above."""
        assert REHEARSAL_SCRIPT.is_file()
        assert REHEARSAL_SCRIPT not in DEPLOY_SCRIPTS

    @pytest.mark.parametrize("script", DEPLOY_SCRIPTS, ids=lambda s: s.name)
    def test_every_script_fails_fast_on_error(self, script: Path) -> None:
        """Without `set -e` a failed step is followed by the next one, and a
        deployment continues past the check that was supposed to stop it."""
        assert "set -Eeuo pipefail" in script.read_text(encoding="utf-8")

    @pytest.mark.parametrize("script", DEPLOY_SCRIPTS, ids=lambda s: s.name)
    def test_every_script_is_executable_when_checked_out(self, script: Path) -> None:
        """Mode 644 would land on the instance as "Permission denied"."""
        mode = git_file_mode(f"scripts/deploy/{script.name}")
        assert mode == "100755", f"{script.name} is tracked as {mode}, not executable"

    def test_preflight_requires_the_accepted_commit_and_a_clean_tree(self) -> None:
        text = (REPO / "scripts" / "deploy" / "preflight.sh").read_text(encoding="utf-8")
        assert "git status --porcelain" in text
        assert "ACCEPTED_SHA" in text
        assert "git rev-parse HEAD" in text

    def test_preflight_refuses_a_published_port(self) -> None:
        text = (REPO / "scripts" / "deploy" / "preflight.sh").read_text(encoding="utf-8")
        assert "published:" in text
        assert "publishes a host port" in text

    def test_preflight_refuses_a_development_server(self) -> None:
        text = directives(REPO / "scripts" / "deploy" / "preflight.sh")
        assert "--reload" in text and "next dev" in text

    def test_preflight_requires_digest_pinned_images(self) -> None:
        """A tag is a moving reference; "roll back to what was running" cannot
        be stated precisely against one."""
        text = (REPO / "scripts" / "deploy" / "preflight.sh").read_text(encoding="utf-8")
        assert "@sha256:" in text
        assert "not pinned by digest" in text

    def test_preflight_checks_secret_file_permissions(self) -> None:
        text = (REPO / "scripts" / "deploy" / "preflight.sh").read_text(encoding="utf-8")
        assert '"600"' in text and '"700"' in text

    def test_preflight_runs_the_readiness_audit(self) -> None:
        text = (REPO / "scripts" / "deploy" / "preflight.sh").read_text(encoding="utf-8")
        assert "readiness" in text

    def test_preflight_reports_the_backup_blocker_honestly(self) -> None:
        text = (REPO / "scripts" / "deploy" / "preflight.sh").read_text(encoding="utf-8")
        assert "BLOCKED" in text
        assert "no production backup regime is operational" in text

    def test_the_build_script_refuses_a_loopback_or_plaintext_api_url(self) -> None:
        """Inlined at build time, so this is the last moment it can be caught."""
        text = (REPO / "scripts" / "deploy" / "build_images.sh").read_text(encoding="utf-8")
        assert "localhost" in text and "must be https" in text

    def test_the_build_script_passes_the_commit_into_every_image(self) -> None:
        text = (REPO / "scripts" / "deploy" / "build_images.sh").read_text(encoding="utf-8")
        assert "APP_SHA=${ACCEPTED_SHA}" in text

    def test_deployment_preserves_the_previous_image_set(self) -> None:
        text = (REPO / "scripts" / "deploy" / "deploy.sh").read_text(encoding="utf-8")
        assert "PREVIOUS" in text
        assert "install -m 600" in text

    def test_deployment_waits_for_health_rather_than_for_containers(self) -> None:
        text = (REPO / "scripts" / "deploy" / "deploy.sh").read_text(encoding="utf-8")
        assert "--wait" in text

    def test_rollback_requires_a_typed_confirmation_at_a_terminal(self) -> None:
        text = (REPO / "scripts" / "deploy" / "rollback.sh").read_text(encoding="utf-8")
        assert "-t 0" in text
        assert 'typed" == "rollback"' in text

    def test_rollback_raises_the_schema_question_rather_than_assuming(self) -> None:
        """There is no production backup to restore from, so a rollback across a
        destructive migration is a decision, not a command."""
        text = (REPO / "scripts" / "deploy" / "rollback.sh").read_text(encoding="utf-8")
        assert "REMOVED or" in text
        assert "alembic" in text

    def test_no_deployment_script_deletes_an_image_or_a_volume(self) -> None:
        """A deployment that prunes has no rollback target left to return to."""
        for script in DEPLOY_SCRIPTS:
            text = script.read_text(encoding="utf-8")
            destructive_commands = (
                "docker system prune",
                "docker volume rm",
                "docker image prune",
                "-v --remove-orphans",
            )
            for destructive in destructive_commands:
                assert destructive not in text, f"{script.name} runs {destructive}"


# ---------------------------------------------------------------------------
# Cloudflare
# ---------------------------------------------------------------------------


class TestCloudflareRouting:
    def document(self) -> dict[str, Any]:
        return yaml.safe_load(CLOUDFLARED.read_text(encoding="utf-8"))

    def test_each_public_hostname_maps_to_exactly_one_internal_service(self) -> None:
        ingress = self.document()["ingress"]
        routes = {r["hostname"]: r["service"] for r in ingress if "hostname" in r}
        assert routes == {
            "app.whiteto.com": "http://frontend:3000",
            "api.whiteto.com": "http://backend:8000",
        }

    def test_the_catch_all_refuses_rather_than_routing(self) -> None:
        """A catch-all pointing at the backend publishes the API under every
        hostname anyone ever points at this tunnel."""
        final = self.document()["ingress"][-1]
        assert "hostname" not in final
        assert final["service"] == "http_status:404"

    def test_no_wildcard_hostname_exposes_internal_services(self) -> None:
        for route in self.document()["ingress"]:
            hostname = route.get("hostname", "")
            assert not hostname.startswith("*"), f"wildcard route: {hostname}"

    def test_the_credential_lives_outside_the_repository(self) -> None:
        document = self.document()
        assert document["credentials-file"].startswith("/etc/cloudflared/")
        assert not list(CLOUDFLARED.parent.glob("*.json"))

    def test_metrics_are_bound_to_loopback_only(self) -> None:
        assert self.document()["metrics"].startswith("127.0.0.1:")

    def test_the_tunnel_does_not_update_itself(self) -> None:
        """An unattended binary replacing itself on a production host is a
        supply-chain change nobody reviewed."""
        command = services()["cloudflared"]["command"]
        assert "--no-autoupdate" in command

    def test_the_template_is_not_installed(self) -> None:
        assert CLOUDFLARED.name.endswith(".example")


# ---------------------------------------------------------------------------
# systemd
# ---------------------------------------------------------------------------


class TestSystemdUnits:
    def unit(self, name: str) -> str:
        return (SYSTEMD / name).read_text(encoding="utf-8")

    def test_every_unit_is_a_template_rather_than_installed(self) -> None:
        for path in SYSTEMD.iterdir():
            assert path.name.endswith(".example"), path.name

    def test_the_stack_starts_after_docker_and_a_configured_network(self) -> None:
        """`network-online` rather than `network`: the difference is starting
        when an interface exists versus when it can reach the database."""
        text = self.unit("droppilot.service.example")
        assert "After=docker.service network-online.target" in text
        assert "Requires=docker.service" in text

    def test_the_stack_unit_waits_for_health(self) -> None:
        """Without `--wait`, systemd reports active while the application is
        still failing its health checks."""
        assert "--wait" in self.unit("droppilot.service.example")

    def test_the_stack_unit_uses_absolute_paths_and_an_explicit_directory(self) -> None:
        text = self.unit("droppilot.service.example")
        assert "WorkingDirectory=/opt/droppilot" in text
        assert "ExecStart=/usr/bin/docker" in text

    def test_restarts_are_throttled(self) -> None:
        """A crash-looping stack hammers the managed database's connection
        limit and buries the original failure."""
        text = self.unit("droppilot.service.example")
        assert "StartLimitBurst=" in text and "StartLimitIntervalSec=" in text

    def test_the_backup_timer_and_service_are_present_but_uninstalled(self) -> None:
        assert (SYSTEMD / "droppilot-backup.service.example").is_file()
        assert (SYSTEMD / "droppilot-backup.timer.example").is_file()

    def test_the_backup_unit_states_that_it_must_not_be_installed_yet(self) -> None:
        text = self.unit("droppilot-backup.service.example")
        assert "MUST NOT BE" in text

    def test_the_backup_timer_survives_a_missed_window(self) -> None:
        assert "Persistent=true" in self.unit("droppilot-backup.timer.example")

    def test_the_backup_service_does_not_retry_in_a_loop(self) -> None:
        """A failed backup is an operator action, not a retry against a database
        that may be under load."""
        assert "Restart=" not in directives(SYSTEMD / "droppilot-backup.service.example")

    def test_no_unit_contains_a_secret(self) -> None:
        """Units reference the environment file; they never inline a value.

        `systemctl show` does not render EnvironmentFile contents, so a secret
        that stays in the file stays out of every operator's terminal.
        """
        for path in SYSTEMD.iterdir():
            text = directives(path)
            for marker in ("PASSWORD=", "SECRET_KEY=", "API_KEY=", "Environment="):
                assert marker not in text, f"{path.name} inlines {marker}"

    def test_the_units_that_need_configuration_read_it_from_a_file(self) -> None:
        for name in ("droppilot.service.example", "droppilot-backup.service.example"):
            assert "EnvironmentFile=/etc/droppilot/" in self.unit(name)


# ---------------------------------------------------------------------------
# The development stack must stay distinguishable
# ---------------------------------------------------------------------------


class TestTheDevelopmentStackIsNotTheProductionOne:
    def test_the_development_compose_file_still_publishes_ports(self) -> None:
        """Not a defect — it is what makes local development work. Asserted so
        that nobody "fixes" it by pointing production at the wrong file."""
        development = yaml.safe_load(DEV_COMPOSE.read_text(encoding="utf-8"))
        assert any(s.get("ports") for s in development["services"].values())

    def test_the_production_file_is_a_separate_file_not_an_override(self) -> None:
        """An override forgotten on the command line is a production deployment
        running a development server."""
        assert COMPOSE.is_file()
        assert "docker-compose.override" not in {p.name for p in REPO.iterdir()}

    def test_production_does_not_mount_source_code(self) -> None:
        """A mounted source directory overrides what was built into the image,
        so the running code is whatever is on the disk rather than what was
        reviewed."""
        for name, service in services().items():
            for volume in service.get("volumes", []):
                assert not str(volume).startswith("./"), f"{name} mounts source"

    def test_production_runs_no_container_for_postgresql(self) -> None:
        """A database in the same Compose project dies with the instance it was
        protecting against losing, and takes its volume with it."""
        assert "postgres" not in services()
        for service in services().values():
            assert "postgres:" not in str(service.get("image", ""))
