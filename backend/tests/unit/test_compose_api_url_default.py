"""Compose frontend API URL defaults to the nginx public origin (audit A-05)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]
_COMPOSE = _REPO_ROOT / "docker-compose.yml"


def test_compose_frontend_defaults_to_nginx_same_origin_api_url() -> None:
    """Browsers via :80 must call same-origin /api, not the published :8000 port."""
    text = _COMPOSE.read_text(encoding="utf-8")
    match = re.search(
        r"NEXT_PUBLIC_API_URL:\s*\$\{NEXT_PUBLIC_API_URL:-([^}]+)\}",
        text,
    )
    assert match is not None, "Compose frontend build arg default not found"
    assert match.group(1) == "http://localhost"
    assert "http://localhost:8000" not in match.group(0)
