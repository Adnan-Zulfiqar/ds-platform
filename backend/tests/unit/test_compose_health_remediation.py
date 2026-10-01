"""Review findings J-4 and K-4, plus the Lightsail beat defect found with them.

* Beat must write its schedule under /tmp: in local compose /app is the
  bind-mounted repository (generated files landed in Git's working tree), and
  on Lightsail the root filesystem is read-only, where beat died at startup
  with ``OSError 30`` (reproduced against the worker image).
* Beat must override the worker image's HEALTHCHECK, which pings a *worker*
  and therefore can never pass for beat.
* nginx's probe must use 127.0.0.1: alpine resolves ``localhost`` to ::1
  first and the server listens on IPv4 only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[3]
LOCAL = REPO_ROOT / "docker-compose.yml"
LIGHTSAIL = REPO_ROOT / "docker" / "compose.lightsail.yml"


def _service(path: Path, name: str) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    service: dict[str, Any] = document["services"][name]
    return service


def _command_text(service: dict[str, Any]) -> str:
    command = service.get("command", "")
    return " ".join(command) if isinstance(command, list) else str(command)


def _healthcheck_text(service: dict[str, Any]) -> str:
    test = service.get("healthcheck", {}).get("test", [])
    return " ".join(test) if isinstance(test, list) else str(test)


@pytest.mark.parametrize("path", [LOCAL, LIGHTSAIL], ids=["local", "lightsail"])
class TestBeat:
    def test_schedule_file_lives_under_tmp(self, path: Path) -> None:
        assert "--schedule=/tmp/celerybeat-schedule" in _command_text(_service(path, "beat"))

    def test_beat_has_its_own_healthcheck(self, path: Path) -> None:
        check = _healthcheck_text(_service(path, "beat"))
        assert "celerybeat-schedule" in check
        assert "inspect ping" not in check

    def test_the_health_window_outlasts_the_shortest_schedule(self, path: Path) -> None:
        from app.workers.celery_app import celery_app

        shortest_minutes = (
            min(float(entry["schedule"]) for entry in celery_app.conf.beat_schedule.values()) / 60
        )
        assert "-mmin -15" in _healthcheck_text(_service(path, "beat"))
        assert shortest_minutes < 15


def test_lightsail_beat_has_a_writable_tmp() -> None:
    beat = _service(LIGHTSAIL, "beat")
    assert beat.get("read_only") is True
    # S108: reads the compose file's tmpfs mount path; creates no temp file.
    assert any(str(mount).startswith("/tmp") for mount in beat.get("tmpfs", []))  # noqa: S108


def test_nginx_probe_uses_the_ipv4_loopback() -> None:
    check = _healthcheck_text(_service(LOCAL, "nginx"))
    assert "http://127.0.0.1/nginx-health" in check
    assert "localhost" not in check


def test_generated_beat_files_are_ignored() -> None:
    ignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "celerybeat-schedule*" in ignore
