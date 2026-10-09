"""D-019: a feature switch nobody reads would be a disconnected button.
Every known switch must be seeded by the migration and read by application
code other than the flag service itself."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services import feature_flags

pytestmark = pytest.mark.unit

BACKEND = Path(__file__).resolve().parents[2]


def _constant_for(key: str) -> str:
    return next(
        name
        for name in ("AI_BULK_PIPELINE", "SUPPLIER_AUTO_ORDERING", "CHANNEL_PUBLISHING")
        if getattr(feature_flags, name) == key
    )


@pytest.mark.parametrize("key", sorted(feature_flags.KNOWN_FLAGS))
def test_every_switch_is_seeded_by_the_migration(key: str) -> None:
    migration = (BACKEND / "alembic/versions/0055_billing_overrides_feature_flags.py").read_text(
        encoding="utf-8"
    )
    assert f'"{key}"' in migration


@pytest.mark.parametrize("key", sorted(feature_flags.KNOWN_FLAGS))
def test_every_switch_has_a_reader_outside_the_flag_service(key: str) -> None:
    constant = _constant_for(key)
    readers = [
        path
        for path in (BACKEND / "app").rglob("*.py")
        if path.name != "feature_flags.py"
        and constant in path.read_text(encoding="utf-8")
        and "platform_workspace" not in path.name
    ]
    assert readers, f"{key} is defined but nothing enforces it"
