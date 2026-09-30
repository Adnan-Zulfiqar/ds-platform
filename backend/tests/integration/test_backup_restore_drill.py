"""BACKUP-B1 — the restore drill, against real PostgreSQL and real `pg_dump`.

**A successful restore here proves the tooling. It does not prove that
production backups exist.** Nothing in this file touches the production
database, and the milestone that wrote it was not authorised to. What it does
prove is that a backup taken by this code, of a database at the current schema
head, containing multi-tenant relational data and encrypted credential columns,
restores into a different database byte-for-byte identical in content.

The schema is built by **running the migrations**, as every integration test in
this repository must — a drill against `create_all()` would prove the models
restore, which is not what production runs.

Two databases are created and dropped: `droppilot_b1_source` and
`droppilot_b1_restore`. Neither is `droppilot`, and the tooling refuses that
name in both directions regardless.

The negatives matter as much as the positive. A verifier that accepts
everything passes the happy path too, so this file also corrupts a copy, offers
the wrong key, and states the wrong source and target — and requires each to be
refused.
"""

from __future__ import annotations

import base64
import os
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest

from app.core.backup_crypto import load_backup_key
from app.core.config import settings
from app.services.database_backup import (
    LAST_DRILL_MARKER,
    ConfigurationError,
    PostgresTarget,
    VerificationError,
    create_backup,
    read_marker,
    restore_backup,
    verify_backup,
    write_marker,
)

pytestmark = [pytest.mark.integration]

SOURCE_DB = "droppilot_b1_source"
RESTORE_DB = "droppilot_b1_restore"

#: Tenants, and users per tenant. Small enough to be quick, large enough that a
#: restore losing one row changes a digest.
TENANTS = 4
USERS_PER_TENANT = 5

#: Stands in for a marketplace credential. The drill asserts this survives the
#: round trip **unchanged and still opaque** — a restore that decrypted it, or
#: that silently re-encoded it, would be a data-protection incident rather than
#: a bug.
CIPHERTEXT_SENTINEL = "gAAAAABn0000-not-a-real-fernet-token-but-shaped-like-one=="

#: Aggregates and digests only. A drill report that printed a restored row
#: would turn the recovery runbook into a disclosure.
INTEGRITY_QUERIES: dict[str, str] = {
    "tenants": "SELECT count(*) FROM tenants",
    "users": "SELECT count(*) FROM users",
    "stores": "SELECT count(*) FROM stores",
    "tenants_digest": (
        "SELECT encode(sha256(convert_to("
        "coalesce(string_agg(t::text, '|' ORDER BY t.id), ''), 'UTF8')), 'hex') FROM tenants t"
    ),
    "users_digest": (
        "SELECT encode(sha256(convert_to("
        "coalesce(string_agg(u::text, '|' ORDER BY u.id), ''), 'UTF8')), 'hex') FROM users u"
    ),
    "stores_digest": (
        "SELECT encode(sha256(convert_to("
        "coalesce(string_agg(s::text, '|' ORDER BY s.id), ''), 'UTF8')), 'hex') FROM stores s"
    ),
    "orphan_users": (
        "SELECT count(*) FROM users u LEFT JOIN tenants t ON t.id = u.tenant_id WHERE t.id IS NULL"
    ),
    "orphan_stores": (
        "SELECT count(*) FROM stores s LEFT JOIN tenants t ON t.id = s.tenant_id WHERE t.id IS NULL"
    ),
    "nullable_tenant_id_columns": (
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_schema = 'public' AND column_name = 'tenant_id' "
        "AND is_nullable = 'YES'"
    ),
    "foreign_keys": (
        "SELECT count(*) FROM information_schema.table_constraints "
        "WHERE table_schema = 'public' AND constraint_type = 'FOREIGN KEY'"
    ),
    "tables": (
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
    ),
    "indexes": "SELECT count(*) FROM pg_indexes WHERE schemaname = 'public'",
    "revision": "SELECT version_num FROM alembic_version",
    "credentials_still_ciphertext": (
        "SELECT count(*) FROM stores "
        "WHERE encrypted_credentials IS NOT NULL AND encrypted_credentials LIKE 'gAAAA%'"
    ),
}


