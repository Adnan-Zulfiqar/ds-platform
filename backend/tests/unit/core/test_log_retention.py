"""Log pruning must delete old logs and nothing else, ever.

These run without a database or network: the whole module is filesystem logic,
and the interesting cases are all about what it *refuses* to do. Each test names
a way the tool could destroy something it was never pointed at.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.core.log_retention import (
    LogRetentionError,
    build_plan,
    execute_plan,
)

NOW = datetime(2026, 8, 28, 12, 0, 0, tzinfo=UTC)


def write(path: Path, *, age_days: float, content: str = "x") -> Path:
    path.write_text(content, encoding="utf-8")
    stamp = (NOW - timedelta(days=age_days)).timestamp()
    os.utime(path, (stamp, stamp))
    return path


class TestRefusals:
    def test_an_unset_directory_is_refused_rather_than_guessed(self) -> None:
        with pytest.raises(LogRetentionError, match="LOG_DIRECTORY is not set"):
            build_plan(directory=None, retention_days=30, now=NOW)

    def test_a_missing_directory_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(LogRetentionError, match="not an existing directory"):
            build_plan(directory=tmp_path / "nope", retention_days=30, now=NOW)

    @pytest.mark.parametrize(
        "neighbour", [".git", ".env", "alembic.ini", "pyproject.toml", "package.json"]
    )
    def test_a_directory_that_looks_like_a_project_is_refused(
        self, tmp_path: Path, neighbour: str
    ) -> None:
        # The single most dangerous mistake: pointing this at a checkout.
        (tmp_path / neighbour).write_text("", encoding="utf-8")
        write(tmp_path / "app.log", age_days=99)

        with pytest.raises(LogRetentionError, match="project or deployment directory"):
            build_plan(directory=tmp_path, retention_days=30, now=NOW)

    def test_a_zero_retention_window_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(LogRetentionError, match="at least 1"):
            build_plan(directory=tmp_path, retention_days=0, now=NOW)

    def test_a_naive_timestamp_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(LogRetentionError, match="timezone-aware"):
            build_plan(
                directory=tmp_path,
                retention_days=30,
                now=datetime(2026, 8, 28, 12, 0, 0),
            )


class TestWhatItSelects:
    def test_only_log_shaped_names_are_ever_candidates(self, tmp_path: Path) -> None:
        write(tmp_path / "old.log", age_days=99)
        write(tmp_path / "old.log.1", age_days=99)
        # None of these may be touched however old they are.
        write(tmp_path / "database.sql", age_days=99)
        write(tmp_path / "config.json", age_days=99)
        write(tmp_path / "notes.txt", age_days=99)
        write(tmp_path / "archive.tar.gz", age_days=99)

        plan = build_plan(directory=tmp_path, retention_days=30, now=NOW)

        assert {p.path.name for p in plan.delete} == {"old.log.1"}
        # `old.log` is the un-rotated live file and is therefore protected.

    def test_it_never_descends_into_subdirectories(self, tmp_path: Path) -> None:
        nested = tmp_path / "archive"
        nested.mkdir()
        write(nested / "buried.log", age_days=99)
        write(tmp_path / "surface.log", age_days=99)
        write(tmp_path / "surface.log.1", age_days=99)

        plan = build_plan(directory=tmp_path, retention_days=30, now=NOW)

        assert (nested / "buried.log").exists()
        assert all("archive" not in str(item.path) for item in plan.delete)

    def test_the_live_file_survives_whatever_its_age(self, tmp_path: Path) -> None:
        # `app.log` is what a running process appends to. Deleting it out from
        # under an open handle is the one thing worse than keeping it too long.
        write(tmp_path / "app.log", age_days=90)
        write(tmp_path / "app.log.1", age_days=91)
        write(tmp_path / "app.log.2", age_days=92)

        plan = build_plan(directory=tmp_path, retention_days=30, now=NOW)

        assert {p.path.name for p in plan.delete} == {"app.log.1", "app.log.2"}
        assert plan.kept_active == 1

    def test_every_live_file_is_protected_independently(self, tmp_path: Path) -> None:
        write(tmp_path / "api.log", age_days=99)
        write(tmp_path / "api.log.1", age_days=100)
        write(tmp_path / "worker.log", age_days=99)
        write(tmp_path / "worker.log.1", age_days=100)

        plan = build_plan(directory=tmp_path, retention_days=30, now=NOW)

        assert {p.path.name for p in plan.delete} == {"api.log.1", "worker.log.1"}

    def test_a_stale_rotated_file_with_no_live_sibling_is_still_removed(
        self, tmp_path: Path
    ) -> None:
        # The rule this replaced protected the newest file of each stem, which
        # meant an abandoned service's rotated logs were retained forever and the
        # published retention period quietly did not apply to them.
        write(tmp_path / "abandoned.log.1", age_days=400)

        plan = build_plan(directory=tmp_path, retention_days=30, now=NOW)

        assert {p.path.name for p in plan.delete} == {"abandoned.log.1"}


class TestTheBoundary:
    @pytest.mark.parametrize(
        ("age_days", "expected_deleted"),
        [
            (29.0, False),  # inside the window
            (30.0, False),  # exactly at the cutoff — retained, not deleted
            (30.5, True),  # past it
            (31.0, True),
        ],
    )
    def test_the_cutoff_is_inclusive_of_the_retained_side(
        self, tmp_path: Path, age_days: float, expected_deleted: bool
    ) -> None:
        # A file exactly `retention_days` old is kept: "we keep logs for 30 days"
        # must not delete one on its thirtieth day.
        write(tmp_path / "sample.log.1", age_days=age_days)

        plan = build_plan(directory=tmp_path, retention_days=30, now=NOW)

        names = {p.path.name for p in plan.delete}
        assert ("sample.log.1" in names) is expected_deleted

    def test_a_shorter_window_deletes_more(self, tmp_path: Path) -> None:
        write(tmp_path / "a.log", age_days=0)
        write(tmp_path / "a.log.1", age_days=10)

        assert build_plan(directory=tmp_path, retention_days=30, now=NOW).delete == []
        assert len(build_plan(directory=tmp_path, retention_days=7, now=NOW).delete) == 1


class TestExecution:
    def test_a_plan_deletes_exactly_what_it_listed(self, tmp_path: Path) -> None:
        write(tmp_path / "app.log", age_days=0)
        doomed = write(tmp_path / "app.log.1", age_days=99)
        spared = write(tmp_path / "keep.txt", age_days=99)

        plan = build_plan(directory=tmp_path, retention_days=30, now=NOW)
        removed = execute_plan(plan)

        assert removed == [doomed]
        assert not doomed.exists()
        assert spared.exists()
        assert (tmp_path / "app.log").exists()

    def test_building_a_plan_deletes_nothing_by_itself(self, tmp_path: Path) -> None:
        write(tmp_path / "app.log", age_days=0)
        old = write(tmp_path / "app.log.1", age_days=99)

        plan = build_plan(directory=tmp_path, retention_days=30, now=NOW)

        assert plan.delete
        assert old.exists()

    def test_it_is_idempotent(self, tmp_path: Path) -> None:
        write(tmp_path / "app.log", age_days=0)
        write(tmp_path / "app.log.1", age_days=99)

        execute_plan(build_plan(directory=tmp_path, retention_days=30, now=NOW))
        second = execute_plan(build_plan(directory=tmp_path, retention_days=30, now=NOW))

        assert second == []

    @pytest.mark.skipif(os.name == "nt", reason="Symlink creation needs privilege on Windows.")
    def test_a_symlink_pointing_outside_the_directory_is_skipped(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        target = write(outside / "precious.log", age_days=99)

        logs = tmp_path / "logs"
        logs.mkdir()
        write(logs / "app.log", age_days=0)
        (logs / "sneaky.log.1").symlink_to(target)

        plan = build_plan(directory=logs, retention_days=30, now=NOW)
        execute_plan(plan)

        assert target.exists()
        assert plan.skipped_unsafe
