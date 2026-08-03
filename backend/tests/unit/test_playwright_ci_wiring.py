"""CI / Playwright e2e wiring for audit A-06."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO = Path(__file__).resolve().parents[3]


def test_playwright_boots_standalone_not_next_start() -> None:
    config = (_REPO / "frontend" / "playwright.config.ts").read_text(encoding="utf-8")
    assert "npm run start:e2e" in config
    assert 'command: "npm run start"' not in config


def test_env_example_keeps_production_rate_limit_default() -> None:
    text = (_REPO / ".env.example").read_text(encoding="utf-8")
    assert "SECURITY_RATE_LIMIT_REQUESTS=100" in text
    assert "SECURITY_RATE_LIMIT_REQUESTS=1000" in text  # documented e2e ceiling


def test_ci_defines_playwright_job_with_elevated_rate_limit() -> None:
    ci = (_REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "frontend-e2e:" in ci
    assert (
        'SECURITY_RATE_LIMIT_REQUESTS: "1000"' in ci
        or "SECURITY_RATE_LIMIT_REQUESTS: '1000'" in ci
        or "SECURITY_RATE_LIMIT_REQUESTS: 1000" in ci
    )
    assert "playwright test" in ci