def _admin_connection() -> psycopg.Connection[Any]:
    return psycopg.connect(
        host=settings.database.host,
        port=settings.database.port,
        user=settings.database.user,
        password=settings.database.password.get_secret_value(),
        dbname="postgres",
        autocommit=True,
        connect_timeout=10,
    )


def _recreate(name: str) -> None:
    """Drop and create, refusing anything that is not a drill database.

    The guard is here rather than trusted to the caller because this function
    issues `DROP DATABASE`, and a constant that gets edited during a refactor
    is exactly how one of those ends up pointed somewhere else.
    """
    if not name.startswith("droppilot_b1_"):
        raise AssertionError(f"{name!r} is not an isolated drill database")
    with _admin_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (name,),
        )
        cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
        cur.execute(f'CREATE DATABASE "{name}"')


def _drop(name: str) -> None:
    if not name.startswith("droppilot_b1_"):
        raise AssertionError(f"{name!r} is not an isolated drill database")
    with _admin_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (name,),
        )
        cur.execute(f'DROP DATABASE IF EXISTS "{name}"')


def _connect(dbname: str) -> psycopg.Connection[Any]:
    return psycopg.connect(
        host=settings.database.host,
        port=settings.database.port,
        user=settings.database.user,
        password=settings.database.password.get_secret_value(),
        dbname=dbname,
        connect_timeout=10,
    )


def _migrate(dbname: str) -> None:
    """Build the schema by running the migrations, not from the models."""
    env = dict(os.environ, POSTGRES_DB=dbname)
    backend_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(backend_root),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"alembic upgrade failed: {result.stderr[-2000:]}")


def _seed(dbname: str) -> None:
    """Representative multi-tenant data: tenants, their users, and their stores."""
    with _connect(dbname) as conn, conn.cursor() as cur:
        for index in range(TENANTS):
            tenant_id = uuid.uuid5(uuid.NAMESPACE_URL, f"b1-tenant-{index}")
            cur.execute(
                "INSERT INTO tenants (id, name, slug, status, is_active, timezone, "
                "default_currency) VALUES (%s, %s, %s, %s, true, 'UTC', 'GBP')",
                (tenant_id, f"Drill Workspace {index}", f"drill-workspace-{index}", "active"),
            )
            for member in range(USERS_PER_TENANT):
                cur.execute(
                    "INSERT INTO users (id, tenant_id, email, is_active, is_verified) "
                    "VALUES (%s, %s, %s, true, %s)",
                    (
                        uuid.uuid5(uuid.NAMESPACE_URL, f"b1-user-{index}-{member}"),
                        tenant_id,
                        f"drill-{index}-{member}@example.invalid",
                        member % 2 == 0,
                    ),
                )
            cur.execute(
                "INSERT INTO stores (id, tenant_id, name, slug, platform, status, currency, "
                "timezone, settings, inventory_sync_enabled, pricing_sync_enabled, "
                "order_sync_enabled, health_score, encrypted_credentials) "
                "VALUES (%s, %s, %s, %s, %s, %s, 'GBP', 'UTC', '{}'::jsonb, "
                "true, true, true, 100, %s)",
                (
                    uuid.uuid5(uuid.NAMESPACE_URL, f"b1-store-{index}"),
                    tenant_id,
                    f"Drill Store {index}",
                    f"drill-store-{index}",
                    "shopify",
                    "connected",
                    f"{CIPHERTEXT_SENTINEL}{index}",
                ),
            )
        conn.commit()


def _measure(dbname: str) -> dict[str, str]:
    results: dict[str, str] = {}
    with _connect(dbname) as conn:
        for name, sql in INTEGRITY_QUERIES.items():
            with conn.cursor() as cur:
                cur.execute(sql)
                row = cur.fetchone()
            results[name] = "null" if row is None or row[0] is None else str(row[0])
    return results


@pytest.fixture(scope="module")
def drill_key() -> Any:
    """A key generated for this run and never written anywhere.

    Deliberately not read from configuration: the drill must not depend on a
    production key existing, and must not create one.
    """
    return load_backup_key(base64.urlsafe_b64encode(os.urandom(32)).decode())


@pytest.fixture(scope="module")
def source_database() -> Iterator[dict[str, str]]:
    _recreate(SOURCE_DB)
    try:
        _migrate(SOURCE_DB)
        _seed(SOURCE_DB)
        yield _measure(SOURCE_DB)
    finally:
        _drop(SOURCE_DB)
        _drop(RESTORE_DB)


@pytest.fixture(scope="module")
def backup_directory(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("b1drill") / "droppilot" / "backups"
    directory.mkdir(parents=True)
    return directory


@pytest.fixture(scope="module")
def taken_backup(source_database: dict[str, str], drill_key: Any, backup_directory: Path) -> Any:
    return create_backup(
        target=PostgresTarget.from_settings(settings, dbname=SOURCE_DB),
        expected_database=SOURCE_DB,
        key=drill_key,
        directory=backup_directory,
    )


class TestTheDrill:
    def test_the_source_is_at_the_current_head_with_the_data_seeded(
        self, source_database: dict[str, str]
    ) -> None:
        assert source_database["revision"] == "0035"
        assert source_database["tenants"] == str(TENANTS)
        assert source_database["users"] == str(TENANTS * USERS_PER_TENANT)
        assert source_database["stores"] == str(TENANTS)
        assert source_database["orphan_users"] == "0"
        assert source_database["nullable_tenant_id_columns"] == "0"
        assert source_database["credentials_still_ciphertext"] == str(TENANTS)

    def test_a_real_backup_is_taken_and_records_the_right_facts(
        self, taken_backup: Any, source_database: dict[str, str]
    ) -> None:
        manifest = taken_backup.manifest
        assert manifest.database == SOURCE_DB
        assert manifest.alembic_revision == "0035"
        assert manifest.postgres_version.startswith("PostgreSQL 17")
        assert manifest.dump_format == "pg_dump/custom"
        assert manifest.plaintext_bytes > 0
        assert taken_backup.ciphertext_path.stat().st_size == manifest.ciphertext_bytes

    def test_the_backup_file_does_not_contain_the_plaintext_it_came_from(
        self, taken_backup: Any
    ) -> None:
        """The whole point, checked directly rather than assumed from the algorithm."""
        blob = taken_backup.ciphertext_path.read_bytes()
        assert b"drill-workspace-0" not in blob
        assert b"drill-0-0@example.invalid" not in blob
        assert CIPHERTEXT_SENTINEL.encode() not in blob
        assert b"PGDMP" not in blob

    def test_the_backup_verifies_against_a_real_pg_restore(
        self, taken_backup: Any, drill_key: Any
    ) -> None:
        outcome = verify_backup(taken_backup.ciphertext_path, key=drill_key)
        assert outcome.toc_entries > 20
        assert outcome.plaintext_bytes == taken_backup.manifest.plaintext_bytes

    def test_two_backups_of_the_same_database_differ_as_ciphertext(
        self, taken_backup: Any, drill_key: Any, backup_directory: Path
    ) -> None:
        """Otherwise an observer of the archive learns which nights changed nothing."""
        second = create_backup(
            target=PostgresTarget.from_settings(settings, dbname=SOURCE_DB),
            expected_database=SOURCE_DB,
            key=drill_key,
            directory=backup_directory,
        )
        assert second.ciphertext_path != taken_backup.ciphertext_path
        assert second.ciphertext_path.read_bytes() != taken_backup.ciphertext_path.read_bytes()
        second.ciphertext_path.unlink()
        second.manifest_path.unlink()

    def test_the_restore_reproduces_the_source_exactly(
        self, taken_backup: Any, drill_key: Any, source_database: dict[str, str]
    ) -> None:
        """The drill itself: a different, newly created database, compared by digest."""
        _recreate(RESTORE_DB)
        outcome = restore_backup(
            taken_backup.ciphertext_path,
            target=PostgresTarget.from_settings(settings, dbname=RESTORE_DB),
            expected_source=SOURCE_DB,
            expected_target=RESTORE_DB,
            key=drill_key,
            apply=True,
            integrity_queries=INTEGRITY_QUERIES,
        )
        assert outcome.dry_run is False
        assert outcome.target_database == RESTORE_DB
        assert outcome.alembic_revision == "0035"

        restored = _measure(RESTORE_DB)
        assert restored == source_database, "the restored database differs from the source"

        # Named individually so a failure says which property broke rather than
        # printing two large dictionaries.
        assert restored["revision"] == "0035"
        assert restored["tenants_digest"] == source_database["tenants_digest"]
        assert restored["users_digest"] == source_database["users_digest"]
        assert restored["stores_digest"] == source_database["stores_digest"]
        assert restored["tables"] == source_database["tables"]
        assert restored["indexes"] == source_database["indexes"]
        assert restored["foreign_keys"] == source_database["foreign_keys"]
        assert restored["nullable_tenant_id_columns"] == "0"
        assert restored["orphan_users"] == "0"
        assert restored["orphan_stores"] == "0"
        assert restored["credentials_still_ciphertext"] == str(TENANTS)

    def test_the_restored_credentials_are_the_same_ciphertext_not_merely_present(
        self, taken_backup: Any
    ) -> None:
        with _connect(RESTORE_DB) as conn, conn.cursor() as cur:
            cur.execute("SELECT encrypted_credentials FROM stores ORDER BY slug")
            values = [row[0] for row in cur.fetchall()]
        assert values == [f"{CIPHERTEXT_SENTINEL}{i}" for i in range(TENANTS)]
        assert all(v.startswith("gAAAA") for v in values)

    def test_a_rehearsal_against_a_populated_target_refuses_rather_than_overwriting(
        self, taken_backup: Any, drill_key: Any
    ) -> None:
        with pytest.raises(ConfigurationError, match="already holds"):
            restore_backup(
                taken_backup.ciphertext_path,
                target=PostgresTarget.from_settings(settings, dbname=RESTORE_DB),
                expected_source=SOURCE_DB,
                expected_target=RESTORE_DB,
                key=drill_key,
            )


class TestTheNegativesTheDrillMustAlsoProve:
    def test_a_corrupted_copy_is_refused(
        self, taken_backup: Any, drill_key: Any, tmp_path: Path
    ) -> None:
        copy = tmp_path / taken_backup.ciphertext_path.name
        data = bytearray(taken_backup.ciphertext_path.read_bytes())
        data[len(data) // 2] ^= 0xFF
        copy.write_bytes(bytes(data))
        (tmp_path / taken_backup.manifest_path.name).write_text(
            taken_backup.manifest_path.read_text(encoding="utf-8"), encoding="utf-8"
        )
        with pytest.raises(VerificationError):
            verify_backup(copy, key=drill_key)

    def test_a_truncated_copy_is_refused(
        self, taken_backup: Any, drill_key: Any, tmp_path: Path
    ) -> None:
        copy = tmp_path / taken_backup.ciphertext_path.name
        data = taken_backup.ciphertext_path.read_bytes()
        copy.write_bytes(data[: len(data) // 2])
        (tmp_path / taken_backup.manifest_path.name).write_text(
            taken_backup.manifest_path.read_text(encoding="utf-8"), encoding="utf-8"
        )
        with pytest.raises(VerificationError, match="truncated"):
            verify_backup(copy, key=drill_key)

    def test_the_wrong_key_cannot_open_the_backup(self, taken_backup: Any) -> None:
        other = load_backup_key(base64.urlsafe_b64encode(os.urandom(32)).decode())
        with pytest.raises(VerificationError):
            verify_backup(taken_backup.ciphertext_path, key=other)

    def test_the_wrong_source_identity_is_refused(self, taken_backup: Any, drill_key: Any) -> None:
        with pytest.raises(ConfigurationError, match="was taken from"):
            restore_backup(
                taken_backup.ciphertext_path,
                target=PostgresTarget.from_settings(settings, dbname=RESTORE_DB),
                expected_source="droppilot",
                expected_target=RESTORE_DB,
                key=drill_key,
            )

    def test_the_wrong_target_identity_is_refused(self, taken_backup: Any, drill_key: Any) -> None:
        with pytest.raises(ConfigurationError, match="was stated"):
            restore_backup(
                taken_backup.ciphertext_path,
                target=PostgresTarget.from_settings(settings, dbname=RESTORE_DB),
                expected_source=SOURCE_DB,
                expected_target="droppilot_b1_somewhere_else",
                key=drill_key,
            )

    def test_the_production_database_is_refused_before_any_connection(
        self, drill_key: Any, backup_directory: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Refused on the stated name alone — production is never even dialled.

        Asserted rather than assumed: `_connect` is replaced with something that
        fails the test if it is called at all. An earlier version of this check
        lived inside `inspect_database`, so it connected to production, asked
        it `current_database()`, and only then refused. The refusal was correct
        and the connection should never have happened.
        """

        def refuse_to_connect(target: Any) -> Any:
            raise AssertionError("a connection to production was attempted")

        monkeypatch.setattr("app.services.database_backup._connect", refuse_to_connect)
        with pytest.raises(ConfigurationError, match="Nothing was connected to"):
            create_backup(
                target=PostgresTarget.from_settings(settings, dbname="droppilot"),
                expected_database="droppilot",
                key=drill_key,
                directory=backup_directory,
                allow_production=False,
            )

    def test_the_production_database_is_refused_as_a_restore_target(
        self, taken_backup: Any, drill_key: Any
    ) -> None:
        with pytest.raises(ConfigurationError, match="protected production database"):
            restore_backup(
                taken_backup.ciphertext_path,
                target=PostgresTarget.from_settings(settings, dbname="droppilot"),
                expected_source=SOURCE_DB,
                expected_target="droppilot",
                key=drill_key,
            )


class TestTheDrillLeavesEvidenceThatCannotBeForged:
    def test_a_drill_marker_records_the_run_and_reads_back(
        self, backup_directory: Path, drill_key: Any, taken_backup: Any
    ) -> None:
        from datetime import UTC, datetime

        write_marker(
            backup_directory,
            LAST_DRILL_MARKER,
            {
                "completed_at": datetime.now(UTC).isoformat(),
                "source": SOURCE_DB,
                "target": RESTORE_DB,
                "backup_id": taken_backup.manifest.backup_id,
            },
            drill_key,
        )
        payload = read_marker(backup_directory, LAST_DRILL_MARKER, drill_key)
        assert payload is not None
        assert payload["source"] == SOURCE_DB
        assert payload["target"] == RESTORE_DB

    def test_no_temporary_plaintext_survives_the_drill(self, backup_directory: Path) -> None:
        temp_root = Path(tempfile.gettempdir())
        assert [p for p in temp_root.glob("dp-verify-*") if p.is_dir()] == []
        assert [p for p in temp_root.glob("dp-restore-*") if p.is_dir()] == []
        assert [p for p in temp_root.glob("dp-pgpass-*") if p.is_dir()] == []
        assert list(backup_directory.glob("*.part")) == []
